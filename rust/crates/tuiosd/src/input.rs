//! Turns client input into the bytes a terminal program expects. This lives on
//! the server because the right bytes depend on terminal modes (application
//! cursor keys, bracketed paste, mouse reporting) that only the server knows.

use alacritty_terminal::term::TermMode;
use tuios_proto::{mods, Key};

/// xterm modifier parameter: 1 + shift + 2*alt + 4*ctrl.
fn mod_param(m: u8) -> u8 {
    1 + (m & mods::SHIFT != 0) as u8
        + 2 * (m & mods::ALT != 0) as u8
        + 4 * (m & mods::CTRL != 0) as u8
}

pub fn encode_key(key: Key, m: u8, mode: TermMode) -> Vec<u8> {
    let alt = m & mods::ALT != 0;
    let ctrl = m & mods::CTRL != 0;
    let shift = m & mods::SHIFT != 0;
    let modified = m & (mods::SHIFT | mods::ALT | mods::CTRL) != 0;
    let app_cursor = mode.contains(TermMode::APP_CURSOR);

    // CSI sequences for cursor-style keys: ESC [ X, ESC O X, or ESC [ 1;m X.
    let cursor = |final_byte: u8| -> Vec<u8> {
        if modified {
            format!("\x1b[1;{}{}", mod_param(m), final_byte as char).into_bytes()
        } else if app_cursor {
            vec![0x1b, b'O', final_byte]
        } else {
            vec![0x1b, b'[', final_byte]
        }
    };
    // ESC [ n ~ keys, with an optional modifier.
    let tilde = |n: u8| -> Vec<u8> {
        if modified {
            format!("\x1b[{};{}~", n, mod_param(m)).into_bytes()
        } else {
            format!("\x1b[{}~", n).into_bytes()
        }
    };

    let mut out = match key {
        Key::Char(c) => {
            let mut bytes = Vec::new();
            if ctrl {
                if let Some(b) = ctrl_byte(c) {
                    bytes.push(b);
                } else {
                    bytes.extend(c.to_string().as_bytes());
                }
            } else {
                let c = if shift { c.to_ascii_uppercase() } else { c };
                bytes.extend(c.to_string().as_bytes());
            }
            if alt {
                bytes.insert(0, 0x1b);
            }
            return bytes;
        }
        Key::Enter => vec![b'\r'],
        Key::Tab if shift => b"\x1b[Z".to_vec(),
        Key::Tab => vec![b'\t'],
        Key::Backspace if ctrl => vec![0x08],
        Key::Backspace => vec![0x7f],
        Key::Escape => vec![0x1b],
        Key::Up => cursor(b'A'),
        Key::Down => cursor(b'B'),
        Key::Right => cursor(b'C'),
        Key::Left => cursor(b'D'),
        Key::Home => cursor(b'H'),
        Key::End => cursor(b'F'),
        Key::Insert => tilde(2),
        Key::Delete => tilde(3),
        Key::PageUp => tilde(5),
        Key::PageDown => tilde(6),
        Key::F(n @ 1..=4) => {
            let fb = b'P' + (n - 1);
            if modified {
                format!("\x1b[1;{}{}", mod_param(m), fb as char).into_bytes()
            } else {
                vec![0x1b, b'O', fb]
            }
        }
        Key::F(n) => {
            let code = match n {
                5 => 15,
                6 => 17,
                7 => 18,
                8 => 19,
                9 => 20,
                10 => 21,
                11 => 23,
                12 => 24,
                _ => return Vec::new(),
            };
            tilde(code)
        }
    };
    // Alt+Enter, Alt+Backspace etc. are sent as ESC-prefixed keys.
    if alt && matches!(key, Key::Enter | Key::Backspace | Key::Escape | Key::Tab) {
        out.insert(0, 0x1b);
    }
    out
}

fn ctrl_byte(c: char) -> Option<u8> {
    match c {
        'a'..='z' => Some(c as u8 - b'a' + 1),
        'A'..='Z' => Some(c as u8 - b'A' + 1),
        '@' | ' ' | '2' => Some(0),
        '[' | '3' => Some(0x1b),
        '\\' | '4' => Some(0x1c),
        ']' | '5' => Some(0x1d),
        '^' | '6' => Some(0x1e),
        '_' | '-' | '7' => Some(0x1f),
        '?' | '8' => Some(0x7f),
        _ => None,
    }
}

