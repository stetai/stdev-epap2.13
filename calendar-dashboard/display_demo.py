
"""
E-paper calendar PREVIEW - runs on your computer, no display needed.

It renders the two channels exactly as the panel would receive them:
  layer_black.png  - black/white channel  (black = ink, white = no ink)
  layer_red.png    - red channel          (black = red ink, white = no ink)
  preview.png      - both combined, enlarged 3x, roughly how the panel looks

Run:
  uv run preview_calendar.py                      # uses ICS_SOURCES below
  uv run preview_calendar.py /path/a.ics /dir/    # files/folders on the command line
"""
import datetime
import pathlib
import sys

import icalendar
import recurring_ical_events
from PIL import Image, ImageDraw, ImageFont, ImageOps   # "PIL" = the Pillow package

# =========================================================================
# SETTINGS
# =========================================================================
WIDTH, HEIGHT = 250, 122      # landscape; matches set_rotation(270) on the Pi

# Files and/or folders to read. Absolute paths work from anywhere.
ICS_SOURCES = [
    pathlib.Path("/mnt/data/Documents/StVau_001/4 Vault/Coding/Stedule/Stedule Data Test/calendar2.ics"),
    # pathlib.Path("/mnt/data/Documents/other-calendars"),   # a whole folder
    # pathlib.Path("~/Sync/calendar"),                        # ~ = your home folder
]

HOURS = 24            # how many hours ahead to show (current hour excluded)
FONT_SIZE = 12
LINE_H = FONT_SIZE + 4         # line height in pixels - increase if lines overlap
MAX_LINES = 6       # header + 5 lines x 18 px fits into 122 px
MARGIN_X = 2
PREVIEW_SCALE = 6   # enlarge preview.png so you can see the pixels

OUTPUT_DIR = pathlib.Path(__file__).resolve().parent   # PNGs land next to this script

# In Pillow's 1-bit mode ("1"): 1 = white = no ink, 0 = ink.
NO_INK, INK = 1, 0


# =========================================================================
# CALENDAR
# =========================================================================
def collect_ics_files(sources):
    """Turn a list of files/folders into a list of .ics files."""
    files = []
    for src in sources:
        src = pathlib.Path(src).expanduser()
        if src.is_dir():
            files += sorted(src.glob("*.ics"))
        elif src.is_file():
            files.append(src)
        else:
            print(f"WARNING: not found, skipped: {src}")
    return files


def start_of(ev):
    """Event start as a timezone-aware local datetime, so all-day events
    (date) and timed events (datetime) can be sorted together."""
    s = ev["DTSTART"].dt
    if isinstance(s, datetime.datetime):
        return s.astimezone()          # naive datetimes are treated as local time
    return datetime.datetime.combine(s, datetime.time.min).astimezone()


def load_events(files, now):
    end = now + datetime.timedelta(hours=HOURS)
    events = []
    for f in files:
        try:
            cal = icalendar.Calendar.from_ical(f.read_bytes())
            events += recurring_ical_events.of(cal).between(now, end)
        except Exception as e:            # broken / half-synced file
            print(f"WARNING: could not read {f}: {e}")
    return sorted(events, key=start_of)


def event_label(ev):
    s = start_of(ev)
    timed = isinstance(ev["DTSTART"].dt, datetime.datetime)
    when = s.strftime("%H:%M") if timed else s.strftime("%a")
    return f"{when} {ev.get('SUMMARY', '(no title)')}"


# =========================================================================
# RENDERING
# =========================================================================
def load_font():
    try:
        return ImageFont.load_default(size=FONT_SIZE)   # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()                 # older Pillow: fixed size
    # Alternative: any TrueType/OpenType font file, e.g.
    # return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)


def fit(text, draw, font, max_width):
    """Shorten text with '...' until it fits into max_width pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text + "..."


def render(header, items):
    """items: list of (text, is_today). Returns (black_layer, red_layer)."""

    # ---------------------------------------------------------------------
    # BOTH channels are created fresh on EVERY run, i.e. cleared to "no ink".
    # The red channel is cleared and sent even when nothing is drawn in red:
    # the panel keeps its red memory, so old red content could otherwise
    # reappear (or the whole background could turn red).
    # ---------------------------------------------------------------------
    black = Image.new("1", (WIDTH, HEIGHT), NO_INK)
    red = Image.new("1", (WIDTH, HEIGHT), NO_INK)
    draw_black = ImageDraw.Draw(black)
    draw_red = ImageDraw.Draw(red)
    font = load_font()

    # ---------------------------------------------------------------------
    # CHOOSE THE CHANNEL FOR EACH ELEMENT
    # Default: everything black. To use red, UNCOMMENT the "= draw_red" line.
    # ---------------------------------------------------------------------
    header_layer = draw_black
    #header_layer = draw_red          # <- uncomment: header in red

    separator_layer = draw_black
    #separator_layer = draw_red       # <- uncomment: separator line in red

    today_layer = draw_black
    #today_layer = draw_red           # <- uncomment: today's events in red

    # ---- header -----------------------------------------------------------
    header_layer.text((MARGIN_X, 0), header, font=font, fill=INK)
    separator_layer.line((0, LINE_H, WIDTH - 1, LINE_H), fill=INK, width=1)

    # ---- events -----------------------------------------------------------
    max_w = WIDTH - 2 * MARGIN_X
    y = LINE_H + 3
    for text, is_today in items[:MAX_LINES]:
        layer = today_layer if is_today else draw_black
        layer.text((MARGIN_X, y), fit(text, layer, font, max_w), font=font, fill=INK)
        y += LINE_H

    return black, red


def has_ink(layer):
    """True if the 1-bit layer contains at least one ink pixel."""
    return ImageOps.invert(layer.convert("L")).getbbox() is not None


def compose_preview(black, red):
    """Combine both channels into one RGB picture. Where a pixel is set in
    both channels, red is drawn on top (assumption - check on the real panel)."""
    rgb = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    box = (0, 0, WIDTH, HEIGHT)
    rgb.paste((0, 0, 0), box, ImageOps.invert(black.convert("L")))
    rgb.paste((200, 0, 0), box, ImageOps.invert(red.convert("L")))
    return rgb.resize((WIDTH * PREVIEW_SCALE, HEIGHT * PREVIEW_SCALE),
                      Image.Resampling.NEAREST)


# =========================================================================
# MAIN
# =========================================================================
def main():
    sources = [pathlib.Path(a) for a in sys.argv[1:]] or ICS_SOURCES
    files = collect_ics_files(sources)
    print(f"Reading {len(files)} ICS file(s)")

    today = datetime.datetime.now()
    events = load_events(files, today)
    items = [(event_label(e), start_of(e).date() == today) for e in events]
    if not items:
        items = [("No events", False)]

    header = today.strftime("%A %d.%m.")
    black, red = render(header, items)

    black.save(OUTPUT_DIR / "layer_black.png")
    red.save(OUTPUT_DIR / "layer_red.png")
    compose_preview(black, red).save(OUTPUT_DIR / "preview.png")

    print(f"Events found: {len(events)} (showing up to {MAX_LINES})")
    print("Red channel:", "contains red ink" if has_ink(red)
          else "EMPTY - still cleared and would be sent as all 'no ink'")
    print(f"Saved preview.png, layer_black.png, layer_red.png in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()