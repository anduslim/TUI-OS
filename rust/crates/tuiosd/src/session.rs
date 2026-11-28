//! One terminal session: a program in a PTY, emulated by `alacritty_terminal`.
//!
//! Every connected client shares the session (the laptop and the Boox see the
//! same tabs). Output is gathered for `coalesce_ms`, turned into a [`Screen`],
//! and published on a watch channel; each client diffs against what it last
//! sent, so a slow client simply skips intermediate states.

use std::io::{Read, Write};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use alacritty_terminal::event::{Event, EventListener};
use alacritty_terminal::grid::{Dimensions, Scroll};
use alacritty_terminal::index::{Column, Line as TLine};
use alacritty_terminal::term::cell::Flags;
use alacritty_terminal::term::test::TermSize;
use alacritty_terminal::term::{Config as TermConfig, TermMode};
use alacritty_terminal::vte::ansi::{self, Color as AColor, NamedColor, Processor};
use alacritty_terminal::Term;
use anyhow::{Context, Result};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use tokio::sync::{broadcast, watch, Notify};
use tuios_proto::grid::WIDE_SPACER;
use tuios_proto::{Cell, Color, Key, Screen, Style};

use crate::config::Config;
use crate::input;

/// Side events clients care about besides screen contents.
#[derive(Debug, Clone)]
pub enum SessionEvent {
    Bell,
    Exited(String),
}

/// Receives terminal events. Replies the terminal must send to the program
/// (device attributes, cursor position reports) go back into the PTY.
#[derive(Clone)]
struct Listener {
    pty_out: Arc<Mutex<Option<Box<dyn Write + Send>>>>,
    events: broadcast::Sender<SessionEvent>,
}

impl EventListener for Listener {
    fn send_event(&self, event: Event) {
        match event {
            Event::PtyWrite(text) => {
                if let Some(w) = self.pty_out.lock().unwrap().as_mut() {
                    let _ = w.write_all(text.as_bytes());
                    let _ = w.flush();
                }
            }
            Event::Bell => {
                let _ = self.events.send(SessionEvent::Bell);
            }
            _ => {}
        }
    }
}

pub struct Session {
    cfg: Config,
    term: Mutex<Term<Listener>>,
    listener: Listener,
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    size: Mutex<(u16, u16)>,
    /// Input that arrived while no program was running.
    pending: Mutex<Vec<u8>>,
    dirty: Notify,
    screen_tx: watch::Sender<Arc<Screen>>,
}

impl Session {
    pub fn start(cfg: Config, cols: u16, rows: u16) -> Result<Arc<Session>> {
        let (events, _) = broadcast::channel(64);
        let listener = Listener {
            pty_out: Arc::new(Mutex::new(None)),
            events,
        };
        let term_cfg = TermConfig {
            scrolling_history: cfg.scrollback,
            ..TermConfig::default()
        };
        let term = Term::new(
            term_cfg,
            &TermSize::new(cols as usize, rows as usize),
            listener.clone(),
        );
        let (screen_tx, _) = watch::channel(Arc::new(Screen::new(cols, rows)));
        let session = Arc::new(Session {
            cfg,
            term: Mutex::new(term),
            listener,
            master: Mutex::new(None),
            size: Mutex::new((cols, rows)),
            pending: Mutex::new(Vec::new()),
            dirty: Notify::new(),
            screen_tx,
        });

        let s = session.clone();
        std::thread::Builder::new()
            .name("pty".into())
            .spawn(move || s.run_pty_loop())?;
        let s = session.clone();
        tokio::spawn(async move { s.publish_loop().await });
        Ok(session)
    }

    pub fn subscribe(
        &self,
    ) -> (
        watch::Receiver<Arc<Screen>>,
        broadcast::Receiver<SessionEvent>,
    ) {
        (self.screen_tx.subscribe(), self.listener.events.subscribe())
    }

    /// Runs the program, restarting it when it exits.
    fn run_pty_loop(self: Arc<Self>) {
        loop {
            match self.run_once() {
                Ok(status) => {
                    log::info!("session program exited: {status}");
                    let _ = self.listener.events.send(SessionEvent::Exited(status));
                }
                Err(e) => {
                    log::error!("session program failed: {e:#}");
                    let _ = self
                        .listener
                        .events
                        .send(SessionEvent::Exited(format!("{e:#}")));
                }
            }
            std::thread::sleep(Duration::from_secs(1));
            // Fresh terminal state for the new program.
            let (cols, rows) = *self.size.lock().unwrap();
            let term_cfg = TermConfig {
                scrolling_history: self.cfg.scrollback,
                ..TermConfig::default()
            };
            *self.term.lock().unwrap() = Term::new(
                term_cfg,
                &TermSize::new(cols as usize, rows as usize),
                self.listener.clone(),
            );
            self.dirty.notify_one();
        }
    }

