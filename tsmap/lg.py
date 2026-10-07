"""Saber3D level geometry (.lg): object property texts and their scripts.

A .lg file is a tree of chunks: u16 tag, u32 ABSOLUTE end offset, payload. A payload is either
raw data or [raw prefix][chunk][chunk]... (the prefix is usually a u32 count or a small record
header). Object properties live in text chunks (tag 0x1ba, NUL-terminated); the part after the
"#ssl" line is the object's script. They are found in three places:
  scene graph   0xf0 nodes: 0xf4 name, 0x115 template, 0xfd > 0x1ba text
  0x1ea         template instances (s3d_ref ...)
  0x1b8         effects / sound actors / interactive objects
Because chunk ends are absolute, changing the length of a text shifts every later chunk; all
chunk headers are rewritten from the parsed tree (see replace_texts).
"""
import re
import struct

TEXT = 0x1ba
SECTIONS = {0xf0: 'scene', 0x1ea: 'templates', 0x1b8: 'effects'}
_MAXPRE = 4096


class Chunk:
    __slots__ = ('tag', 'start', 'end', 'pre', 'children', 'parent')

    @property
    def header(self):
        return self.start - 6


def _seq(d, s, e):
    p, out = s, []
    while p + 6 <= e:
        t, end = struct.unpack_from('<HI', d, p)
        if not p + 6 <= end <= e:
            return None
        out.append((t, p + 6, end))
        p = end
    return out if p == e and out else None


def _parse(d, s, e, parent, depth):
    """Children of payload [s, e): the smallest raw prefix after which a chunk chain ends exactly at e."""
    for k in range(0, min(e - s - 6, _MAXPRE) + 1):
        p = s + k
        end = int.from_bytes(d[p + 2:p + 6], 'little')
        if not p + 6 <= end <= e:
            continue
        sq = _seq(d, p, e)
        if not sq:
            continue
        kids = []
        for t, a, b in sq:
            c = Chunk()
            c.tag, c.start, c.end, c.pre, c.children, c.parent = t, a, b, 0, None, parent
            if depth < 16 and t != TEXT:
                sub = _parse(d, a, b, c, depth + 1)
                if sub is not None:
                    c.pre, c.children = sub
            kids.append(c)
        return k, kids
    return None


def _walk(chunks):
    for c in chunks:
        yield c
        if c.children:
            yield from _walk(c.children)


def _cstr(b):
    return b.split(b'\0', 1)[0].decode('latin1', 'replace')


class LgObject:
    """One editable property text. `index` is its position among all texts of the file."""
    __slots__ = ('index', 'section', 'name', 'template', 'text', 'tail', 'newline', 'chunk')

    @property
    def script(self):
        m = re.search(r'(?m)^#ssl\s*$', self.text)
        return self.text[m.end():] if m else ''

    @property
    def has_script(self):
        return bool(re.search(r'(?m)^#ssl\s*$', self.text))


