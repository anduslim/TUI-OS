//! Software renderer: draws cell rows into a 32-bit pixel buffer.
//!
//! No GPU. E-ink panels want exact control over which pixels change, so the
//! client redraws only the rows a frame touched and reports their rectangle.

use std::collections::HashMap;

use fontdue::{Font, FontSettings};
use tuios_proto::grid::WIDE_SPACER;
use tuios_proto::{Color, Screen, Style};

static REGULAR: &[u8] = include_bytes!("../../../../assets/fonts/JetBrainsMonoNL-Regular.ttf");
static BOLD: &[u8] = include_bytes!("../../../../assets/fonts/JetBrainsMonoNL-Bold.ttf");

/// How a pixel is packed into a `u32` of the target buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PixelFormat {
    /// 0x00RRGGBB (softbuffer on desktop).
    Xrgb,
    /// Bytes R, G, B, A in memory, i.e. 0xAABBGGRR on little-endian
    /// (Android `WINDOW_FORMAT_RGBA_8888`).
    Rgba,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Palette {
    /// The six E-ink tokens from TUI OS v1: black, white, green, blue, yellow,
    /// red. White text on black by default.
    Eink,
    /// Standard xterm colours, for the laptop.
    Full,
}

pub type Rgb = (u8, u8, u8);

const BLACK: Rgb = (0, 0, 0);
const WHITE: Rgb = (255, 255, 255);
const GREEN: Rgb = (0, 255, 0);
const BLUE: Rgb = (0, 0, 255);
const YELLOW: Rgb = (255, 255, 0);
const RED: Rgb = (255, 0, 0);
const EINK: [Rgb; 6] = [BLACK, WHITE, GREEN, BLUE, YELLOW, RED];

/// Pixel rectangle that changed: x, y, width, height.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Rect {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
}

impl Rect {
    pub fn union(self, o: Rect) -> Rect {
        if self.w == 0 || self.h == 0 {
            return o;
        }
        if o.w == 0 || o.h == 0 {
            return self;
        }
        let x = self.x.min(o.x);
        let y = self.y.min(o.y);
        let r = (self.x + self.w).max(o.x + o.w);
        let b = (self.y + self.h).max(o.y + o.h);
        Rect {
            x,
            y,
            w: r - x,
            h: b - y,
        }
    }
}

/// A target buffer: `pixels[y * stride + x]`.
pub struct Target<'a> {
    pub pixels: &'a mut [u32],
    pub width: u32,
    pub height: u32,
    pub stride: u32,
}

struct Glyph {
    xmin: i32,
    ymin: i32,
    width: usize,
    height: usize,
    coverage: Vec<u8>,
}

pub struct Renderer {
    fonts: [Font; 2],
    px: f32,
    pub cell_w: u32,
    pub cell_h: u32,
    ascent: i32,
    pub format: PixelFormat,
    pub palette: Palette,
    glyphs: HashMap<(char, bool), Glyph>,
}

impl Renderer {
    pub fn new(px: f32, format: PixelFormat, palette: Palette) -> Renderer {
        let load = |b: &[u8]| {
            Font::from_bytes(
                b,
                FontSettings {
                    scale: px,
                    ..FontSettings::default()
                },
            )
            .expect("bundled font")
        };
        let fonts = [load(REGULAR), load(BOLD)];
        let lm = fonts[0]
            .horizontal_line_metrics(px)
            .expect("font has line metrics");
        let cell_w = fonts[0].metrics('M', px).advance_width.round().max(1.0) as u32;
        let cell_h = (lm.ascent - lm.descent + lm.line_gap).ceil().max(1.0) as u32;
        Renderer {
            fonts,
            px,
            cell_w,
            cell_h,
            ascent: lm.ascent.round() as i32,
            format,
            palette,
            glyphs: HashMap::new(),
        }
    }

    /// Grid size that fits a surface.
    pub fn grid_size(&self, width: u32, height: u32) -> (u16, u16) {
        (
            (width / self.cell_w).max(2) as u16,
            (height / self.cell_h).max(1) as u16,
        )
    }

    fn pack(&self, c: Rgb) -> u32 {
        pack(self.format, c)
    }

    pub fn background(&self) -> Rgb {
        BLACK
    }

    /// Resolves a cell style to (foreground, background) colours.
    pub fn colors(&self, style: Style) -> (Rgb, Rgb) {
        let mut fg = match style.fg {
            Color::Default => WHITE,
            c => self.rgb(c),
        };
        let mut bg = match style.bg {
            Color::Default => BLACK,
            c => self.rgb(c),
        };
        if style.has(Style::INVERSE) {
            std::mem::swap(&mut fg, &mut bg);
        }
        if self.palette == Palette::Eink {
            fg = nearest(fg);
            bg = nearest(bg);
            // Keep every pairing readable on the panel, as v1 did.
            fg = match bg {
                BLACK if fg == BLACK || fg == BLUE => WHITE,
                BLACK => fg,
                BLUE | RED => WHITE,
                _ => BLACK,
            };
        }
        (fg, bg)
    }