    fn run_once(&self) -> Result<String> {
        let (cols, rows) = *self.size.lock().unwrap();
        let pair = native_pty_system()
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .context("opening a PTY")?;
        let mut cmd = CommandBuilder::new(&self.cfg.command[0]);
        cmd.args(&self.cfg.command[1..]);
        cmd.env("TERM", &self.cfg.term);
        cmd.env("COLORTERM", "truecolor");
        cmd.env_remove("TMUX");
        match &self.cfg.cwd {
            Some(dir) => cmd.cwd(dir),
            None => {
                if let Some(home) = std::env::var_os("HOME") {
                    cmd.cwd(home);
                }
            }
        }
        let mut child = pair
            .slave
            .spawn_command(cmd)
            .with_context(|| format!("starting {:?}", self.cfg.command))?;
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader()?;
        {
            // Hand over the writer and flush input typed while starting, under
            // one lock so nothing slips in between.
            let mut out = self.listener.pty_out.lock().unwrap();
            let mut w = pair.master.take_writer()?;
            let pending = std::mem::take(&mut *self.pending.lock().unwrap());
            if !pending.is_empty() {
                let _ = w.write_all(&pending);
                let _ = w.flush();
            }
            *out = Some(w);
        }
        *self.master.lock().unwrap() = Some(pair.master);

        let mut parser: Processor = Processor::new();
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            let n = match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => n,
            };
            parser.advance(&mut *self.term.lock().unwrap(), &buf[..n]);
            self.dirty.notify_one();
        }
        *self.listener.pty_out.lock().unwrap() = None;
        *self.master.lock().unwrap() = None;
        let status = child
            .wait()
            .map(|s| format!("{s:?}"))
            .unwrap_or_else(|e| e.to_string());
        Ok(status)
    }

    /// Gathers output for `coalesce_ms`, then publishes one snapshot.
    async fn publish_loop(self: Arc<Self>) {
        let window = Duration::from_millis(self.cfg.coalesce_ms);
        loop {
            self.dirty.notified().await;
            tokio::time::sleep(window).await;
            let screen = Arc::new(snapshot(&self.term.lock().unwrap()));
            self.screen_tx.send_if_modified(|cur| {
                if **cur == *screen {
                    false
                } else {
                    *cur = screen;
                    true
                }
            });
        }
    }

    fn mode(&self) -> TermMode {
        *self.term.lock().unwrap().mode()
    }

    fn write(&self, bytes: &[u8]) {
        if bytes.is_empty() {
            return;
        }
        let mut out = self.listener.pty_out.lock().unwrap();
        match out.as_mut() {
            Some(w) => {
                let _ = w.write_all(bytes);
                let _ = w.flush();
            }
            // The program is (re)starting; keep the input for it, bounded.
            None => {
                let mut pending = self.pending.lock().unwrap();
                if pending.len() + bytes.len() <= 64 * 1024 {
                    pending.extend_from_slice(bytes);
                }
            }
        }
    }

    /// Typing jumps back to the live screen if the view was scrolled.
    fn scroll_to_bottom(&self) {
        let mut term = self.term.lock().unwrap();
        if term.grid().display_offset() != 0 {
            term.scroll_display(Scroll::Bottom);
            drop(term);
            self.dirty.notify_one();
        }
    }

    pub fn key(&self, key: Key, mods: u8) {
        self.scroll_to_bottom();
        self.write(&input::encode_key(key, mods, self.mode()));
    }

    pub fn text(&self, text: &str) {
        self.scroll_to_bottom();
        self.write(text.as_bytes());
    }

    pub fn paste(&self, text: &str) {
        self.scroll_to_bottom();
        self.write(&input::encode_paste(text, self.mode()));
    }

    pub fn tap(&self, col: u16, row: u16) {
        if let Some(bytes) = input::encode_click(col, row, self.mode()) {
            self.write(&bytes);
        }
    }

    /// Positive scrolls up into older output. Full-screen programs get wheel
    /// events (tmux with mouse on) or PageUp/PageDown instead.
    pub fn scroll(&self, lines: i32) {
        let mode = self.mode();
        if mode.contains(TermMode::ALT_SCREEN) {
            let (cols, rows) = *self.size.lock().unwrap();
            if let Some(bytes) = input::encode_wheel(lines, cols / 2, rows / 2, mode) {
                self.write(&bytes);
            } else {
                let key = if lines > 0 {
                    Key::PageUp
                } else {
                    Key::PageDown
                };
                self.write(&input::encode_key(key, 0, mode));
            }
            return;
        }
        self.term
            .lock()
            .unwrap()
            .scroll_display(Scroll::Delta(lines));
        self.dirty.notify_one();
    }

    pub fn resize(&self, cols: u16, rows: u16) {
        let (cols, rows) = (cols.clamp(2, 1000), rows.clamp(1, 500));
        {
            let mut size = self.size.lock().unwrap();
            if *size == (cols, rows) {
                return;
            }
            *size = (cols, rows);
        }
        self.term
            .lock()
            .unwrap()
            .resize(TermSize::new(cols as usize, rows as usize));
        if let Some(m) = self.master.lock().unwrap().as_ref() {
            let _ = m.resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            });
        }
        self.dirty.notify_one();
    }
}

