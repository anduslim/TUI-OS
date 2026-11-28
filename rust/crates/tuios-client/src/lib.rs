//! Thin-client core shared by the Boox app and the laptop app.
//!
//! [`Client`] keeps a WebSocket to `tuiosd` open (reconnecting after sleep or
//! Wi-Fi drops), [`Model`] holds the cell grid and which rows need repainting,
//! and [`render::Renderer`] draws those rows into a pixel buffer.

pub mod render;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tuios_proto::{decode, encode, ClientMsg, Frame, Screen, ServerMsg, PROTO_VERSION};

#[derive(Debug, Clone)]
pub struct ClientConfig {
    /// e.g. `ws://100.64.0.1:7420`
    pub url: String,
    pub token: String,
    pub device: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Connected {
        server: String,
    },
    Disconnected {
        reason: String,
    },
    Frame(Frame),
    Bell,
    Exited(String),
    /// The server refused us (bad token, wrong protocol). No retry until the
    /// settings change.
    Fatal(String),
}

/// Handle to the connection thread. Cheap to clone.
#[derive(Clone)]
pub struct Client {
    tx: mpsc::UnboundedSender<ClientMsg>,
    size: Arc<Mutex<(u16, u16)>>,
}

impl Client {
    /// Starts the connection thread. `on_event` runs on that thread.
    pub fn spawn(
        cfg: ClientConfig,
        cols: u16,
        rows: u16,
        on_event: impl Fn(Event) + Send + 'static,
    ) -> Client {
        let (tx, rx) = mpsc::unbounded_channel();
        let size = Arc::new(Mutex::new((cols, rows)));
        let s = size.clone();
        std::thread::Builder::new()
            .name("tuios-net".into())
            .spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime");
                rt.block_on(run(cfg, s, rx, on_event));
            })
            .expect("spawn network thread");
        Client { tx, size }
    }

    pub fn send(&self, msg: ClientMsg) {
        if let ClientMsg::Resize { cols, rows } = msg {
            *self.size.lock().unwrap() = (cols, rows);
        }
        let _ = self.tx.send(msg);
    }
}

async fn run(
    cfg: ClientConfig,
    size: Arc<Mutex<(u16, u16)>>,
    mut rx: mpsc::UnboundedReceiver<ClientMsg>,
    on_event: impl Fn(Event),
) {
    let mut backoff = Duration::from_millis(500);
    loop {
        match session(&cfg, &size, &mut rx, &on_event).await {
            Ok(()) => return, // the app dropped every Client handle
            Err(SessionEnd::Fatal(msg)) => {
                on_event(Event::Fatal(msg));
                return;
            }
            Err(SessionEnd::Lost(reason, was_connected)) => {
                on_event(Event::Disconnected { reason });
                if was_connected {
                    backoff = Duration::from_millis(500);
                }
            }
        }
        // Input typed while offline would land in the wrong place later; drop
        // it. A pending resize is kept in `size` and sent with the next Hello.
        let wait = tokio::time::sleep(backoff);
        tokio::pin!(wait);
        loop {
            tokio::select! {
                _ = &mut wait => break,
                m = rx.recv() => if m.is_none() { return },
            }
        }
        backoff = (backoff * 2).min(Duration::from_secs(10));
    }
}

enum SessionEnd {
    Fatal(String),
    /// Reason, and whether we had got as far as a Welcome.
    Lost(String, bool),
}

