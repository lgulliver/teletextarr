"""Draw teletext screens as 1280x720 images using the Bedstead font."""
import datetime as dt
import os
import time
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .maps import mosaic_bits
from .pages import COLS, ROWS, WHITE, YELLOW, BLACK, Screen

WIDTH, HEIGHT = 1280, 720
CELL_W, CELL_H = 28, 27
LEFT, TOP = (WIDTH - COLS * CELL_W) // 2, (HEIGHT - ROWS * CELL_H) // 2
DEFAULT_FONT = os.path.join(os.path.dirname(__file__), "..", "fonts", "bedstead.otf")


class Renderer:
    def __init__(self, font_path=None):
        self.font = ImageFont.truetype(font_path or os.environ.get("TELETEXTARR_FONT", DEFAULT_FONT), 120)
        self.ascent, descent = self.font.getmetrics()
        self.glyph_size = (round(self.font.getlength("M")), self.ascent + descent)
        self.glyphs = {}

    def glyph(self, ch, double):
        """Anti-aliased mask for one character, scaled to a cell (or two cells tall)."""
        key = (ch, double)
        if key not in self.glyphs:
            mask = Image.new("L", self.glyph_size)
            ImageDraw.Draw(mask).text((0, self.ascent), ch, font=self.font, fill=255, anchor="ls")
            self.glyphs[key] = mask.resize((CELL_W, CELL_H * (2 if double else 1)), Image.LANCZOS)
        return self.glyphs[key]

    def render(self, screen):
        img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        draw = ImageDraw.Draw(img)
        hidden = {r + 1 for r in screen.double}
        for r, cells in enumerate(screen.cells):
            if r in hidden:
                continue
            double = r in screen.double
            for c, (ch, fg, bg) in enumerate(cells):
                x, y = LEFT + c * CELL_W, TOP + r * CELL_H
                if bg != BLACK:
                    draw.rectangle((x, y, x + CELL_W - 1, y + CELL_H * (2 if double else 1) - 1), fill=bg)
                bits = mosaic_bits(ch)
                if bits is not None:
                    self.blocks(draw, x, y, bits, fg)
                elif ch != " ":
                    img.paste(fg, (x, y), self.glyph(ch, double))
        return img

    @staticmethod
    def blocks(draw, x, y, bits, fg):
        """Contiguous teletext mosaic: a cell split into 2 columns and 3 rows of solid blocks."""
        heights = (CELL_H // 3, CELL_H // 3, CELL_H - 2 * (CELL_H // 3))
        for i in range(6):
            if bits & (1 << i):
                bx = x + (i % 2) * (CELL_W // 2)
                by = y + sum(heights[: i // 2])
                bw = CELL_W // 2 if i % 2 == 0 else CELL_W - CELL_W // 2
                draw.rectangle((bx, by, bx + bw - 1, by + heights[i // 2] - 1), fill=fg)

    def with_header(self, img, brand, number, tz):
        """Copy of img with the live header row: page number, brand, date and a ticking clock."""
        now = dt.datetime.now(ZoneInfo(tz))
        header = Screen()
        # Same shape as the real thing: "P101 CEEFAX 1 101 Mon 26 Oct 00:13/33"; the brand gets 11 characters.
        header.put(0, 0, f"P{number} {brand[:11]} {number} {now:%a %d %b}", WHITE)
        header.put(0, 32, f"{now:%H:%M/%S}", YELLOW)
        out = img.copy()
        ImageDraw.Draw(out).rectangle((LEFT, TOP, LEFT + COLS * CELL_W, TOP + CELL_H - 1), fill=BLACK)
        for c, (ch, fg, _) in enumerate(header.cells[0]):
            if ch != " ":
                out.paste(fg, (LEFT + c * CELL_W, TOP), self.glyph(ch, False))
        return out


class Carousel:
    """Picks the subpage the shared clock is on, so every viewer of a page sees the same subpage."""

    def __init__(self, config, renderer, build):
        self.config, self.renderer, self.build = config, renderer, build
        self.key, self.img = None, None

    def frame(self, number):
        screens = self.build(number)
        seconds = self.config.index_seconds if self.config.pages[number].type == "index" else self.config.story_seconds
        sub = int(time.time() // seconds) % len(screens)
        key = (sub, repr(screens[sub].cells))
        if key != self.key:
            self.key, self.img = key, self.renderer.render(screens[sub])
        return self.renderer.with_header(self.img, self.config.brand, number, self.config.timezone)
