"""Small dependency-free PNG contribution grid for Discord embeds.

The embed supplies date labels, legend and accessible text. The image is a view
of retained challenge-day counts, never a separate source of history.
"""
from datetime import date
import struct
import zlib

from tracker import activity_days

BACKGROUND = (13, 17, 23)
COLORS = {
    "empty": (33, 38, 45),
    "unknown": (110, 118, 129),
    "inactive": (37, 44, 58),
    "future": (18, 22, 28),
}
GREENS = [(14, 68, 41), (0, 109, 50), (38, 166, 65), (57, 211, 83)]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)


def render_activity(member: dict, today: date) -> bytes:
    days = activity_days(member, today, weeks=12)
    columns = (len(days) + 6) // 7
    cell, gap, padding = 18, 5, 12
    width, height = columns * (cell + gap) - gap + padding * 2, 7 * (cell + gap) - gap + padding * 2
    pixels = [bytearray(BACKGROUND * width) for _ in range(height)]
    for index, day in enumerate(days):
        status, count = day["status"], day["count"]
        color = COLORS.get(status, COLORS["empty"])
        if status == "active" and count:
            color = GREENS[min(count, 4) - 1]
        x = padding + (index // 7) * (cell + gap)
        y = padding + (index % 7) * (cell + gap)
        for row in range(y, y + cell):
            pixels[row][x * 3:(x + cell) * 3] = bytes(color) * cell
    raw = b"".join(b"\0" + row for row in pixels)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))
