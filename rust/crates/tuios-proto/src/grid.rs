//! The cell grid both sides agree on, and the row diff that keeps updates small.

use serde::{Deserialize, Serialize};

use crate::Frame;

/// Placeholder char for the second cell of a wide (double-width) character.
pub const WIDE_SPACER: char = '\0';

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub enum Color {
    #[default]
    Default,
    /// ANSI 0-255.
    Indexed(u8),
    Rgb(u8, u8, u8),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub struct Style {
    pub fg: Color,
    pub bg: Color,
    pub flags: u8,
}

impl Style {
    pub const BOLD: u8 = 1;
    pub const ITALIC: u8 = 2;
    pub const UNDERLINE: u8 = 4;
    pub const INVERSE: u8 = 8;
    pub const DIM: u8 = 16;
    pub const STRIKE: u8 = 32;

    pub fn has(&self, flag: u8) -> bool {
        self.flags & flag != 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cell {
    pub ch: char,
    pub style: Style,
}

impl Default for Cell {
    fn default() -> Self {
        Cell {
            ch: ' ',
            style: Style::default(),
        }
    }
}

/// Cells of one row that share a style. `text` has exactly one char per cell
/// (wide characters are followed by [`WIDE_SPACER`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Run {
    pub col: u16,
    pub style: Style,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Line {
    pub row: u16,
    pub runs: Vec<Run>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Screen {
    pub cols: u16,
    pub rows: u16,
    pub cells: Vec<Cell>,
    pub cursor: Option<(u16, u16)>,
}

impl Screen {
    pub fn new(cols: u16, rows: u16) -> Self {
        Screen {
            cols,
            rows,
            cells: vec![Cell::default(); cols as usize * rows as usize],
            cursor: None,
        }
    }

    pub fn row(&self, row: u16) -> &[Cell] {
        let w = self.cols as usize;
        &self.cells[row as usize * w..(row as usize + 1) * w]
    }

    pub fn row_mut(&mut self, row: u16) -> &mut [Cell] {
        let w = self.cols as usize;
        &mut self.cells[row as usize * w..(row as usize + 1) * w]
    }

    pub fn cell(&self, col: u16, row: u16) -> &Cell {
        &self.cells[row as usize * self.cols as usize + col as usize]
    }

    /// Plain text of one row, trailing blanks trimmed (used by tests and copy).
    pub fn row_text(&self, row: u16) -> String {
        let s: String = self
            .row(row)
            .iter()
            .filter(|c| c.ch != WIDE_SPACER)
            .map(|c| c.ch)
            .collect();
        s.trim_end().to_string()
    }

    pub fn encode_line(&self, row: u16) -> Line {
        let mut runs: Vec<Run> = Vec::new();
        for (col, cell) in self.row(row).iter().enumerate() {
            match runs.last_mut() {
                Some(run) if run.style == cell.style => run.text.push(cell.ch),
                _ => runs.push(Run {
                    col: col as u16,
                    style: cell.style,
                    text: cell.ch.to_string(),
                }),
            }
        }
        // A trailing run of default blanks carries no information: the client
        // clears a row before applying it.
        if let Some(last) = runs.last() {
            if last.style == Style::default() && last.text.chars().all(|c| c == ' ') {
                runs.pop();
            }
        }
        Line { row, runs }
    }

    /// A frame that turns `prev` into `self`. Full when there is no previous
    /// screen or the size changed; otherwise only changed rows.
    pub fn diff(&self, prev: Option<&Screen>, id: u64) -> Frame {
        let full = !matches!(prev, Some(p) if p.cols == self.cols && p.rows == self.rows);
        let lines = (0..self.rows)
            .filter(|&r| full || prev.map(|p| p.row(r) != self.row(r)).unwrap_or(true))
            .map(|r| self.encode_line(r))
            .collect();
        Frame {
            id,
            full,
            cols: self.cols,
            rows: self.rows,
            cursor: self.cursor,
            lines,
        }
    }

    /// Applies a frame and returns the rows it touched.
    pub fn apply(&mut self, frame: &Frame) -> Vec<u16> {
        if frame.full || frame.cols != self.cols || frame.rows != self.rows {
            *self = Screen::new(frame.cols, frame.rows);
        }
        let mut touched = Vec::with_capacity(frame.lines.len());
        for line in &frame.lines {
            if line.row >= self.rows {
                continue;
            }
            let cols = self.cols as usize;
            let row = self.row_mut(line.row);
            row.fill(Cell::default());
            for run in &line.runs {
                for (i, ch) in run.text.chars().enumerate() {
                    let col = run.col as usize + i;
                    if col < cols {
                        row[col] = Cell {
                            ch,
                            style: run.style,
                        };
                    }
                }
            }
            touched.push(line.row);
        }
        self.cursor = frame.cursor;
        touched
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(s: &mut Screen, row: u16, text: &str, style: Style) {
        for (i, ch) in text.chars().enumerate() {
            s.row_mut(row)[i] = Cell { ch, style };
        }
    }

    #[test]
    fn diff_sends_only_changed_rows_and_applies_back() {
        let mut a = Screen::new(10, 4);
        write(&mut a, 0, "hello", Style::default());
        let mut b = a.clone();
        let bold = Style {
            flags: Style::BOLD,
            ..Default::default()
        };
        write(&mut b, 2, "wo", bold);
        b.cursor = Some((2, 2));

        let f = b.diff(Some(&a), 7);
        assert!(!f.full);
        assert_eq!(f.lines.len(), 1);
        assert_eq!(f.lines[0].row, 2);

        let mut client = Screen::new(1, 1);
        client.apply(&a.diff(None, 1));
        let touched = client.apply(&f);
        assert_eq!(touched, vec![2]);
        assert_eq!(client, b);
        assert_eq!(client.row_text(0), "hello");
    }

    #[test]
    fn resize_forces_a_full_frame() {
        let a = Screen::new(10, 4);
        let b = Screen::new(12, 4);
        let f = b.diff(Some(&a), 2);
        assert!(f.full);
        assert_eq!(f.lines.len(), 4);
    }

    #[test]
    fn wide_chars_keep_cell_positions() {
        let mut a = Screen::new(6, 1);
        write(&mut a, 0, "世\0x", Style::default());
        let mut c = Screen::new(6, 1);
        c.apply(&a.diff(None, 1));
        assert_eq!(c.cell(2, 0).ch, 'x');
        assert_eq!(c.row_text(0), "世x");
    }
}
