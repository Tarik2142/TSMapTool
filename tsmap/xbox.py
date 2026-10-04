"""Xbox 360 side: STFS (LIVE/PIRS/CON) packages, Saber3D .s3dpak/.s3dlst, textures."""
import os
import re
import struct
import zlib


# ---------------------------------------------------------------- STFS ---

class StfsPackage:
    """Read-only access to an Xbox 360 STFS content package (DLC files from Xbox Live)."""

    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = f.read()
        d = self.data
        if d[:4] not in (b'LIVE', b'PIRS', b'CON '):
            raise ValueError('not an Xbox 360 STFS package')
        self.header_size = struct.unpack('>I', d[0x340:0x344])[0]
        self.content_type = struct.unpack('>I', d[0x344:0x348])[0]
        self.title_id = struct.unpack('>I', d[0x360:0x364])[0]
        self.display_name = self._wstr(0x411, 0x100)
        self.description = self._wstr(0xD11, 0x100)
        self.title_name = self._wstr(0x1691, 0x80)
        vd = d[0x379:0x379 + 0x24]
        ft_count = struct.unpack('<H', vd[3:5])[0]
        ft_block = int.from_bytes(vd[5:8], 'little')
        self.base = (self.header_size + 0xFFF) & 0xF000
        self.shift = 0 if self.base >> 12 == 0xB else 1
        self.files = []
        for i in range(ft_count):
            o = self._block(ft_block + i)
            for j in range(64):
                e = d[o + j * 0x40:o + j * 0x40 + 0x40]
                nl = e[0x28] & 0x3f
                if not nl:
                    continue
                self.files.append(dict(
                    name=e[:nl].decode('latin1'), dir=bool(e[0x28] & 0x80), contiguous=bool(e[0x28] & 0x40),
                    start=int.from_bytes(e[0x2F:0x32], 'little'),
                    size=struct.unpack('>I', e[0x34:0x38])[0], parent=struct.unpack('>h', e[0x32:0x34])[0]))
        for f in self.files:
            f['path'] = self._path(f)

    def _wstr(self, off, n):
        return self.data[off:off + n * 2].decode('utf-16be', 'replace').split('\0')[0]

    def _block(self, b):
        # data block -> file offset, skipping the interleaved hash-table blocks
        adj = (b // 170 + 1) << self.shift
        if b >= 170:
            adj += (b // 28900 + 1) << self.shift
        if b >= 28900:
            adj += 1 << self.shift
        return self.base + (b + adj) * 0x1000

    def _path(self, f):
        parts = [f['name']]
        p = f['parent']
        guard = 0
        while p >= 0 and guard < 64:
            parts.append(self.files[p]['name'])
            p = self.files[p]['parent']
            guard += 1
        return '/'.join(reversed(parts))

    def listdir(self):
        return [f['path'] for f in self.files if not f['dir']]

    def read(self, path):
        for f in self.files:
            if not f['dir'] and f['path'].lower() == path.lower():
                if not f['contiguous']:
                    raise NotImplementedError('fragmented STFS files are not supported (%s)' % path)
                n = (f['size'] + 0xFFF) // 0x1000
                out = bytearray()
                for i in range(n):
                    o = self._block(f['start'] + i)
                    out += self.data[o:o + 0x1000]
                return bytes(out[:f['size']])
        raise FileNotFoundError(path)


class FolderSource:
    """An already extracted Xbox DLC folder (same layout as inside the package)."""

    def __init__(self, path):
        self.root = path
        self.display_name = os.path.basename(os.path.normpath(path))
        self.description = ''

    def listdir(self):
        out = []
        for dp, _, fs in os.walk(self.root):
            for fn in fs:
                out.append(os.path.relpath(os.path.join(dp, fn), self.root).replace('\\', '/'))
        return out

    def read(self, path):
        for p in self.listdir():
            if p.lower() == path.lower():
                with open(os.path.join(self.root, p), 'rb') as f:
                    return f.read()
        raise FileNotFoundError(path)


def open_source(path):
    if os.path.isdir(path):
        return FolderSource(path)
    return StfsPackage(path)


# ------------------------------------------------------- s3dpak / s3dlst ---

def read_s3dpak(data):
    """Per-level Xbox pak: u32 n, n chunk offsets, zlib chunks -> stream with a TOC.
    Returns [(name, type, bytes)]."""
    n = struct.unpack('<I', data[:4])[0]
    offs = list(struct.unpack('<%dI' % n, data[4:4 + 4 * n])) + [len(data)]
    raw = b''.join(zlib.decompress(data[offs[i]:offs[i + 1]]) for i in range(n))
    cnt = struct.unpack('<I', raw[:4])[0]
    p = 4
    out = []
    for _ in range(cnt):
        off, size, nl = struct.unpack('<III', raw[p:p + 12])
        name = raw[p + 12:p + 12 + nl].decode('latin1')
        t = struct.unpack('<I', raw[p + 12 + nl:p + 16 + nl])[0]
        p += 24 + nl
        out.append((name, t, raw[off:off + size]))
    return out


def parse_list_text(text):
    """Preload list text 'Name = [ "a", "b" ]' -> [(section, [items])]."""
    return [(m.group(1), re.findall(r'"([^"]*)"', m.group(2)))
            for m in re.finditer(r'(?ms)^(\w+)\s+=\s+\[(.*?)^\]', text)]


def read_s3dlst(data):
    """Xbox preload list: sequence of (u32 len, bytes); the last one is the list text."""
    p = 0
    parts = []
    while p < len(data):
        n = struct.unpack('<I', data[p:p + 4])[0]
        parts.append(data[p + 4:p + 4 + n])
        p += 4 + n
    return parse_list_text(parts[-1].decode('latin1'))


def format_list(sections):
    """[(section, [items])] -> PC preload list entry data (u32 len + text, \\n line ends)."""
    txt = ''
    for s, items in sections:
        txt += '%s    =    [\n' % s + ',\n'.join('\t"%s"' % i for i in items) + ('\n' if items else '') + ']\n'
    b = txt.encode('latin1')
    return struct.pack('<I', len(b)) + b


# ------------------------------------------------------------ textures ---
# Saber3D 'TCIP' texture: tagged chunks [u16 tag][u32 end offset][payload]:
#   f0 'TCIP' | f1 width,height | f2 format | f9 mip count | ff pixel data | 0001 end
# Loose .pct files store relative offsets, Xbox paks absolute stream offsets,
# PC archives 0xFFFFFFFF everywhere. Pixel data is identical on both platforms.
# Formats: 0 = BGRA8, 12 = DXT1, 13 = DXT1 (spec), 17 = DXT5, 22 = lightmap dir,
#          36 = 3Dc/BC5 (normal maps), 37 = BC4 (single channel).

def tex_header(d):
    h = d.index(b'TCIP') - 6
    w, hh = struct.unpack('<II', d[h + 16:h + 24])
    fmt = struct.unpack('<I', d[h + 30:h + 34])[0]
    mips = struct.unpack('<I', d[h + 40:h + 44])[0]
    return h, w, hh, fmt, mips


def tex_format(d):
    return tex_header(d)[3]


def normalize_texture(d, prefix0=True):
    """Any TCIP texture -> PC archive form (offsets 0xFFFFFFFF, 1-byte 00 prefix for type 6)."""
    i = d.index(b'TCIP')
    h = i - 6
    if struct.unpack('<H', d[h:h + 2])[0] != 0xf0:
        raise ValueError('bad texture header')
    base = struct.unpack('<I', d[h + 2:h + 6])[0]
    if base != 0xffffffff:
        base -= i + 4
        b = bytearray(d)
        p = h
        while True:
            tag = struct.unpack('<H', b[p:p + 2])[0]
            val = struct.unpack('<I', b[p + 2:p + 6])[0]
            b[p + 2:p + 6] = b'\xff\xff\xff\xff'
            if tag == 0x0001:
                if p + 6 != len(b):
                    raise ValueError('texture end tag is not at the end')
                break
            nxt = val - base
            if not p < nxt <= len(b):
                raise ValueError('bad texture chunk offset')
            p = nxt
        d = bytes(b)
    body = d[h:]
    return (b'\x00' + body) if prefix0 else body


def _bc4(b):
    a0, a1 = b[0], b[1]
    bits = int.from_bytes(b[2:8], 'little')
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * j)) & 7] for j in range(16)]


