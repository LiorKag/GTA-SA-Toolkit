# GTA SA Toolkit - fast (numpy) DXT / BGRA decoders for RenderWare native textures (formats/txd.py).
# Vectorised: ~100x faster than decoding pixel by pixel, the same output (checked against DragonFF's
# decoders by the project's tests).
import numpy as np


def _blocks(data, nbytes, bw, bh):
    need = bw * bh * nbytes
    buf = np.frombuffer(bytes(data[:need]), dtype=np.uint8)
    if buf.size < need:
        buf = np.concatenate([buf, np.zeros(need - buf.size, np.uint8)])
    return buf.reshape(bw * bh, nbytes)


def _color_block(b, alpha_mode3):
    """b: (n, 8) uint8 colour blocks -> (n, 16, 4) int32 rgba (alpha filled for BC1 rules)."""
    c0 = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
    c1 = b[:, 2].astype(np.int32) | (b[:, 3].astype(np.int32) << 8)
    bits = (b[:, 4].astype(np.uint32) | (b[:, 5].astype(np.uint32) << 8) |
            (b[:, 6].astype(np.uint32) << 16) | (b[:, 7].astype(np.uint32) << 24))

    def rgb(c):
        return np.stack((((c >> 11) & 0x1f) * 0xff // 0x1f,
                         ((c >> 5) & 0x3f) * 0xff // 0x3f,
                         (c & 0x1f) * 0xff // 0x1f), axis=1)

    p0, p1 = rgb(c0), rgb(c1)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, 0)
    n = b.shape[0]
    pal = np.zeros((n, 4, 4), np.int32)
    pal[:, 0, :3], pal[:, 1, :3], pal[:, 2, :3], pal[:, 3, :3] = p0, p1, p2, p3
    pal[:, :3, 3] = 255
    pal[:, 3, 3] = np.where(c0 > c1, 255, 0) if alpha_mode3 else 255
    shifts = np.arange(16, dtype=np.uint32) * 2
    idx = ((bits[:, None] >> shifts) & 3).astype(np.intp)
    idx += (np.arange(n, dtype=np.intp) * 4)[:, None]
    return pal.reshape(n * 4, 4)[idx]


def _assemble(px, bw, bh, w, h):
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w]).astype(np.uint8)


def bc1(data, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    b = _blocks(data, 8, bw, bh)
    return _assemble(_color_block(b, True), bw, bh, w, h)


def bc2(data, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    b = _blocks(data, 16, bw, bh)
    px = _color_block(b[:, 8:], False)
    a64 = np.zeros(b.shape[0], np.uint64)
    for i in range(8):
        a64 |= b[:, i].astype(np.uint64) << np.uint64(8 * i)
    shifts = (np.arange(16, dtype=np.uint64) * np.uint64(4))
    px[:, :, 3] = ((a64[:, None] >> shifts) & np.uint64(0xf)).astype(np.int32) * 0x11
    return _assemble(px, bw, bh, w, h)


def bc3(data, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    b = _blocks(data, 16, bw, bh)
    px = _color_block(b[:, 8:], False)
    a0 = b[:, 0].astype(np.float64)
    a1 = b[:, 1].astype(np.float64)
    eight = (b[:, 0] > b[:, 1])[:, None]
    # 8-alpha mode: a0*(7-k+1)/7 ...
    w8 = np.array([0, 0, 6, 5, 4, 3, 2, 1], np.float64) / 7.0
    w6 = np.array([0, 0, 4, 3, 2, 1, 0, 0], np.float64) / 5.0
    pal8 = np.round(a0[:, None] * w8 + a1[:, None] * (1 - w8))
    pal6 = np.round(a0[:, None] * w6 + a1[:, None] * (1 - w6))
    pal6[:, 6] = 0
    pal6[:, 7] = 255
    pal = np.where(eight, pal8, pal6)
    pal[:, 0], pal[:, 1] = a0, a1
    bits = np.zeros(b.shape[0], np.uint64)
    for i in range(6):
        bits |= b[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    shifts = np.arange(16, dtype=np.uint64) * np.uint64(3)
    idx = ((bits[:, None] >> shifts) & np.uint64(7)).astype(np.intp)
    px[:, :, 3] = np.take_along_axis(pal, idx, axis=1).astype(np.int32)
    return _assemble(px, bw, bh, w, h)


def bgra8888(data, w, h):
    a = np.frombuffer(bytes(data[:w * h * 4]), np.uint8).reshape(h, w, 4)
    return np.ascontiguousarray(a[:, :, [2, 1, 0, 3]])


def bgra888(data, w, h):
    a = np.frombuffer(bytes(data[:w * h * 4]), np.uint8).reshape(h, w, 4)
    out = np.empty_like(a)
    out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = a[:, :, 2], a[:, :, 1], a[:, :, 0], 255
    return out
