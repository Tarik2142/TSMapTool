"""TimeShift (PC) .s3darc archive reader / writer.

Layout (all integers little-endian):
    Z(u32 version = 1)
    Z(str base_archive)          "" for out*.s3darc, "out*.s3darc" for patch*.s3darc
    u32 index_offset
    <data: one raw zlib stream per own entry>
    Z(index)                     at index_offset
where Z(x) = u32 size, u32 compressed size, zlib(x) and str = u32 len + bytes.

index = u32 count, then per entry:
    str name, u32 type, i32 offset (-1 = stored in the base archive),
    u32 crc32 of the data (own entries) / entry index in the base archive (offset -1),
    u32 size, u32 compressed size
Entries are unique by (name, type), not by name alone.
"""
import os
import struct
import zlib


def compress(data):
    # level 1 -> zlib header 78 01, same as the original archives
    return zlib.compress(data, 1)


def crc32(data):
    return zlib.crc32(data) & 0xffffffff


def _read_z(f):
    size, csize = struct.unpack('<II', f.read(8))
    data = zlib.decompress(f.read(csize))
    if len(data) != size:
        raise ValueError('bad Z block size')
    return data


def _z(data):
    c = compress(data)
    return struct.pack('<II', len(data), len(c)) + c


def _str(s):
    b = s.encode('latin1')
    return struct.pack('<I', len(b)) + b


class Entry:
    __slots__ = ('name', 'type', 'off', 'x', 'size', 'csize')

    def __init__(self, name, type, off, x, size, csize):
        self.name, self.type, self.off, self.x, self.size, self.csize = name, type, off, x, size, csize

    @property
    def key(self):
        return (self.name.lower(), self.type)

    @property
    def own(self):
        return self.off >= 0

    def copy(self):
        return Entry(self.name, self.type, self.off, self.x, self.size, self.csize)


class Archive:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.version = struct.unpack('<I', _read_z(f))[0]
            b = _read_z(f)
            self.base_name = b[4:4 + struct.unpack('<I', b[:4])[0]].decode('latin1')
            self.data_start = f.tell() + 4
            self.index_offset = struct.unpack('<I', f.read(4))[0]
            f.seek(self.index_offset)
            idx = _read_z(f)
        count = struct.unpack('<I', idx[:4])[0]
        p = 4
        self.entries = []
        for _ in range(count):
            n = struct.unpack('<I', idx[p:p + 4])[0]
            name = idx[p + 4:p + 4 + n].decode('latin1')
            p += 4 + n
            t, off, x, size, csize = struct.unpack('<IiIII', idx[p:p + 20])
            p += 20
            self.entries.append(Entry(name, t, off, x, size, csize))
        if p != len(idx):
            raise ValueError('%s: trailing data in index' % path)
        self.by_key = {e.key: e for e in self.entries}
        self._f = None
        self._base = None

    def _file(self):
        if self._f is None:
            self._f = open(self.path, 'rb')
        return self._f

    def close(self):
        if self._f:
            self._f.close()
            self._f = None
        if self._base:
            self._base.close()

    def base(self, base_dir=None):
        """The archive this one is layered on (out*.s3darc), opened lazily."""
        if not self.base_name:
            return None
        if self._base is None:
            self._base = Archive(os.path.join(base_dir or os.path.dirname(self.path), self.base_name))
        return self._base

    def get(self, name, type=None):
        if type is not None:
            return self.by_key.get((name.lower(), type))
        for e in self.entries:
            if e.name.lower() == name.lower():
                return e
        return None

    def raw(self, e):
        f = self._file()
        f.seek(e.off)
        return f.read(e.csize)

    def read(self, e, base_dir=None):
        """Uncompressed data of an entry; base-archive entries are read from the base."""
        if not e.own:
            b = self.base(base_dir)
            return b.read(b.entries[e.x])
        data = zlib.decompress(self.raw(e))
        if len(data) != e.size:
            raise ValueError('%s: bad size of %s' % (self.path, e.name))
        return data


def write_archive(src, path, replace=None, insert_after_type0=(), append=(), remove=()):
    """Write a copy of `src` to `path`.

    replace            {(name_lower, type): data}  new data for existing entries
    insert_after_type0 [(name, type, data)]         inserted after the last type-0 entry
    append             [(name, type, data)]         appended at the end of the index
    remove             {(name_lower, type)}         entries to drop
    Own data of untouched entries is copied as-is (compressed bytes are not re-encoded).
    """
    replace = replace or {}
    remove = set(remove)
    out = bytearray()
    out += _z(struct.pack('<I', src.version))
    out += _z(_str(src.base_name))
    idx_field = len(out)
    out += b'\0\0\0\0'  # index offset, patched below
    ents = []

    def add_new(name, t, data):
        c = compress(data)
        e = Entry(name, t, len(out), crc32(data), len(data), len(c))
        out.extend(c)
        ents.append(e)

    last0 = max((i for i, e in enumerate(src.entries) if e.type == 0), default=-1)
    if last0 < 0:
        for n, t, d in insert_after_type0:
            add_new(n, t, d)
    for i, e in enumerate(src.entries):
        if e.key in remove:
            pass
        elif e.key in replace:
            add_new(e.name, e.type, replace[e.key])
        elif e.own:
            raw = src.raw(e)
            ne = e.copy()
            ne.off = len(out)
            out.extend(raw)
            ents.append(ne)
        else:
            ents.append(e.copy())
        if i == last0:
            for n, t, d in insert_after_type0:
                add_new(n, t, d)
    for n, t, d in append:
        add_new(n, t, d)

    keys = [e.key for e in ents]
    if len(keys) != len(set(keys)):
        dup = sorted({k for k in keys if keys.count(k) > 1})[:5]
        raise ValueError('duplicate archive entries: %s' % dup)

    idx = bytearray(struct.pack('<I', len(ents)))
    for e in ents:
        idx += _str(e.name) + struct.pack('<IiIII', e.type, e.off, e.x, e.size, e.csize)
    struct.pack_into('<I', out, idx_field, len(out))
    out += _z(bytes(idx))
    with open(path, 'wb') as f:
        f.write(out)
    return len(ents)


def verify_archive(path):
    """Decompress every own entry and check its CRC. Returns the number of checked entries."""
    a = Archive(path)
    try:
        n = 0
        for e in a.entries:
            if e.own:
                data = zlib.decompress(a.raw(e))
                if len(data) != e.size or crc32(data) != e.x:
                    raise ValueError('%s: entry %s (type %d) is corrupt' % (path, e.name, e.type))
                n += 1
        return n
    finally:
        a.close()