def _dxt1_green(vals):
    g0 = round(max(vals) * 63 / 255)
    g1 = round(min(vals) * 63 / 255)
    if g0 == g1:
        return struct.pack('<HHI', g0 << 5, g1 << 5, 0)
    p0 = (g0 << 2) | (g0 >> 4)
    p1 = (g1 << 2) | (g1 >> 4)
    pal = [p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3]
    idx = 0
    for j, v in enumerate(vals):
        k = min(range(4), key=lambda i: abs(pal[i] - v))
        idx |= k << (2 * j)
    return struct.pack('<HHI', g0 << 5, g1 << 5, idx)


def to_nvidia(d):
    """PC ATI archives keep 3Dc (fmt 36 BC5 / 37 BC4); NVIDIA archives need fmt 17 DXT5
    (alpha = 2nd BC5 channel, green = 1st) / fmt 12 DXT1 (value in green, r = b = 0)."""
    h, _, _, fmt, _ = tex_header(d)
    body, tail = d[h + 50:-6], d[-6:]
    out = bytearray()
    if fmt == 36:
        for p in range(0, len(body), 16):
            b = body[p:p + 16]
            out += b[8:16] + _dxt1_green(_bc4(b[:8]))
        nf = 17
    elif fmt == 37:
        for p in range(0, len(body), 8):
            out += _dxt1_green(_bc4(body[p:p + 8]))
        nf = 12
    else:
        return d
    hdr = bytearray(d[h:h + 50])
    struct.pack_into('<I', hdr, 30, nf)
    return d[:h] + bytes(hdr) + bytes(out) + tail


