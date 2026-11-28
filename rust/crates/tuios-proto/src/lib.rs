//! Wire protocol between `tuiosd` (on the Olares One) and its thin clients.
//!
//! The server owns every terminal and all layout. It sends the client a cell
//! grid as [`Frame`]s: a full snapshot after connecting or resizing, then only
//! the rows that changed. The client draws cells and sends input back. Messages
//! are `postcard`-encoded, one per binary WebSocket message.

pub mod grid;

use serde::{Deserialize, Serialize};

pub use grid::{Cell, Color, Line, Run, Screen, Style};

/// Bumped on any incompatible change to the messages below.
pub const PROTO_VERSION: u16 = 1;

/// Default TCP port `tuiosd` listens on.
pub const DEFAULT_PORT: u16 = 7420;

/// Modifier bits for [`ClientMsg::Key`].
pub mod mods {
    pub const SHIFT: u8 = 1;
    pub const ALT: u8 = 2;
    pub const CTRL: u8 = 4;
    pub const META: u8 = 8;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Key {
    /// A character key pressed together with Ctrl and/or Alt. Plain typing
    /// arrives as [`ClientMsg::Text`] instead, so keyboard layouts just work.
    Char(char),
    Enter,
    Tab,
    Backspace,
    Escape,
    Up,
    Down,
    Left,
    Right,
    Home,
    End,
    PageUp,
    PageDown,
    Insert,
    Delete,
    /// F1 to F12.
    F(u8),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClientMsg {
    /// First message on every connection.
    Hello {
        proto: u16,
        /// Shared secret from the server's config. Phase 2 replaces this with
        /// a fingerprint-unlocked device key.
        token: String,
        /// Free-form device name for logs, e.g. "boox" or "laptop".
        device: String,
        cols: u16,
        rows: u16,
    },
    Key {
        key: Key,
        mods: u8,
    },
    /// Typed text (already resolved by the client's keyboard layout).
    Text(String),
    /// Clipboard paste; the server wraps it in bracketed-paste markers when
    /// the program asked for that.
    Paste(String),
    Resize {
        cols: u16,
        rows: u16,
    },
    /// Scroll the view through the scrollback, positive = up (older output).
    Scroll {
        lines: i32,
    },
    /// A tap on a cell. Forwarded as a mouse click when the program enabled
    /// mouse reporting (tmux, k9s).
    Tap {
        col: u16,
        row: u16,
    },
    /// The last frame the client painted.
    Ack {
        frame: u64,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ServerMsg {
    Welcome {
        server: String,
        session: String,
    },
    Frame(Frame),
    Bell,
    /// The session's program exited; the server starts a new one.
    Exited {
        status: String,
    },
    Error(String),
}

/// One screen update.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Frame {
    pub id: u64,
    /// True when `lines` holds every row and the client should drop its grid.
    pub full: bool,
    pub cols: u16,
    pub rows: u16,
    /// Cursor position (col, row), or None when hidden or scrolled away.
    pub cursor: Option<(u16, u16)>,
    pub lines: Vec<Line>,
}

pub fn encode<T: Serialize>(msg: &T) -> Vec<u8> {
    postcard::to_stdvec(msg).expect("protocol messages always serialize")
}

pub fn decode<'a, T: Deserialize<'a>>(bytes: &'a [u8]) -> Result<T, postcard::Error> {
    postcard::from_bytes(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_round_trip() {
        let msgs = vec![
            ClientMsg::Hello {
                proto: PROTO_VERSION,
                token: "t".into(),
                device: "boox".into(),
                cols: 80,
                rows: 24,
            },
            ClientMsg::Key {
                key: Key::Char('c'),
                mods: mods::CTRL,
            },
            ClientMsg::Text("héllo 世界".into()),
            ClientMsg::Scroll { lines: -3 },
        ];
        for m in msgs {
            let back: ClientMsg = decode(&encode(&m)).unwrap();
            assert_eq!(back, m);
        }
    }
}