pub fn encode_paste(text: &str, mode: TermMode) -> Vec<u8> {
    // Strip ESC so pasted text can never end the paste early or inject keys.
    let clean: String = text
        .replace("\r\n", "\r")
        .replace('\n', "\r")
        .chars()
        .filter(|&c| c != '\x1b')
        .collect();
    if mode.contains(TermMode::BRACKETED_PASTE) {
        format!("\x1b[200~{clean}\x1b[201~").into_bytes()
    } else {
        clean.into_bytes()
    }
}

/// A left click (press + release) at a cell, if the program asked for mouse
/// reports. Coordinates are 0-based.
pub fn encode_click(col: u16, row: u16, mode: TermMode) -> Option<Vec<u8>> {
    if !mode.intersects(TermMode::MOUSE_MODE) {
        return None;
    }
    let (x, y) = (col as u32 + 1, row as u32 + 1);
    if mode.contains(TermMode::SGR_MOUSE) {
        return Some(format!("\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m").into_bytes());
    }
    if x > 223 || y > 223 {
        return None;
    }
    let (cx, cy) = (32 + x as u8, 32 + y as u8);
    Some(vec![
        0x1b,
        b'[',
        b'M',
        32,
        cx,
        cy,
        0x1b,
        b'[',
        b'M',
        32 + 3,
        cx,
        cy,
    ])
}

/// Mouse-wheel reports (button 64 up, 65 down), used to scroll full-screen
/// programs such as tmux with mouse mode on.
pub fn encode_wheel(lines: i32, col: u16, row: u16, mode: TermMode) -> Option<Vec<u8>> {
    if !mode.intersects(TermMode::MOUSE_MODE) {
        return None;
    }
    let button: u32 = if lines > 0 { 64 } else { 65 };
    let (x, y) = (col as u32 + 1, row as u32 + 1);
    let one = if mode.contains(TermMode::SGR_MOUSE) {
        format!("\x1b[<{button};{x};{y}M").into_bytes()
    } else {
        vec![
            0x1b,
            b'[',
            b'M',
            (32 + button) as u8,
            (32 + x.min(223)) as u8,
            (32 + y.min(223)) as u8,
        ]
    };
    Some(one.repeat(lines.unsigned_abs().min(50) as usize))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys() {
        let n = TermMode::empty();
        assert_eq!(encode_key(Key::Char('c'), mods::CTRL, n), vec![3]);
        assert_eq!(encode_key(Key::Char('x'), mods::ALT, n), b"\x1bx");
        assert_eq!(encode_key(Key::Up, 0, n), b"\x1b[A");
        assert_eq!(encode_key(Key::Up, 0, TermMode::APP_CURSOR), b"\x1bOA");
        assert_eq!(encode_key(Key::Right, mods::CTRL, n), b"\x1b[1;5C");
        assert_eq!(encode_key(Key::PageDown, 0, n), b"\x1b[6~");
        assert_eq!(encode_key(Key::Tab, mods::SHIFT, n), b"\x1b[Z");
        assert_eq!(encode_key(Key::F(1), 0, n), b"\x1bOP");
        assert_eq!(encode_key(Key::F(5), 0, n), b"\x1b[15~");
    }

    #[test]
    fn paste_is_bracketed_and_cleaned() {
        assert_eq!(
            encode_paste("a\nb\x1b", TermMode::BRACKETED_PASTE),
            b"\x1b[200~a\rb\x1b[201~"
        );
        assert_eq!(encode_paste("a\r\nb", TermMode::empty()), b"a\rb");
    }

    #[test]
    fn clicks_only_with_mouse_mode() {
        assert_eq!(encode_click(0, 0, TermMode::empty()), None);
        let sgr = TermMode::MOUSE_REPORT_CLICK | TermMode::SGR_MOUSE;
        assert_eq!(
            encode_click(4, 1, sgr).unwrap(),
            b"\x1b[<0;5;2M\x1b[<0;5;2m"
        );
    }
}