/// The visible part of the terminal (honouring scrollback offset) as a grid.
pub fn snapshot<T: EventListener>(term: &Term<T>) -> Screen {
    let grid = term.grid();
    let cols = grid.columns() as u16;
    let rows = grid.screen_lines() as u16;
    let offset = grid.display_offset() as i32;
    let mut screen = Screen::new(cols, rows);
    for row in 0..rows {
        let line = &grid[TLine(row as i32 - offset)];
        let out = screen.row_mut(row);
        for col in 0..cols as usize {
            let cell = &line[Column(col)];
            let ch = if cell.flags.contains(Flags::WIDE_CHAR_SPACER) {
                WIDE_SPACER
            } else if cell
                .flags
                .intersects(Flags::HIDDEN | Flags::LEADING_WIDE_CHAR_SPACER)
            {
                ' '
            } else {
                cell.c
            };
            out[col] = Cell {
                ch,
                style: style_of(cell.fg, cell.bg, cell.flags),
            };
        }
    }
    let mode = term.mode();
    let point = term.renderable_content().cursor.point;
    screen.cursor = if mode.contains(TermMode::SHOW_CURSOR) && offset == 0 && point.line.0 >= 0 {
        Some((point.column.0 as u16, point.line.0 as u16))
    } else {
        None
    };
    screen
}

fn style_of(fg: AColor, bg: AColor, flags: Flags) -> Style {
    let mut f = 0;
    if flags.contains(Flags::BOLD) {
        f |= Style::BOLD;
    }
    if flags.contains(Flags::ITALIC) {
        f |= Style::ITALIC;
    }
    if flags.intersects(Flags::ALL_UNDERLINES) {
        f |= Style::UNDERLINE;
    }
    if flags.contains(Flags::INVERSE) {
        f |= Style::INVERSE;
    }
    if flags.contains(Flags::DIM) {
        f |= Style::DIM;
    }
    if flags.contains(Flags::STRIKEOUT) {
        f |= Style::STRIKE;
    }
    Style {
        fg: color_of(fg),
        bg: color_of(bg),
        flags: f,
    }
}

fn color_of(c: AColor) -> Color {
    match c {
        AColor::Spec(ansi::Rgb { r, g, b }) => Color::Rgb(r, g, b),
        AColor::Indexed(i) => Color::Indexed(i),
        AColor::Named(n) => {
            let i = n as usize;
            if i < 16 {
                Color::Indexed(i as u8)
            } else {
                match n {
                    NamedColor::DimBlack => Color::Indexed(0),
                    NamedColor::DimRed => Color::Indexed(1),
                    NamedColor::DimGreen => Color::Indexed(2),
                    NamedColor::DimYellow => Color::Indexed(3),
                    NamedColor::DimBlue => Color::Indexed(4),
                    NamedColor::DimMagenta => Color::Indexed(5),
                    NamedColor::DimCyan => Color::Indexed(6),
                    NamedColor::DimWhite => Color::Indexed(7),
                    _ => Color::Default,
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use alacritty_terminal::event::VoidListener;

    fn term_with(bytes: &[u8], cols: usize, rows: usize) -> Term<VoidListener> {
        let mut term = Term::new(
            TermConfig::default(),
            &TermSize::new(cols, rows),
            VoidListener,
        );
        let mut p: Processor = Processor::new();
        p.advance(&mut term, bytes);
        term
    }

    #[test]
    fn snapshot_reads_text_style_and_cursor() {
        let term = term_with(b"hi \x1b[1;31mred\x1b[0m\r\nnext", 20, 4);
        let s = snapshot(&term);
        assert_eq!(s.row_text(0), "hi red");
        assert_eq!(s.row_text(1), "next");
        let red = s.cell(3, 0).style;
        assert_eq!(red.fg, Color::Indexed(1));
        assert!(red.has(Style::BOLD));
        assert_eq!(s.cursor, Some((4, 1)));
    }

    #[test]
    fn dec_line_drawing_renders_box_chars() {
        // tmux draws pane borders this way; pyte showed "qqqq" in v1.
        let term = term_with(b"\x1b(0lqqk\x1b(B", 10, 2);
        assert_eq!(snapshot(&term).row_text(0), "┌──┐");
    }

    #[test]
    fn wide_chars_get_spacers() {
        let term = term_with("世x".as_bytes(), 10, 1);
        let s = snapshot(&term);
        assert_eq!(s.cell(1, 0).ch, WIDE_SPACER);
        assert_eq!(s.cell(2, 0).ch, 'x');
    }
}
