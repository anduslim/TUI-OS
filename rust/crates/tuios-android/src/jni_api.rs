//! JNI functions behind `org.olares.tuios2.Native`.

use std::sync::Mutex;

use jni::objects::{JClass, JObject, JString};
use jni::sys::{jfloat, jint};
use jni::JNIEnv;
use ndk::hardware_buffer_format::HardwareBufferFormat;
use ndk::native_window::NativeWindow;
use tuios_client::render::{Palette, PixelFormat, Renderer, Target};
use tuios_client::{Client, ClientConfig, Event, Model};
use tuios_proto::ClientMsg;

use crate::keys;

struct State {
    renderer: Renderer,
    model: Model,
    client: Client,
    window: Option<NativeWindow>,
    /// Full-frame copy of what is on screen; rows are drawn here, then only
    /// the dirty rectangle is copied into the Surface buffer.
    backing: Vec<u32>,
    width: u32,
    height: u32,
    /// Accumulated swipe distance in pixels, turned into whole lines.
    scroll_px: f32,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn with_state(f: impl FnOnce(&mut State)) {
    if let Some(st) = STATE.lock().unwrap().as_mut() {
        f(st);
    }
}

fn on_event(ev: Event) {
    with_state(|st| {
        st.model.handle(ev);
        paint(st);
    });
}

fn paint(st: &mut State) {
    let Some(window) = st.window.as_ref() else {
        return;
    };
    if st.width == 0 || st.height == 0 || st.backing.len() != (st.width * st.height) as usize {
        return;
    }
    let mut target = Target {
        pixels: &mut st.backing,
        width: st.width,
        height: st.height,
        stride: st.width,
    };
    let rect = st.model.paint(&mut st.renderer, &mut target);
    if rect.w == 0 || rect.h == 0 {
        return;
    }
    let mut dirty = ndk::native_window::Rect {
        left: rect.x as i32,
        top: rect.y as i32,
        right: (rect.x + rect.w) as i32,
        bottom: (rect.y + rect.h) as i32,
    };
    let mut guard = match window.lock(Some(&mut dirty)) {
        Ok(g) => g,
        Err(e) => {
            log::warn!("ANativeWindow_lock failed: {e}");
            return;
        }
    };
    // The system may enlarge the dirty area (e.g. after a buffer swap); copy
    // whatever it asks for from the backing frame.
    let (bw, bh) = (
        guard.width().min(st.width as usize),
        guard.height().min(st.height as usize),
    );
    let stride = guard.stride();
    let left = (dirty.left.max(0) as usize).min(bw);
    let right = (dirty.right.max(0) as usize).min(bw);
    let top = (dirty.top.max(0) as usize).min(bh);
    let bottom = (dirty.bottom.max(0) as usize).min(bh);
    let bits = guard.bits() as *mut u32;
    for y in top..bottom {
        let src = &st.backing[y * st.width as usize + left..y * st.width as usize + right];
        // SAFETY: the locked buffer holds `stride * height` 32-bit pixels and
        // `y < height`, `right <= width <= stride`.
        unsafe {
            std::ptr::copy_nonoverlapping(src.as_ptr(), bits.add(y * stride + left), right - left)
        };
    }
    drop(guard); // posts the buffer
}

fn jstr(env: &mut JNIEnv, s: &JString) -> String {
    env.get_string(s).map(Into::into).unwrap_or_default()
}

/// `start(url, token, fontPx)`: (re)connects with new settings.
#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_start(
    mut env: JNIEnv,
    _cls: JClass,
    url: JString,
    token: JString,
    font_px: jfloat,
) {
    android_logger::init_once(
        android_logger::Config::default()
            .with_tag("tuios")
            .with_max_level(log::LevelFilter::Info),
    );
    let url = jstr(&mut env, &url);
    let token = jstr(&mut env, &token);
    log::info!("connecting to {url} (font {font_px}px)");
    let mut guard = STATE.lock().unwrap();
    // Keep the surface across restarts with new settings.
    let (window, width, height) = match guard.take() {
        Some(old) => (old.window, old.width, old.height),
        None => (None, 0, 0),
    };
    let renderer = Renderer::new(font_px.max(8.0), PixelFormat::Rgba, Palette::Eink);
    let (cols, rows) = if width > 0 {
        renderer.grid_size(width, height)
    } else {
        (80, 24)
    };
    let cfg = ClientConfig {
        url,
        token,
        device: "boox".into(),
    };
    let client = Client::spawn(cfg, cols, rows, on_event);
    let mut st = State {
        renderer,
        model: Model::new(),
        client,
        window,
        backing: vec![0; (width * height) as usize],
        width,
        height,
        scroll_px: 0.0,
    };
    paint(&mut st);
    *guard = Some(st);
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_surfaceChanged(
    env: JNIEnv,
    _cls: JClass,
    surface: JObject,
    width: jint,
    height: jint,
) {
    // SAFETY: called from Java with a live JNIEnv and a valid Surface.
    let window = unsafe { NativeWindow::from_surface(env.get_raw(), surface.as_raw()) };
    with_state(|st| {
        let Some(window) = window else { return };
        let (w, h) = (width.max(1) as u32, height.max(1) as u32);
        if let Err(e) = window.set_buffers_geometry(
            w as i32,
            h as i32,
            Some(HardwareBufferFormat::R8G8B8A8_UNORM),
        ) {
            log::warn!("setBuffersGeometry failed: {e}");
        }
        st.window = Some(window);
        st.width = w;
        st.height = h;
        st.backing = vec![0; (w * h) as usize];
        st.model.full_redraw = true;
        let (cols, rows) = st.renderer.grid_size(w, h);
        st.client.send(ClientMsg::Resize { cols, rows });
        paint(st);
    });
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_surfaceDestroyed(_env: JNIEnv, _cls: JClass) {
    with_state(|st| st.window = None);
}

/// Full clean repaint, e.g. after waking from sleep.
#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_redrawAll(_env: JNIEnv, _cls: JClass) {
    with_state(|st| {
        st.model.full_redraw = true;
        paint(st);
    });
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_key(
    _env: JNIEnv,
    _cls: JClass,
    id: jint,
    meta: jint,
) {
    with_state(|st| {
        if let Some(m) = keys::special(id, meta) {
            st.client.send(m);
        }
    });
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_chord(
    _env: JNIEnv,
    _cls: JClass,
    codepoint: jint,
    meta: jint,
) {
    with_state(|st| {
        if let Some(m) = keys::chord(codepoint, meta) {
            st.client.send(m);
        }
    });
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_text(
    mut env: JNIEnv,
    _cls: JClass,
    text: JString,
) {
    let text = jstr(&mut env, &text);
    if !text.is_empty() {
        with_state(|st| st.client.send(ClientMsg::Text(text)));
    }
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_paste(
    mut env: JNIEnv,
    _cls: JClass,
    text: JString,
) {
    let text = jstr(&mut env, &text);
    if !text.is_empty() {
        with_state(|st| st.client.send(ClientMsg::Paste(text)));
    }
}

#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_tap(
    _env: JNIEnv,
    _cls: JClass,
    x: jfloat,
    y: jfloat,
) {
    with_state(|st| {
        let col = (x.max(0.0) / st.renderer.cell_w as f32) as u16;
        let row = (y.max(0.0) / st.renderer.cell_h as f32) as u16;
        st.client.send(ClientMsg::Tap { col, row });
    });
}

/// Finger moved by `dy` pixels (positive = finger moved down = show older
/// output, like scrolling a page).
#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_swipe(_env: JNIEnv, _cls: JClass, dy: jfloat) {
    with_state(|st| {
        st.scroll_px += dy;
        let h = st.renderer.cell_h as f32;
        let lines = (st.scroll_px / h).trunc();
        if lines != 0.0 {
            st.scroll_px -= lines * h;
            st.client.send(ClientMsg::Scroll {
                lines: lines as i32,
            });
        }
    });
}

/// Finger lifted: forget any partial line of swipe.
#[no_mangle]
pub extern "system" fn Java_org_olares_tuios2_Native_swipeEnd(_env: JNIEnv, _cls: JClass) {
    with_state(|st| st.scroll_px = 0.0);
}
