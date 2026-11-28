//! `tuios`: the laptop client. Same protocol and renderer as the Boox app,
//! drawn into a desktop window.
//!
//! ```text
//! TUIOS_TOKEN=... tuios --url ws://100.64.0.1:7420 [--font-size 18] [--eink]
//! ```

use std::num::NonZeroU32;
use std::rc::Rc;
use std::sync::{Arc, Mutex};

use anyhow::{bail, Context, Result};
use tuios_client::render::{Palette, PixelFormat, Renderer, Target};
use tuios_client::{Client, ClientConfig, Model};
use tuios_proto::{mods, ClientMsg, Key};
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, MouseButton, MouseScrollDelta, WindowEvent};
use winit::event_loop::{ActiveEventLoop, EventLoop, EventLoopProxy};
use winit::keyboard::{Key as WKey, ModifiersState, NamedKey};
use winit::window::{Window, WindowId};

struct Args {
    url: String,
    token: String,
    font_px: f32,
    palette: Palette,
}

fn parse_args() -> Result<Args> {
    let mut url = std::env::var("TUIOS_URL").unwrap_or_default();
    let mut token = std::env::var("TUIOS_TOKEN").unwrap_or_default();
    let mut font_px = 18.0;
    let mut palette = Palette::Full;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--url" => url = args.next().context("--url needs a value")?,
            "--token" => token = args.next().context("--token needs a value")?,
            "--font-size" => font_px = args.next().context("--font-size needs a value")?.parse()?,
            "--eink" => palette = Palette::Eink,
            "-h" | "--help" => {
                println!(
                    "usage: TUIOS_TOKEN=... tuios --url ws://HOST:7420 [--font-size PX] [--eink]"
                );
                std::process::exit(0);
            }
            other => bail!("unknown argument {other:?} (try --help)"),
        }
    }
    if url.is_empty() || token.is_empty() {
        bail!("set --url (or TUIOS_URL) and TUIOS_TOKEN");
    }
    Ok(Args {
        url,
        token,
        font_px,
        palette,
    })
}

struct Gfx {
    window: Rc<Window>,
    surface: softbuffer::Surface<Rc<Window>, Rc<Window>>,
    /// Our own copy of the pixels, updated row by row, then presented.
    pixels: Vec<u32>,
    width: u32,
    height: u32,
}

struct App {
    args: Args,
    renderer: Renderer,
    model: Arc<Mutex<Model>>,
    proxy: EventLoopProxy<()>,
    client: Option<Client>,
    gfx: Option<Gfx>,
    mods: ModifiersState,
    cursor_px: (f64, f64),
}

impl App {
    fn send(&self, m: ClientMsg) {
        if let Some(c) = &self.client {
            c.send(m);
        }
    }

    fn resized(&mut self, width: u32, height: u32) {
        let Some(g) = self.gfx.as_mut() else { return };
        let (w, h) = (width.max(1), height.max(1));
        g.width = w;
        g.height = h;
        g.pixels = vec![0; (w * h) as usize];
        let _ = g
            .surface
            .resize(NonZeroU32::new(w).unwrap(), NonZeroU32::new(h).unwrap());
        g.window.request_redraw();
        self.model.lock().unwrap().full_redraw = true;
        let (cols, rows) = self.renderer.grid_size(w, h);
        self.send(ClientMsg::Resize { cols, rows });
    }

    fn redraw(&mut self) {
        let Some(g) = self.gfx.as_mut() else { return };
        let mut target = Target {
            pixels: &mut g.pixels,
            width: g.width,
            height: g.height,
            stride: g.width,
        };
        self.model
            .lock()
            .unwrap()
            .paint(&mut self.renderer, &mut target);
        if let Ok(mut buf) = g.surface.buffer_mut() {
            if buf.len() == g.pixels.len() {
                buf.copy_from_slice(&g.pixels);
                let _ = buf.present();
            }
        }
    }

