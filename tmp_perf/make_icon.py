"""生成 perf-turbo 插件图标（256x256 PNG，纯标准库，4x 超采样抗锯齿）。"""

import struct
import sys
import zlib
from pathlib import Path

S = 256
SS = 4
W = S * SS

#: 闪电多边形（256 坐标系，顺时针）
BOLT = [(152, 34), (92, 142), (126, 142), (104, 222), (174, 106), (138, 106)]


def _rr(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _in_poly(x, y, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def _mix(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def render():
    top, bot = (37, 99, 235), (12, 74, 110)      # #2563EB → #0C4A6E
    bolt = (250, 250, 252)
    glow = (56, 189, 248)                        # #38BDF8

    buf = bytearray(W * W * 4)
    for y in range(W):
        bg = _mix(top, bot, y / (W - 1))
        for x in range(W):
            i = (y * W + x) * 4
            if _rr(x, y, 6 * SS, 6 * SS, W - 6 * SS, W - 6 * SS, 52 * SS):
                buf[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))
            else:
                buf[i:i + 4] = b"\x00\x00\x00\x00"

    # 外发光（放大 1.12 倍的闪电，半透明）
    cx, cy = 128 * SS, 128 * SS
    glow_pts = [(int(cx + (px * SS - cx) * 1.14), int(cy + (py * SS - cy) * 1.14))
                for px, py in BOLT]
    for y in range(W):
        for x in range(W):
            i = (y * W + x) * 4
            if buf[i + 3] == 0:
                continue
            if _in_poly(x, y, glow_pts):
                a = 90 / 255.0
                for c in range(3):
                    buf[i + c] = int(round(buf[i + c] * (1 - a) + glow[c] * a))

    # 闪电本体
    pts = [(px * SS, py * SS) for px, py in BOLT]
    for y in range(W):
        for x in range(W):
            i = (y * W + x) * 4
            if buf[i + 3] and _in_poly(x, y, pts):
                buf[i:i + 3] = bytes(bolt)

    out = bytearray(S * S * 4)
    for y in range(S):
        for x in range(S):
            r = g = b = a = 0
            for dy in range(SS):
                row = (y * SS + dy) * W
                for dx in range(SS):
                    i = (row + x * SS + dx) * 4
                    pa = buf[i + 3]
                    r += buf[i] * pa
                    g += buf[i + 1] * pa
                    b += buf[i + 2] * pa
                    a += pa
            o = (y * S + x) * 4
            if a:
                out[o], out[o + 1], out[o + 2] = r // a, g // a, b // a
                out[o + 3] = a // (SS * SS)
            else:
                out[o:o + 4] = b"\x00\x00\x00\x00"
    return bytes(out)


def write_png(path, raw):
    rows = b"".join(b"\x00" + raw[y * S * 4:(y + 1) * S * 4] for y in range(S))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
    return len(png)


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    print("icon →", dest, write_png(dest, render()), "bytes")