class LevelFile:
    def __init__(self, data):
        self.data = bytes(data)
        r = _parse(self.data, 0, len(self.data), None, 0)
        if r is None or r[0] != 0:
            raise ValueError('not a Saber3D level (.lg) file')
        self.top = r[1]
        self.chunks = list(_walk(self.top))
        self.objects = self._collect()

    def _collect(self):
        d = self.data
        objs = []
        for c in self.chunks:
            if c.tag != TEXT or c.children is not None:
                continue
            raw = d[c.start:c.end]
            z = raw.find(b'\0')
            body, tail = (raw, b'') if z < 0 else (raw[:z], raw[z:])
            o = LgObject()
            o.index, o.chunk, o.tail = len(objs), c, tail
            o.text = body.decode('latin1')
            o.newline = '\r\n' if '\r\n' in o.text else '\n'
            o.section, o.name, o.template = self._describe(c)
            objs.append(o)
        return objs

    def _describe(self, c):
        """(section, name, template) for a text chunk."""
        d = self.data
        section, node, top_child = 'other', None, None
        p = c.parent
        while p is not None:
            if node is None and p.tag == 0xf0 and p.children and any(k.tag == 0xf4 for k in p.children):
                node = p
            if p.parent is None:
                section = SECTIONS.get(p.tag, 'other')
                break
            top_child = p
            p = p.parent
        if node is not None:
            name = template = ''
            for k in node.children:
                if k.tag == 0xf4:
                    name = _cstr(d[k.start:k.end])
                elif k.tag == 0x115:
                    template = _cstr(d[k.start:k.end]).lstrip('&')
            return section, name or '?', template
        # records in 0x1ea / 0x1b8: printable names in the record's raw header
        rec = top_child or c.parent
        if rec is not None:
            head = d[rec.start:min(rec.end, c.header, rec.start + 400)]
            strs = [s.decode('latin1') for s in re.findall(rb'[\x20-\x7e]{3,}', head)]
            strs = [re.sub(r'^(LPTA|SNIA)', '', s) for s in strs]         # record magics glued to the name
            strs = [s for s in strs if len(s) >= 2 and not re.fullmatch(r'[\W\d]+', s)]
            if strs:
                name = strs[0]
                if '|' in name:                     # "s3d_refLocator8|step_ladder01"
                    name, tpl = name.split('|', 1)
                    return section, name, tpl
                return section, name, strs[1] if len(strs) > 1 else ''
        return section, '#%d' % c.header, ''

    def encode_text(self, obj, text):
        if text.replace('\r\n', '\n') == obj.text.replace('\r\n', '\n'):
            return self.data[obj.chunk.start:obj.chunk.end]     # unchanged: keep the original bytes (mixed line ends)
        text = text.replace('\r\n', '\n').replace('\n', obj.newline)
        body = text.encode('latin1')     # raises UnicodeEncodeError for characters the game cannot store
        if b'\0' in body:
            raise ValueError('text must not contain NUL characters')
        return body + (obj.tail or b'\0')

    def replace_texts(self, new_texts, other=None):
        """{object index: new text} -> new file bytes with every chunk header relocated.
        `other` = {chunk: payload} replaces other leaf chunks in the same pass."""
        payloads = dict(other or {})
        for idx, text in new_texts.items():
            o = self.objects[idx]
            payloads[o.chunk] = self.encode_text(o, text)
        return self.replace_chunks(payloads)

    def replace_chunks(self, payloads):
        """{leaf chunk: new payload} -> new file bytes with every chunk header relocated."""
        edits = []
        for c, payload in payloads.items():
            if c.children:
                raise ValueError('only leaf chunks can be replaced')
            if payload != self.data[c.start:c.end]:
                edits.append((c.start, c.end, payload))
        if not edits:
            return self.data
        edits.sort()
        ends = [e for _, e, _ in edits]
        deltas = [len(p) - (e - s) for s, e, p in edits]

        def moved(pos):
            # positions at or after the end of an edited chunk move by that edit's delta
            return pos + sum(dl for e, dl in zip(ends, deltas) if pos >= e)

        out = bytearray()
        prev = 0
        for s, e, p in edits:
            out += self.data[prev:s] + p
            prev = e
        out += self.data[prev:]
        for c in self.chunks:
            struct.pack_into('<I', out, moved(c.header) + 2, moved(c.end))
        return bytes(out)

    def rebuild(self, payloads=None, prefixes=None, insert_after=None, delete=(), replace=None):
        """New file bytes written from the chunk tree, for changes of the structure:
            payloads      {leaf chunk: new payload}
            prefixes      {chunk with children: new raw prefix} (e.g. a record count)
            insert_after  {chunk: [new chunk, ...]} siblings written right after it; a new chunk is
                          (tag, payload bytes) or (tag, prefix bytes, [new chunk, ...])
            delete        chunks left out (with everything inside)
            replace       {chunk: new chunk} written in its place (a leaf record that gets children)
        Every end offset is computed again, so unchanged input gives the same bytes."""
        d = self.data
        payloads, prefixes, insert_after = payloads or {}, prefixes or {}, insert_after or {}
        replace = replace or {}
        delete = set(delete)
        out = bytearray()

        def open_chunk(tag):
            out.extend(struct.pack('<HI', tag, 0))
            return len(out) - 4

        def close_chunk(pos):
            struct.pack_into('<I', out, pos, len(out))

        def write_new(spec):
            pos = open_chunk(spec[0])
            if len(spec) == 2:
                out.extend(spec[1])
            else:
                out.extend(spec[1])
                for k in spec[2]:
                    write_new(k)
            close_chunk(pos)

        def write(c):
            if c in delete:
                return
            if c in replace:
                write_new(replace[c])
                for spec in insert_after.get(c, ()):
                    write_new(spec)
                return
            pos = open_chunk(c.tag)
            if c.children is None or c in payloads:
                if c.children and c in payloads:
                    raise ValueError('only leaf chunks can be replaced')
                out.extend(payloads.get(c, d[c.start:c.end]))
            else:
                out.extend(prefixes.get(c, d[c.start:c.start + c.pre]))
                for k in c.children:
                    write(k)
            close_chunk(pos)
            for spec in insert_after.get(c, ()):
                write_new(spec)

        for c in self.top:
            write(c)
        return bytes(out)


# ------------------------------------------------------------------ helpers for the editor ---

OPENERS = re.compile(r'^\s*(override|func|if|while|for)\b')
CLOSER = re.compile(r'^\s*end\b')


def check_script(text):
    """Cheap sanity check of the #ssl part: block openers vs 'end'. Returns a list of problems."""
    m = re.search(r'(?m)^#ssl\s*$', text)
    if not m:
        return []
    depth, problems = 0, []
    for no, line in enumerate(text[m.end():].split('\n'), text[:m.end()].count('\n') + 1):
        code = line.split('//', 1)[0]
        if re.match(r'^\s*else\b', code):
            continue
        if OPENERS.match(code):
            depth += 1
        elif CLOSER.match(code):
            depth -= 1
            if depth < 0:
                problems.append((no, 'end'))
                depth = 0
    if depth > 0:
        problems.append((None, 'unclosed'))
    if text.count('"') % 2:
        problems.append((None, 'quotes'))
    return problems


_SIG = re.compile(rb'(?<![\x20-\x7e])([A-Z][A-Za-z0-9_]*)\(((?:[a-z_][A-Za-z0-9_]* : [A-Za-z]+(?: = [^,)\x00]{0,24})?(?:, )?)*)\)( : [A-Za-z]+)?(?=\x00)')


def script_functions(exe_bytes):
    """Built-in script functions with signatures, as registered in the game executable."""
    return sorted({m.group(0).decode('latin1') for m in _SIG.finditer(exe_bytes)}, key=str.lower)