def _c565(c):
    return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)


def decode_texture_rgb(d, max_w=None):
    """First mip as (w, h, RGB bytes) for previews; supports DXT1 (12/13) and BGRA8 (0)."""
    h0, w, h, fmt, _ = tex_header(d)
    data = d[h0 + 50:]
    rgb = bytearray(w * h * 3)
    if fmt in (12, 13):
        p = 0
        for by in range(0, h, 4):
            for bx in range(0, w, 4):
                c0, c1, idx = struct.unpack('<HHI', data[p:p + 8])
                p += 8
                a, b = _c565(c0), _c565(c1)
                if c0 > c1:
                    pal = [a, b, tuple((2 * a[i] + b[i]) // 3 for i in range(3)), tuple((a[i] + 2 * b[i]) // 3 for i in range(3))]
                else:
                    pal = [a, b, tuple((a[i] + b[i]) // 2 for i in range(3)), (0, 0, 0)]
                for j in range(16):
                    x, y = bx + (j & 3), by + (j >> 2)
                    if x < w and y < h:
                        o = (y * w + x) * 3
                        rgb[o:o + 3] = bytes(pal[(idx >> (2 * j)) & 3])
    elif fmt == 0:
        for i in range(w * h):
            b, g, r = data[i * 4:i * 4 + 3]
            rgb[i * 3:i * 3 + 3] = bytes((r, g, b))
    else:
        return None
    return w, h, bytes(rgb)


def png_bytes(w, h, rgb):
    raw = b''.join(b'\x00' + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, c):
        return struct.pack('>I', len(c)) + t + c + struct.pack('>I', zlib.crc32(t + c) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
