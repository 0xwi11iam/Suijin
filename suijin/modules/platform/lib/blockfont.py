"""blockfont — the 5-row ASCII block font for big headers.

One code-defined font (no deps, no weird glyphs — classic figlet-small
style). render() composes any string horizontally; per-glyph styles ride
a callback so callers can color each character (version digits
alternating red/blue, the v green, etc.).
"""

from __future__ import annotations

# 5 rows per glyph; glyphs are joined with one space column.
_GLYPHS: dict[str, tuple[str, str, str, str, str]] = {
    "A": ("  __ _ ", " / _` |", "| (_| |", " \\__,_|", "       "),
    "B": (" _     ", "| |__  ", "| '_ \\ ", "| |_) |", "|_.__/ "),
    "C": ("  ___ ", " / __|", "| (__ ", " \\___|", "      "),
    "D": (" _    ", "| |__ ", "| '_ \\", "| |_) |", "|_.__/"),
    "E": (" _____ ", "| ____|", "|  _|  ", "| |___ ", "|_____|"),
    "G": ("  ____ ", " / ___|", "| |  _ ", "| |_| |", " \\____|"),
    "H": (" _   _ ", "| | | |", "| |_| |", "|  _  |", "|_| |_|"),
    "I": (" ___ ", "|_ _|", " | | ", " | | ", "|___|"),
    "K": (" _  __", "| |/ /", "| ' / ", "| . \\ ", "|_|\\_\\"),
    "L": (" _     ", "| |    ", "| |    ", "| |___ ", "|_____|"),
    "M": (" __  __ ", "|  \\/  |", "| |\\/| |", "| |  | |", "|_|  |_|"),
    "N": (" _   _ ", "| \\ | |", "|  \\| |", "| |\\  |", "|_| \\_|"),
    "O": ("  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "),
    "P": (" ____  ", "|  _ \\ ", "| |_) |", "|  __/ ", "|_|    "),
    "R": (" ____  ", "|  _ \\ ", "| |_) |", "|  _ < ", "|_| \\_\\"),
    "S": (" ____  ", "/ ___| ", "\\___ \\ ", " ___) |", "|____/ "),
    "T": (" _____ ", "|_   _|", "  | |  ", "  | |  ", "  |_|  "),
    "U": (" _   _ ", "| | | |", "| | | |", "| |_| |", " \\___/ "),
    "X": ("__  __", "\\ \\/ /", " \\  / ", " /  \\ ", "/_/\\_\\"),
    "Z": (" ______", "|___  /", "  / / ", " / /__", "/_____|"),
    ".": ("   ", "   ", "   ", " _ ", "(_)"),
    "v": ("       ", " __   __", " \\ \\ / /", "  \\ V / ", "   \\_/  "),
    "0": ("  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "),
    "1": (" _ ", "/ |", "| |", "| |", "|_|"),
    "2": (" ____  ", "|___ \\ ", "  __) |", " / __/ ", "|_____|"),
    "3": (" _____ ", "|___  |", "  _| | ", " |_| | ", "|____/ "),
    "4": (" _  _   ", "| || |  ", "| || |_ ", "|__   _|", "   |_|  "),
    "5": (" ____  ", "| ___| ", "| |_   ", "|  _| | ", "|_|   | "),
    "6": ("  ____ ", " / ___|", "| | _ ", "| || |", "| || |"),
    "7": (" _____ ", "|___  |", "   / / ", "  / /  ", " /_/   "),
    "8": ("  ___  ", " ( _ ) ", " / _ \\ ", "| |_| |", " \\___/ "),
    "9": ("  ___  ", " / _ \\ ", "| |_| |", " \\__, |", "  /_/  "),
    " ": ("   ", "   ", "   ", "   ", "   "),
    "F": (" ____  ", "|  ___|", "| |_   ", "|  _|  ", "|_|    "),
    "J": ("     _ ", "    | |", " _  | |", "| |_| |", r" \___/ "),
    "V": ("__   __", "\\ \\ / /", " \\ V / ", "  / /  ", " /_/   "),
    "-": ("      ", " ____ ", "|____|", " ____ ", "|____|"),
}

MISSING = object()


def glyph_rows(ch: str) -> tuple[str, str, str, str, str] | None:
    g = _GLYPHS.get(ch, MISSING)
    return None if g is MISSING else g


def render(text: str, style_of=None) -> list:
    """Render `text` as 5 Rich Text rows. style_of(char, i) -> style str
    (optional; default None = plain). Unknown glyphs render as blank."""
    from rich.text import Text

    rows = [Text() for _ in range(5)]
    for i, ch in enumerate(text):
        g = glyph_rows(ch)
        if g is None:
            g = glyph_rows(" ")
        style = style_of(ch, i) if style_of else None
        for r in range(5):
            rows[r].append(g[r] + " ", style=style)
    return rows


def render_ansi(text: str, color_of=None) -> str:
    """Plain-ANSI variant: color_of(ch, i) -> SGR code (e.g. '31')."""
    lines = []
    for r in range(5):
        buf = []
        for i, ch in enumerate(text):
            g = glyph_rows(ch) or glyph_rows(" ")
            piece = g[r] + " "
            if color_of:
                sgr = color_of(ch, i)
                buf.append(f"\x1b[{sgr}m{piece}\x1b[0m" if sgr else piece)
            else:
                buf.append(piece)
        lines.append("".join(buf).rstrip())
    return "\n".join(lines)


def font_covers(text: str) -> bool:
    return all(ch in _GLYPHS for ch in text)