    fn rgb(&self, c: Color) -> Rgb {
        match c {
            Color::Default => WHITE,
            Color::Rgb(r, g, b) => (r, g, b),
            Color::Indexed(i) if i < 16 && self.palette == Palette::Eink => {
                // ANSI colours by intent, not RGB distance (cyan is an accent).
                [
                    BLACK, RED, GREEN, YELLOW, BLUE, RED, GREEN, WHITE, WHITE, RED, GREEN, YELLOW,
                    BLUE, RED, GREEN, WHITE,
                ][i as usize]
            }
            Color::Indexed(i) => xterm256(i),
        }
    }

    fn ensure_glyph(&mut self, ch: char, bold: bool) {
        let px = self.px;
        let fonts = &self.fonts;
        self.glyphs.entry((ch, bold)).or_insert_with(|| {
            let (m, coverage) = fonts[bold as usize].rasterize(ch, px);
            Glyph {
                xmin: m.xmin,
                ymin: m.ymin,
                width: m.width,
                height: m.height,
                coverage,
            }
        });
    }

    fn fill(&self, t: &mut Target, x: u32, y: u32, w: u32, h: u32, color: Rgb) {
        let px = self.pack(color);
        let x1 = (x + w).min(t.width);
        for yy in y..(y + h).min(t.height) {
            let row = (yy * t.stride) as usize;
            t.pixels[row + x as usize..row + x1 as usize].fill(px);
        }
    }

    /// Clears the whole target to the background colour.
    pub fn clear(&self, t: &mut Target) -> Rect {
        let (w, h) = (t.width, t.height);
        self.fill(t, 0, 0, w, h, self.background());
        Rect { x: 0, y: 0, w, h }
    }

    /// Draws the listed rows of `screen`, including the cursor, and returns
    /// the pixel rectangle that changed.
    pub fn draw_rows(&mut self, t: &mut Target, screen: &Screen, rows: &[u16]) -> Rect {
        let mut dirty = Rect::default();
        for &row in rows {
            if row >= screen.rows {
                continue;
            }
            dirty = dirty.union(self.draw_row(t, screen, row));
        }
        dirty
    }

    fn draw_row(&mut self, t: &mut Target, screen: &Screen, row: u16) -> Rect {
        let y0 = row as u32 * self.cell_h;
        if y0 >= t.height {
            return Rect::default();
        }
        let cells = screen.row(row);
        for (col, cell) in cells.iter().enumerate() {
            if cell.ch == WIDE_SPACER {
                continue;
            }
            let wide = cells
                .get(col + 1)
                .map(|c| c.ch == WIDE_SPACER)
                .unwrap_or(false);
            let span = if wide { 2 } else { 1 };
            let mut style = cell.style;
            if screen.cursor == Some((col as u16, row)) {
                style.flags ^= Style::INVERSE;
            }
            self.draw_cell(t, col as u32, y0, span, cell.ch, style);
        }
        // Clear any pixels right of the last full column.
        let used = screen.cols as u32 * self.cell_w;
        if used < t.width {
            let bg = self.background();
            self.fill(t, used, y0, t.width - used, self.cell_h, bg);
        }
        Rect {
            x: 0,
            y: y0,
            w: t.width,
            h: self.cell_h.min(t.height - y0),
        }
    }

    fn draw_cell(&mut self, t: &mut Target, col: u32, y0: u32, span: u32, ch: char, style: Style) {
        let (fg, bg) = self.colors(style);
        let x0 = col * self.cell_w;
        if x0 >= t.width {
            return;
        }
        let (cw, chh) = (self.cell_w * span, self.cell_h);
        self.fill(t, x0, y0, cw, chh, bg);
        if ch != ' ' {
            let ascent = self.ascent;
            let (tw, th, stride) = (t.width as i32, t.height as i32, t.stride as usize);
            let fgp = fg;
            let bold = style.has(Style::BOLD);
            self.ensure_glyph(ch, bold);
            let format = self.format;
            let g = &self.glyphs[&(ch, bold)];
            let gx = x0 as i32 + g.xmin;
            let gy = y0 as i32 + ascent - (g.height as i32 + g.ymin);
            let (gw, gh) = (g.width, g.height);
            let coverage = &g.coverage;
            for yy in 0..gh {
                let py = gy + yy as i32;
                if py < y0 as i32 || py >= (y0 + chh) as i32 || py >= th {
                    continue;
                }
                for xx in 0..gw {
                    let px = gx + xx as i32;
                    if px < 0 || px >= tw || px >= (x0 + cw) as i32 {
                        continue;
                    }
                    let a = coverage[yy * gw + xx] as u32;
                    if a == 0 {
                        continue;
                    }
                    let mix = |f: u8, b: u8| ((f as u32 * a + b as u32 * (255 - a)) / 255) as u8;
                    let c = (mix(fgp.0, bg.0), mix(fgp.1, bg.1), mix(fgp.2, bg.2));
                    t.pixels[py as usize * stride + px as usize] = pack(format, c);
                }
            }
        }
        if style.has(Style::UNDERLINE) {
            let uy = y0 + chh.saturating_sub(2);
            self.fill(t, x0, uy, cw, 1, fg);
        }
        if style.has(Style::STRIKE) {
            self.fill(t, x0, y0 + chh / 2, cw, 1, fg);
        }
    }