    fn key(&mut self, event: winit::event::KeyEvent) {
        if event.state != ElementState::Pressed {
            return;
        }
        let m = self.mods;
        let mut bits = 0;
        if m.shift_key() {
            bits |= mods::SHIFT;
        }
        if m.alt_key() {
            bits |= mods::ALT;
        }
        if m.control_key() {
            bits |= mods::CTRL;
        }
        let named = |k: Key| ClientMsg::Key { key: k, mods: bits };
        let msg = match &event.logical_key {
            WKey::Named(n) => match n {
                NamedKey::Enter => named(Key::Enter),
                NamedKey::Tab => named(Key::Tab),
                NamedKey::Backspace => named(Key::Backspace),
                NamedKey::Escape => named(Key::Escape),
                NamedKey::ArrowUp => named(Key::Up),
                NamedKey::ArrowDown => named(Key::Down),
                NamedKey::ArrowLeft => named(Key::Left),
                NamedKey::ArrowRight => named(Key::Right),
                NamedKey::Home => named(Key::Home),
                NamedKey::End => named(Key::End),
                NamedKey::PageUp if m.shift_key() => ClientMsg::Scroll { lines: 10 },
                NamedKey::PageDown if m.shift_key() => ClientMsg::Scroll { lines: -10 },
                NamedKey::PageUp => named(Key::PageUp),
                NamedKey::PageDown => named(Key::PageDown),
                NamedKey::Insert => named(Key::Insert),
                NamedKey::Delete => named(Key::Delete),
                NamedKey::Space if bits & (mods::CTRL | mods::ALT) != 0 => named(Key::Char(' ')),
                NamedKey::Space => ClientMsg::Text(" ".into()),
                NamedKey::F1 => named(Key::F(1)),
                NamedKey::F2 => named(Key::F(2)),
                NamedKey::F3 => named(Key::F(3)),
                NamedKey::F4 => named(Key::F(4)),
                NamedKey::F5 => named(Key::F(5)),
                NamedKey::F6 => named(Key::F(6)),
                NamedKey::F7 => named(Key::F(7)),
                NamedKey::F8 => named(Key::F(8)),
                NamedKey::F9 => named(Key::F(9)),
                NamedKey::F10 => named(Key::F(10)),
                NamedKey::F11 => named(Key::F(11)),
                NamedKey::F12 => named(Key::F(12)),
                _ => return,
            },
            WKey::Character(s) if bits & (mods::CTRL | mods::ALT) != 0 => {
                let Some(c) = s.chars().next() else { return };
                // Shift is already folded into the character.
                ClientMsg::Key {
                    key: Key::Char(c),
                    mods: bits & !mods::SHIFT,
                }
            }
            _ => match event.text {
                Some(t) if !t.is_empty() => ClientMsg::Text(t.to_string()),
                _ => return,
            },
        };
        self.send(msg);
    }
}

impl ApplicationHandler<()> for App {
    fn resumed(&mut self, el: &ActiveEventLoop) {
        if self.gfx.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title("TUI OS")
            .with_inner_size(LogicalSize::new(
                self.renderer.cell_w * 100,
                self.renderer.cell_h * 32,
            ));
        let window = Rc::new(el.create_window(attrs).expect("create window"));
        let context = softbuffer::Context::new(window.clone()).expect("softbuffer context");
        let surface =
            softbuffer::Surface::new(&context, window.clone()).expect("softbuffer surface");
        let size = window.inner_size();
        self.gfx = Some(Gfx {
            window,
            surface,
            pixels: Vec::new(),
            width: 0,
            height: 0,
        });

        let (cols, rows) = self
            .renderer
            .grid_size(size.width.max(1), size.height.max(1));
        let model = self.model.clone();
        let proxy = self.proxy.clone();
        let cfg = ClientConfig {
            url: self.args.url.clone(),
            token: self.args.token.clone(),
            device: "laptop".into(),
        };
        self.client = Some(Client::spawn(cfg, cols, rows, move |ev| {
            model.lock().unwrap().handle(ev);
            let _ = proxy.send_event(());
        }));
        self.resized(size.width, size.height);
    }

    fn user_event(&mut self, _el: &ActiveEventLoop, _: ()) {
        if let Some(g) = &self.gfx {
            g.window.request_redraw();
        }
    }

    fn window_event(&mut self, el: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => el.exit(),
            WindowEvent::Resized(s) => self.resized(s.width, s.height),
            WindowEvent::RedrawRequested => self.redraw(),
            WindowEvent::ModifiersChanged(m) => self.mods = m.state(),
            WindowEvent::KeyboardInput { event, .. } => self.key(event),
            WindowEvent::CursorMoved { position, .. } => self.cursor_px = (position.x, position.y),
            WindowEvent::MouseInput {
                state: ElementState::Pressed,
                button: MouseButton::Left,
                ..
            } => {
                let col = (self.cursor_px.0 / self.renderer.cell_w as f64) as u16;
                let row = (self.cursor_px.1 / self.renderer.cell_h as f64) as u16;
                self.send(ClientMsg::Tap { col, row });
            }
            WindowEvent::MouseWheel { delta, .. } => {
                let lines = match delta {
                    MouseScrollDelta::LineDelta(_, y) => (y * 3.0).round() as i32,
                    MouseScrollDelta::PixelDelta(p) => {
                        (p.y / self.renderer.cell_h as f64).round() as i32
                    }
                };
                if lines != 0 {
                    self.send(ClientMsg::Scroll { lines });
                }
            }
            _ => {}
        }
    }
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    let args = parse_args()?;
    let event_loop = EventLoop::<()>::with_user_event().build()?;
    let renderer = Renderer::new(args.font_px, PixelFormat::Xrgb, args.palette);
    let mut app = App {
        args,
        renderer,
        model: Arc::new(Mutex::new(Model::new())),
        proxy: event_loop.create_proxy(),
        client: None,
        gfx: None,
        mods: ModifiersState::empty(),
        cursor_px: (0.0, 0.0),
    };
    event_loop.run_app(&mut app)?;
    Ok(())
}