async fn session(
    cfg: &ClientConfig,
    size: &Mutex<(u16, u16)>,
    rx: &mut mpsc::UnboundedReceiver<ClientMsg>,
    on_event: &impl Fn(Event),
) -> Result<(), SessionEnd> {
    let lost = |e: &dyn std::fmt::Display, c: bool| SessionEnd::Lost(e.to_string(), c);
    let connect = tokio_tungstenite::connect_async(cfg.url.as_str());
    let (mut ws, _) = tokio::time::timeout(Duration::from_secs(10), connect)
        .await
        .map_err(|_| lost(&"connection timed out", false))?
        .map_err(|e| lost(&e, false))?;
    let (cols, rows) = *size.lock().unwrap();
    let hello = ClientMsg::Hello {
        proto: PROTO_VERSION,
        token: cfg.token.clone(),
        device: cfg.device.clone(),
        cols,
        rows,
    };
    ws.send(Message::binary(encode(&hello)))
        .await
        .map_err(|e| lost(&e, false))?;

    let mut connected = false;
    let mut ping = tokio::time::interval(Duration::from_secs(15));
    loop {
        tokio::select! {
            incoming = ws.next() => {
                let bytes = match incoming {
                    Some(Ok(Message::Binary(b))) => b,
                    Some(Ok(Message::Close(_))) | None => return Err(lost(&"server closed the connection", connected)),
                    Some(Ok(_)) => continue,
                    Some(Err(e)) => return Err(lost(&e, connected)),
                };
                let msg: ServerMsg = match decode(&bytes) {
                    Ok(m) => m,
                    Err(e) => { log::warn!("undecodable server message: {e}"); continue; }
                };
                match msg {
                    ServerMsg::Welcome { server, .. } => {
                        connected = true;
                        on_event(Event::Connected { server });
                        // The size may have changed between Hello and now.
                        let (c, r) = *size.lock().unwrap();
                        if (c, r) != (cols, rows) {
                            let m = ClientMsg::Resize { cols: c, rows: r };
                            ws.send(Message::binary(encode(&m))).await.map_err(|e| lost(&e, true))?;
                        }
                    }
                    ServerMsg::Frame(f) => {
                        let id = f.id;
                        on_event(Event::Frame(f));
                        let ack = ClientMsg::Ack { frame: id };
                        ws.send(Message::binary(encode(&ack))).await.map_err(|e| lost(&e, connected))?;
                    }
                    ServerMsg::Bell => on_event(Event::Bell),
                    ServerMsg::Exited { status } => on_event(Event::Exited(status)),
                    ServerMsg::Error(e) if !connected => return Err(SessionEnd::Fatal(e)),
                    ServerMsg::Error(e) => log::warn!("server error: {e}"),
                }
            }
            out = rx.recv() => {
                let Some(m) = out else { return Ok(()) };
                ws.send(Message::binary(encode(&m))).await.map_err(|e| lost(&e, connected))?;
            }
            _ = ping.tick() => {
                ws.send(Message::Ping(Vec::new().into())).await.map_err(|e| lost(&e, connected))?;
            }
        }
    }
}

/// Screen state plus what the UI still has to paint.
#[derive(Debug, Default)]
pub struct Model {
    pub screen: Option<Screen>,
    /// Rows changed since the last paint.
    pub dirty_rows: Vec<u16>,
    /// Repaint everything (new size, banner went away, wake from sleep).
    pub full_redraw: bool,
    /// Connection banner text, None while connected.
    pub banner: Option<String>,
    pub bells: u32,
}

impl Model {
    pub fn new() -> Model {
        Model {
            banner: Some("connecting…".into()),
            full_redraw: true,
            ..Default::default()
        }
    }

    pub fn handle(&mut self, ev: Event) {
        match ev {
            Event::Frame(f) => {
                let screen = self
                    .screen
                    .get_or_insert_with(|| Screen::new(f.cols, f.rows));
                let old_cursor = screen.cursor;
                let size_changed = (screen.cols, screen.rows) != (f.cols, f.rows);
                let touched = screen.apply(&f);
                if f.full || size_changed {
                    self.full_redraw = true;
                } else {
                    self.dirty_rows.extend(touched);
                    // Repaint the rows the cursor left and entered.
                    for c in [old_cursor, screen.cursor].into_iter().flatten() {
                        self.dirty_rows.push(c.1);
                    }
                }
            }
            Event::Connected { .. } => {
                self.banner = None;
                self.full_redraw = true;
            }
            Event::Disconnected { reason } => {
                self.banner = Some(format!("offline: {reason}. Reconnecting…"));
                self.dirty_rows.push(0);
            }
            Event::Fatal(msg) => {
                self.banner = Some(format!(
                    "refused: {msg}. Check the server address and token."
                ));
                self.dirty_rows.push(0);
            }
            Event::Exited(status) => {
                log::info!("session program exited ({status}); server restarts it")
            }
            Event::Bell => self.bells += 1,
        }
    }

    /// Takes the pending rows, sorted and deduplicated. `None` = repaint all.
    pub fn take_dirty(&mut self) -> Option<Vec<u16>> {
        if std::mem::take(&mut self.full_redraw) {
            self.dirty_rows.clear();
            return None;
        }
        let mut rows = std::mem::take(&mut self.dirty_rows);
        rows.sort_unstable();
        rows.dedup();
        Some(rows)
    }

    /// Paints what changed into `target`; returns the changed rectangle.
    pub fn paint(&mut self, r: &mut render::Renderer, target: &mut render::Target) -> render::Rect {
        let dirty = self.take_dirty();
        let mut rect = render::Rect::default();
        match (&self.screen, dirty) {
            (Some(s), None) => {
                rect = r.clear(target);
                let all: Vec<u16> = (0..s.rows).collect();
                r.draw_rows(target, s, &all);
            }
            (Some(s), Some(rows)) => rect = r.draw_rows(target, s, &rows),
            (None, None) => rect = r.clear(target),
            (None, Some(_)) => {}
        }
        if let Some(text) = &self.banner {
            rect = rect.union(r.draw_banner(target, text));
        }
        rect
    }
}