    /// A one-row banner across the top (connection state). Returns its rect.
    pub fn draw_banner(&mut self, t: &mut Target, text: &str) -> Rect {
        let style = Style {
            fg: Color::Indexed(0),
            bg: Color::Indexed(3),
            flags: Style::BOLD,
        };
        let cols = t.width / self.cell_w;
        let mut chars = text.chars();
        for col in 0..cols {
            let ch = chars.next().unwrap_or(' ');
            self.draw_cell(t, col, 0, 1, ch, style);
        }
        Rect {
            x: 0,
            y: 0,
            w: t.width,
            h: self.cell_h.min(t.height),
        }
    }
}

fn pack(format: PixelFormat, (r, g, b): Rgb) -> u32 {
    match format {
        PixelFormat::Xrgb => (r as u32) << 16 | (g as u32) << 8 | b as u32,
        PixelFormat::Rgba => 0xff00_0000 | (b as u32) << 16 | (g as u32) << 8 | r as u32,
    }
}

fn nearest(c: Rgb) -> Rgb {
    let d = |t: Rgb| {
        let (dr, dg, db) = (
            c.0 as i32 - t.0 as i32,
            c.1 as i32 - t.1 as i32,
            c.2 as i32 - t.2 as i32,
        );
        2 * dr * dr + 4 * dg * dg + 3 * db * db
    };
    *EINK.iter().min_by_key(|&&t| d(t)).unwrap()
}

fn xterm256(i: u8) -> Rgb {
    const BASE: [Rgb; 16] = [
        (0, 0, 0),
        (205, 0, 0),
        (0, 205, 0),
        (205, 205, 0),
        (0, 0, 238),
        (205, 0, 205),
        (0, 205, 205),
        (229, 229, 229),
        (127, 127, 127),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (92, 92, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ];
    match i {
        0..=15 => BASE[i as usize],
        16..=231 => {
            let i = i - 16;
            let v = |n: u8| if n == 0 { 0 } else { 55 + 40 * n };
            (v(i / 36), v((i / 6) % 6), v(i % 6))
        }
        _ => {
            let g = 8 + 10 * (i - 232);
            (g, g, g)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tuios_proto::Cell;

    #[test]
    fn draws_only_the_requested_row() {
        let mut r = Renderer::new(16.0, PixelFormat::Xrgb, Palette::Eink);
        let (w, h) = (r.cell_w * 10, r.cell_h * 3);
        let mut pixels = vec![0x123456u32; (w * h) as usize];
        let mut t = Target {
            pixels: &mut pixels,
            width: w,
            height: h,
            stride: w,
        };
        let mut s = Screen::new(10, 3);
        s.row_mut(1)[0] = Cell {
            ch: 'X',
            style: Style::default(),
        };
        let rect = r.draw_rows(&mut t, &s, &[1]);
        assert_eq!(
            rect,
            Rect {
                x: 0,
                y: r.cell_h,
                w,
                h: r.cell_h
            }
        );
        // Row 0 untouched, row 1 has white ink on black.
        assert_eq!(pixels[0], 0x123456);
        let row1 = &pixels[(r.cell_h * w) as usize..(2 * r.cell_h * w) as usize];
        assert!(row1.contains(&0xffffff));
        assert!(row1.contains(&0x000000));
    }

    #[test]
    fn eink_palette_keeps_text_readable() {
        let r = Renderer::new(16.0, PixelFormat::Rgba, Palette::Eink);
        let blue_on_black = Style {
            fg: Color::Indexed(4),
            ..Default::default()
        };
        assert_eq!(r.colors(blue_on_black), (WHITE, BLACK));
        let on_yellow = Style {
            fg: Color::Indexed(7),
            bg: Color::Indexed(3),
            flags: 0,
        };
        assert_eq!(r.colors(on_yellow), (BLACK, YELLOW));
        assert_eq!(r.pack(RED), 0xff0000ff);
    }
}
