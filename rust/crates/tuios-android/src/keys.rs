//! Key ids passed from Kotlin. Kotlin maps Android `KeyEvent` codes to these
//! small ids so the JNI surface stays one `int` wide and testable on the host.

use tuios_proto::{mods, ClientMsg, Key};

pub const ENTER: i32 = 1;
pub const TAB: i32 = 2;
pub const BACKSPACE: i32 = 3;
pub const ESCAPE: i32 = 4;
pub const UP: i32 = 5;
pub const DOWN: i32 = 6;
pub const LEFT: i32 = 7;
pub const RIGHT: i32 = 8;
pub const HOME: i32 = 9;
pub const END: i32 = 10;
pub const PAGE_UP: i32 = 11;
pub const PAGE_DOWN: i32 = 12;
pub const INSERT: i32 = 13;
pub const DELETE: i32 = 14;
/// F1 is `F_BASE + 1`, ..., F12 is `F_BASE + 12`.
pub const F_BASE: i32 = 100;

/// Android `KeyEvent` meta-state bits we care about.
pub const META_SHIFT_ON: i32 = 0x1;
pub const META_ALT_ON: i32 = 0x2;
pub const META_CTRL_ON: i32 = 0x1000;
pub const META_META_ON: i32 = 0x10000;

pub fn mods_from_meta(meta: i32) -> u8 {
    let mut m = 0;
    if meta & META_SHIFT_ON != 0 {
        m |= mods::SHIFT;
    }
    if meta & META_ALT_ON != 0 {
        m |= mods::ALT;
    }
    if meta & META_CTRL_ON != 0 {
        m |= mods::CTRL;
    }
    if meta & META_META_ON != 0 {
        m |= mods::META;
    }
    m
}

/// A special (non-text) key.
pub fn special(id: i32, meta: i32) -> Option<ClientMsg> {
    let key = match id {
        ENTER => Key::Enter,
        TAB => Key::Tab,
        BACKSPACE => Key::Backspace,
        ESCAPE => Key::Escape,
        UP => Key::Up,
        DOWN => Key::Down,
        LEFT => Key::Left,
        RIGHT => Key::Right,
        HOME => Key::Home,
        END => Key::End,
        PAGE_UP => Key::PageUp,
        PAGE_DOWN => Key::PageDown,
        INSERT => Key::Insert,
        DELETE => Key::Delete,
        n if (F_BASE + 1..=F_BASE + 12).contains(&n) => Key::F((n - F_BASE) as u8),
        _ => return None,
    };
    Some(ClientMsg::Key {
        key,
        mods: mods_from_meta(meta),
    })
}

/// A character typed with Ctrl and/or Alt held (plain typing goes as text).
/// `codepoint` is the unshifted character.
pub fn chord(codepoint: i32, meta: i32) -> Option<ClientMsg> {
    let c = char::from_u32(codepoint as u32)?;
    Some(ClientMsg::Key {
        key: Key::Char(c),
        mods: mods_from_meta(meta),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_specials_and_chords() {
        assert_eq!(
            special(UP, 0),
            Some(ClientMsg::Key {
                key: Key::Up,
                mods: 0
            })
        );
        assert_eq!(
            special(F_BASE + 5, 0),
            Some(ClientMsg::Key {
                key: Key::F(5),
                mods: 0
            })
        );
        assert_eq!(special(999, 0), None);
        assert_eq!(
            chord('c' as i32, META_CTRL_ON),
            Some(ClientMsg::Key {
                key: Key::Char('c'),
                mods: mods::CTRL
            })
        );
        assert_eq!(
            mods_from_meta(META_SHIFT_ON | META_ALT_ON),
            mods::SHIFT | mods::ALT
        );
    }
}
