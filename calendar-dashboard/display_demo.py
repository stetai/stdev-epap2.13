"""
E-paper calendar PREVIEW - runs on your computer, no display needed.

It renders the two channels exactly as the panel would receive them:
  layer_black.png  - black/white channel  (black = ink, white = no ink)
  layer_red.png    - red channel          (black = red ink, white = no ink)
  preview.png      - both combined, enlarged, roughly how the panel looks

Run:
  uv run display_demo.py                      # uses ICS_SOURCES below
  uv run display_demo.py /path/a.ics /dir/    # files/folders on the command line
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
    pathlib.Path("/mnt/data/Documents/StVau_001/4 Vault/Coding/Stedule/Stedule Data/main.ics"),
    # pathlib.Path("/mnt/data/Documents/other-calendars"),   # a whole folder
    # pathlib.Path("~/Sync/calendar"),                        # ~ = your home folder
]

HOURS = 24          # how many hours ahead to show regular events
HOURS_IMPORTANT = HOURS  # how many hours ahead to show 'important' events
HOURS_PB = 6        #full progress bar span
FONT_SIZE = 16
LINE_H = 20         # line height in pixels - increase if lines overlap
MAX_LINES = 5       # header + 5 lines x 20 px fits into 122 px
MARGIN_X = 2
PREVIEW_SCALE = 4   # enlarge preview.png so you can see the pixels
PB_WIDTH = 80       # width of the progress bar, best if HOURS_PB * n + 2 for some integer n

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


def is_important(ev):
    c = ev.get('CATEGORIES')
    try:
        important = 'important' in c
    except TypeError:
        important = c == 'important'
    return important


def is_visible(ev, now):
    """Events that are already running (start <= now) always pass, because
    between() only returns events that are not over yet. Others must start
    within HOURS, or within HOURS_IMPORTANT if they are important."""
    s = start_of(ev)
    if s <= now + datetime.timedelta(hours=HOURS):
        return True
    return is_important(ev) and s <= now + datetime.timedelta(hours=HOURS_IMPORTANT)


def select_events(events, now):
    """Pick at most MAX_LINES events (input must be sorted by start).
    Running and important events are never dropped in favour of regular
    upcoming ones; the remaining lines are filled with the earliest regular
    events. The result stays in chronological order, so an important event
    sits between regular events if some start after it, and at the bottom
    otherwise."""
    must = [start_of(e) <= now or is_important(e) for e in events]
    free = MAX_LINES - sum(must)
    chosen = []
    for ev, m in zip(events, must):
        if m:
            chosen.append(ev)
        elif free > 0:
            chosen.append(ev)
            free -= 1
    return chosen


def load_events(files, now):
    end = now + datetime.timedelta(hours=max(HOURS, HOURS_IMPORTANT))
    events = []
    for f in files:
        try:
            cal = icalendar.Calendar.from_ical(f.read_bytes())
            events += recurring_ical_events.of(cal).between(now, end)
        except Exception as e:            # broken / half-synced file
            print(f"WARNING: could not read {f}: {e}")
    events = sorted(events, key=start_of)
    events = [ev for ev in events if is_visible(ev, now)]
    return select_events(events, now)


def is_all_day(ev):
    return not isinstance(ev["DTSTART"].dt, datetime.datetime)


def label_time(ev, now):
    s = start_of(ev)
    if not is_all_day(ev):
        return s.strftime("%H%M")
    return "tday" if s.date() == now.date() else "tmrw"


def label_title(ev):
    title = str(ev.get("SUMMARY", "(no title)"))
    if title == '🍽': title = "Meal"
    if title == '💤': title = "Sleep"
    if title == '🚿': title = "Shower"
    if title == '🚊': title = "To Hamm/DoDorm"
    return title

def progress_fraction(ev, now):
    """0.0 = event is HOURS or more away, 1.0 = event has started.
    The bar therefore fills up (left to right) as the event approaches."""
    remaining = (start_of(ev) - now).total_seconds()
    frac = 1 - remaining / (HOURS_PB * 3600)
    return max(0.0, min(1.0, frac))


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


def draw_progress_bar(layer, x, y, fraction, label, font):
    """Outlined box with the time label inside. The box fills from right to left; 
    where it is filled, the label is shown inverted (knocked out).

    layer: (image, draw) pair of the channel to draw on."""
    img, draw = layer
    top, bottom = y + 1, y + FONT_SIZE + 1
    right = x + PB_WIDTH - 1

    draw.rectangle((x, top, right, bottom), outline=INK, width=1)
    inner_w = PB_WIDTH - 2  
    for offset in range(inner_w//HOURS_PB, inner_w, inner_w//HOURS_PB):
        draw.point((x + 1 + offset, top), fill=NO_INK)
    draw.text((x + 40, y), label, font=font, fill=INK)
                    # space inside the outline
    fill_w = round(fraction * inner_w)
    if fill_w > 0:
        #box = (x + 1, top + 1, x + 1 + fill_w, bottom)   # fill from left to right
        box = (right - fill_w, top + 1, right, bottom)   # fill from right to left
        region = img.crop(box).convert("L")
        img.paste(ImageOps.invert(region).convert("1"), box)

def render_logo(layer, x, y):
    img, draw = layer

    stetai_logo_coding = [
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 1, 0, 0, 0, 0, 1, 1, 1],
        [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 1, 0, 0, 1, 1],
        [1, 1, 1, 0, 1, 1, 0, 1, 1, 1],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
    ]

    stetai_logo_arts = [
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 1, 0, 0, 0, 0, 1, 1, 1],
        [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 1, 0, 0, 1, 1],
        [1, 1, 1, 0, 1, 1, 0, 1, 1, 1],
        [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
    ]

    logo = stetai_logo_coding

    for row, line in enumerate(logo):
        for col, value in enumerate(line):
            if value:
                draw.point((x + col, y + row), fill=INK)


def render(header, events, now):
    """Returns (black_layer, red_layer) as 1-bit images."""

    # ---------------------------------------------------------------------
    # BOTH channels are created fresh on EVERY run, i.e. cleared to "no ink".
    # The red channel is cleared and sent even when nothing is drawn in red:
    # the panel keeps its red memory, so old red content could otherwise
    # reappear (or the whole background could turn red).
    # ---------------------------------------------------------------------
    black = Image.new("1", (WIDTH, HEIGHT), NO_INK)
    red = Image.new("1", (WIDTH, HEIGHT), NO_INK)
    black_l = (black, ImageDraw.Draw(black))
    red_l = (red, ImageDraw.Draw(red))
    font = load_font()

    # ---------------------------------------------------------------------
    # CHOOSE THE CHANNEL FOR EACH ELEMENT
    # Default: everything black. To use red, UNCOMMENT the "= red_l" line.
    # ---------------------------------------------------------------------
    header_layer = black_l
    #header_layer = red_l             # <- uncomment: header in red

    separator_layer = black_l
    #separator_layer = red_l          # <- uncomment: separator line in red

    today_layer = black_l
    #today_layer = red_l              # <- today's events in red

    important_layer = red_l         # <- background of important events

    # ---- header -----------------------------------------------------------
    header_layer[1].text((MARGIN_X, 0), header, font=font, fill=INK)
    separator_layer[1].line((0, LINE_H, WIDTH - 1, LINE_H), fill=INK, width=1)

    # ---- events -----------------------------------------------------------
    max_w = WIDTH - 2 * MARGIN_X - (PB_WIDTH + 2)
    y = LINE_H + 2

    if not events:
        black_l[1].text((MARGIN_X, y), "No events", font=font, fill=INK)

    for ev in events[:MAX_LINES]:
        title_x = MARGIN_X + PB_WIDTH + 2
        is_today = start_of(ev).date() == now.date()
        layer = today_layer if is_today else black_l
        layer = important_layer if is_important(ev) else black_l
        draw = layer[1]

        # progress bar with time label (fills right to left)
        draw_progress_bar(layer, MARGIN_X, y,
                          progress_fraction(ev, now), label_time(ev, now), font)

        # title
        draw.text((title_x, y),
                  fit(label_title(ev), draw, font, max_w), font=font, fill=INK)

        # determine whether event is important
        if is_important(ev):
            layer = important_layer 
            img, draw = layer

            top, bottom = y + 1, y + FONT_SIZE + 2
            box = (title_x, top, title_x + max_w, bottom)
            region = img.crop(box).convert("L")
            img.paste(ImageOps.invert(region).convert("1"), box)
        
        y += LINE_H

    render_logo(red_l, WIDTH - 12 - MARGIN_X, 4) # 18 here is the width of the logo defined in the matrix

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

    now = datetime.datetime.now().astimezone()      # timezone-aware local time
    #now = datetime.datetime.fromisoformat("2026-10-12T17:00:00.000Z")      # debug
    events = load_events(files, now)

    header = now.strftime("%y%m%d %A")
    black, red = render(header, events, now)

    black.save(OUTPUT_DIR / "layer_black.png")
    red.save(OUTPUT_DIR / "layer_red.png")
    pv = compose_preview(black, red)
    pv.save(OUTPUT_DIR / "preview1.png")
    pv.save(OUTPUT_DIR / "preview2.png")

    print(f"Events found: {len(events)} (showing up to {MAX_LINES})")
    print("Red channel:", "contains red ink" if has_ink(red)
          else "EMPTY - still cleared and would be sent as all 'no ink'")
    print(f"Saved preview.png, layer_black.png, layer_red.png in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()