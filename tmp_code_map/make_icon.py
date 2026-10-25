"""生成 code-map 插件图标（256x256 PNG，纯标准库，4x 超采样抗锯齿）。

用法：python tmp_code_map/make_icon.py <输出路径>
"""

import struct
import sys
import zlib
from pathlib import Path

S = 256          # 输出尺寸
SS = 4           # 超采样倍数
W = S * SS


def _rr(x, y, x0, y0, x1, y1, r):
    """点是否落在圆角矩形内。"""
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _mix(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def render():
    top = (30, 64, 175)        # #1E3A8A
    bot = (15, 23, 42)         # #0F172A
    light = (147, 197, 253)    # #93C5FD
    accent = (56, 189, 248)    # #38BDF8
    ink = (15, 23, 42)         # 柱体填充（与底色同族，压住对比）

    buf = bytearray(W * W * 4)
    for y in range(W):
        t = y / (W - 1)
        bg = _mix(top, bot, t)
        for x in range(W):
            i = (y * W + x) * 4
            if _rr(x, y, 6 * SS, 6 * SS, W - 6 * SS, W - 6 * SS, 52 * SS):
                buf[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))
            else:
                buf[i:i + 4] = b"\x00\x00\x00\x00"

    def fill(x0, y0, x1, y1, r, color, alpha=255):
        for y in range(max(0, y0), min(W, y1)):
            for x in range(max(0, x0), min(W, x1)):
                if _rr(x, y, x0, y0, x1, y1, r):
                    i = (y * W + x) * 4
                    a = buf[i + 3] / 255.0
                    na = alpha / 255.0
                    out_a = na + a * (1 - na)
                    if out_a <= 0:
                        continue
                    for c in range(3):
                        buf[i + c] = int(round(
                            (color[c] * na + buf[i + c] * a * (1 - na)) / out_a))
                    buf[i + 3] = int(round(out_a * 255))

    # 文件夹：页签 + 主体
    fill(38 * SS, 58 * SS, 132 * SS, 104 * SS, 16 * SS, light)
    fill(38 * SS, 84 * SS, 218 * SS, 200 * SS, 18 * SS, light)
    # 主体内的三根柱（代码量分布）
    for k, h in enumerate((44, 74, 104)):
        x0 = (62 + k * 50) * SS
        fill(x0, (178 - h) * SS, (x0 + 32 * SS), 178 * SS, 8 * SS, ink)
    # 页签高亮（强调色描边，呼应主题）
    fill(38 * SS, 58 * SS, 132 * SS, 66 * SS, 4 * SS, accent, alpha=170)

    # 4x 盒式下采样
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
                out[o] = r // a
                out[o + 1] = g // a
                out[o + 2] = b // a
                out[o + 3] = a // (SS * SS)
            else:
                out[o:o + 4] = b"\x00\x00\x00\x00"
    return bytes(out)


def write_png(path, raw):
    rows = b"".join(b"\x00" + raw[y * S * 4:(y + 1) * S * 4] for y in range(S))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
    return len(png)


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    size = write_png(dest, render())
    print("icon →", dest, size, "bytes")
