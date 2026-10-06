"""TimeShift map manager core: map library, import / export, archive rebuild.

The game archives are always rebuilt from the original archives (backup) plus every map in
the library, so removing a map never leaves garbage behind and "restore" is a plain copy.
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import zipfile
import zlib

from . import lg, s3darc, xbox
from .i18n import _

LANGS = ['czech', 'english', 'french', 'german', 'hungarian', 'italian', 'polish', 'russian', 'spanish']
XBOX_LANGS = {'english': 'English', 'french': 'French', 'german': 'German', 'italian': 'Italian', 'spanish': 'Spanish'}
ARCS = {'main': 'patch.s3darc', 'nv': 'patch_nv.s3darc', 'ati': 'patch_ati.s3darc'}
MODES = [('bDM', 'DM'), ('bTDM', 'TDM'), ('bKOT', 'KOT'), ('bSTM', 'STM'), ('bCTF', 'CTF'), ('b1V1', '1v1')]
ID_RANGES = [range(15, 30), range(54, 128)]   # free map ids: before singleplayer (30..53) first
BUNDLE_FORMAT = 1
DROP_KEYS = ('id', 'IsDLC', 'dlcMask', 'dlcMapMask', 'idXLAST')
DEFAULT_ENTRY = [('EnableInDemo', 'No'), ('minPlayers', '2'), ('maxPlayers', '16'), ('bDM', 'Yes'), ('bTDM', 'Yes'),
                 ('bKOT', 'Yes'), ('bSTM', 'No'), ('bCTF', 'No'), ('b1V1', 'Yes'), ('bLowGravity', 'Yes'),
                 ('eeDegStart', '25'), ('eeDegEnd', '40')]


class MapToolError(Exception):
    pass


def _noop(msg):
    pass


def unquote(v):
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


# ------------------------------------------------------------ 'ps' container ---

class PsContainer:
    """The 'ps' archive entry: all script / class files of the game.

    u32 file count, u32 total block count, u32 total compressed size, then per file:
    u32 compressed size, u32 block count, block count * (u64 name hash, u32 block length),
    followed by the zlib streams. A file is a concatenation of named blocks (classes) in
    table order; the block lengths must sum up to the file size.
    """

    def __init__(self, data):
        cnt, self.total_blocks, _total = struct.unpack('<3I', data[:12])
        p = 12
        self.recs = []
        for _i in range(cnt):
            cs, n = struct.unpack('<II', data[p:p + 8])
            tab = [list(struct.unpack('<QI', data[p + 8 + 12 * k:p + 20 + 12 * k])) for k in range(n)]
            self.recs.append([cs, tab])
            p += 8 + 12 * n
        self.streams = []
        for cs, _tab in self.recs:
            self.streams.append(data[p:p + cs])
            p += cs
        if p != len(data):
            raise MapToolError(_('unexpected ps container layout'))

    def find(self, needle):
        hits = [i for i, s in enumerate(self.streams) if needle in zlib.decompress(s)]
        if len(hits) != 1:
            raise MapToolError(_('class %r found %d times in ps', needle, len(hits)))
        return hits[0]

    def text(self, i):
        return zlib.decompress(self.streams[i]).decode('latin1')

    def set_text(self, i, new):
        """Replace file i; only its last block may change (its length is updated)."""
        old = self.text(i)
        tab = self.recs[i][1]
        start = len(old) - tab[-1][1]
        if new[:start] != old[:start]:
            raise MapToolError(_('only the last class of a ps file can be modified'))
        tab[-1][1] = len(new) - start
        self.streams[i] = s3darc.compress(new.encode('latin1'))
        self.recs[i][0] = len(self.streams[i])

    def build(self):
        out = bytearray(struct.pack('<3I', len(self.recs), self.total_blocks, sum(len(s) for s in self.streams)))
        for cs, tab in self.recs:
            out += struct.pack('<II', cs, len(tab)) + b''.join(struct.pack('<QI', h, v) for h, v in tab)
        for s in self.streams:
            out += s
        return bytes(out)


# ------------------------------------------------------------- gs_map_list ---

def _blocks(text):
    """Brace structure of a class text: [(name, name_pos, open_pos, close_pos, depth)]."""
    out = []
    stack = []
    i = 0
    n = len(text)
    line_start = True
    while i < n:
        c = text[i]
        if line_start and c in ' \t':
            i += 1
            continue
        if line_start and c == '#':                       # preprocessor line
            i = text.find('\n', i)
            i = n if i < 0 else i
            continue
        line_start = False
        if c == '\n':
            line_start = True
        elif c == '/' and text.startswith('//', i):
            i = text.find('\n', i)
            i = n if i < 0 else i
            continue
        elif c == '"':
            j = text.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        elif c == '{':
            m = re.search(r'(\w+)\s*$', text[max(0, i - 200):i])
            name = m.group(1) if m else ''
            npos = i - len(text[max(0, i - 200):i]) + m.start(1) if m else i
            stack.append([name, npos, i, len(stack)])
        elif c == '}':
            if stack:
                name, npos, op, depth = stack.pop()
                out.append((name, npos, op, i, depth))
        i += 1
    return out


def strip_dev_block(text):
    """Drop the '#ifndef _RETAIL' block with placeholder dlc1_* maps (not shown in retail)."""
    a = text.find('#ifndef _RETAIL')
    if a < 0:
        return text
    p = a
    for _i in range(3):
        p = text.index('#endif', p) + 6
    p = text.index('\n', p) + 1
    return text[:a] + text[p:]


def parse_maps(text):
    """gs_map_list text -> [{class, section, id, props:[(k, v)], start, end}] (end = after the line)."""
    bl = _blocks(text)
    maps_blk = [b for b in bl if b[0] == 'maps' and b[4] == 1]
    if not maps_blk:
        raise MapToolError(_('gs_map_list: maps section not found'))
    mo, mc = maps_blk[0][2], maps_blk[0][3]
    sections = [b for b in bl if b[4] == 2 and mo < b[2] < mc]
    out = []
    for sname, _np, so, sc, _dp in sections:
        for name, npos, op, cl, depth in bl:
            if depth != 3 or not so < op < sc:
                continue
            props = []
            for ln in text[op + 1:cl].split('\n'):
                ln = ln.split('//')[0].strip()
                if '=' in ln and not ln.startswith('#'):
                    k, v = ln.split('=', 1)
                    props.append((k.strip(), v.strip()))
            pd = dict(props)
            start = text.rfind('\n', 0, npos) + 1
            end = text.find('\n', cl)
            end = len(text) if end < 0 else end + 1
            try:
                mid = int(pd.get('id', '-1'))
            except ValueError:
                mid = -1
            out.append(dict({'class': name, 'section': sname, 'id': mid, 'props': props, 'start': start, 'end': end}))
    return out


def section_close_line(text, section):
    bl = _blocks(text)
    for name, _np, op, cl, depth in bl:
        if name == section and depth == 2:
            return text.rfind('\n', 0, cl) + 1
    raise MapToolError(_('gs_map_list: section %s not found', section))


def map_block_text(cls, mid, entry):
    lines = ['         %s {' % cls, '            %-14s = %d' % ('id', mid)]
    for k, v in entry:
        if k in DROP_KEYS:
            continue
        v = v.encode('latin1', 'replace').decode('latin1')   # class files are 8-bit text
        lines.append('            %-14s = %s' % (k, v))
    lines.append('         }')
    return '\r\n'.join(lines) + '\r\n'


def modes_of(props):
    pd = {k: unquote(v).lower() for k, v in props}
    return [label for key, label in MODES if pd.get(key) == 'yes']


# ----------------------------------------------------------------- strings ---

def string_lookup(text, key):
    m = re.search(r'(?m)^' + re.escape(key) + r'\t+(.*?)\r?$', text)
    return unquote(m.group(1)) if m else None


def string_lines(text):
    """'KEY<tabs>"value"' lines of a strings file -> {key: raw value}."""
    out = {}
    for ln in text.replace('\r\n', '\n').split('\n'):
        m = re.match(r'^(\S+)\t+(.*)$', ln.lstrip('﻿'))
        if m:
            out[m.group(1)] = m.group(2)
    return out


# ------------------------------------------------------------------ bundle ---

class Bundle:
    """A .tsmap file: zip with manifest.json + files/*.bin (uncompressed archive entry data).

    manifest: format, class, title, source, entry [[key, value]] (map list properties, no id),
              strings {pc_language: {key: raw value}}, loading (texture name or ""),
              files [{name, type, arc: main|nv|ati|all, path}]  (arc 'all' = preload list in every archive)
    """

    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.manifest = json.loads(z.read('manifest.json').decode('utf-8'))
        if self.manifest.get('format') != BUNDLE_FORMAT:
            raise MapToolError(_('%s: unsupported .tsmap format', path))

    @property
    def cls(self):
        return self.manifest['class']

    def read(self, path):
        with zipfile.ZipFile(self.path) as z:
            return z.read(path)

    def files(self):
        with zipfile.ZipFile(self.path) as z:
            for f in self.manifest['files']:
                yield f, z.read(f['path'])

    @staticmethod
    def write(path, manifest, files):
        """files: [(name, type, arc, data)]"""
        manifest = dict(manifest, format=BUNDLE_FORMAT, files=[])
        tmp = path + '.tmp'
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for i, (n, t, arc, data) in enumerate(files):
                p = 'files/%03d.bin' % i
                z.writestr(p, data)
                manifest['files'].append({'name': n, 'type': t, 'arc': arc, 'path': p, 'size': len(data)})
            z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return Bundle(path)


# ------------------------------------------------------------------- game ---

class Game:
    def __init__(self, game_dir, tool_dir):
        self.dir = os.path.abspath(game_dir)
        self.paks = os.path.join(self.dir, 'preload', 'paks')
        self.backup = os.path.join(self.dir, 'preload', 'paks_backup_original')
        self.tool_dir = tool_dir
        self.lib_dir = os.path.join(tool_dir, 'library')
        self.lib_file = os.path.join(self.lib_dir, 'library.json')
        self.applied_file = os.path.join(self.lib_dir, 'applied.json')
        if not os.path.isfile(os.path.join(self.paks, 'out.s3darc')):
            raise MapToolError(_('TimeShift not found in %s (no preload\\paks\\out.s3darc)', self.dir))
        os.makedirs(self.lib_dir, exist_ok=True)
        self._orig = None
        self._cache = {}

    # ---- originals / backup
    def ensure_backup(self, log=_noop):
        if all(os.path.isfile(os.path.join(self.backup, f)) for f in ARCS.values()):
            return
        a = s3darc.Archive(os.path.join(self.paks, ARCS['main']))
        try:
            ps = PsContainer(a.read(a.get('ps', 2)))
            text = ps.text(ps.find(b'gs_map_list {'))
        finally:
            a.close()
        if '#ifndef _RETAIL' not in text:
            raise MapToolError(_('There is no backup of the original archives and the current ones are already modified. '
                                 'Put the original patch*.s3darc files into %s', self.backup))
        log(_('Backing up the original archives to %s', self.backup))
        os.makedirs(self.backup, exist_ok=True)
        for f in os.listdir(self.paks):
            if f.lower().startswith('patch') and f.lower().endswith('.s3darc'):
                shutil.copy2(os.path.join(self.paks, f), os.path.join(self.backup, f))
        ini = os.path.join(self.dir, 'bin', 'steamwars.ini')
        if os.path.isfile(ini):
            shutil.copy2(ini, os.path.join(self.backup, 'steamwars.ini'))

    @property
    def orig(self):
        if self._orig is None:
            self.ensure_backup()
            self._orig = {k: s3darc.Archive(os.path.join(self.backup, v)) for k, v in ARCS.items()}
        return self._orig

    def close(self):
        if self._orig:
            for a in self._orig.values():
                a.close()
            self._orig = None

    def read_orig(self, arc, e):
        return self.orig[arc].read(e, base_dir=self.paks)

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def orig_ps(self):
        return self.cached('ps', lambda: self.read_orig('main', self.orig['main'].get('ps', 2)))

    @property
    def orig_map_text(self):
        def load():
            ps = PsContainer(self.orig_ps)
            return ps.text(ps.find(b'gs_map_list {'))
        return self.cached('maptext', load)

    @property
    def orig_maps(self):
        return self.cached('maps', lambda: parse_maps(strip_dev_block(self.orig_map_text)))

    def orig_strings(self, lang):
        return self.cached('str_' + lang, lambda: self.read_orig(
            'main', self.orig['main'].get('strings_' + lang, 14)).decode('utf-16le'))

    @property
    def language(self):
        def load():
            ini = os.path.join(self.dir, 'bin', 'steamwars.ini')
            try:
                with open(ini, encoding='latin1') as f:
                    m = re.search(r'(?mi)^\s*Language\s*=\s*(\w+)', f.read())
                lang = m.group(1).lower() if m else 'english'
            except OSError:
                lang = 'english'
            return lang if lang in LANGS else 'english'
        return self.cached('lang', load)

    @property
    def known(self):
        """Every (name_lower, type) of the original game and name_lower -> canonical name."""
        def load():
            nt, names = set(), {}
            for a in self.orig.values():
                for e in a.entries:
                    nt.add(e.key)
                    names.setdefault(e.name.lower(), e.name)
            return nt, names
        return self.cached('known', load)

    @property
    def pc_lists(self):
        def load():
            a = self.orig['main']
            return {e.name: xbox.parse_list_text(self.read_orig('main', e)[4:].decode('latin1'))
                    for e in a.entries if e.type == 0}
        return self.cached('lists', load)

    @property
    def known_sections(self):
        def load():
            out = {}
            for secs in self.pc_lists.values():
                for s, items in secs:
                    d = out.setdefault(s, {})
                    for it in items:
                        d.setdefault(it.lower(), it)
            return out
        return self.cached('ksec', load)

    @property
    def mp_shader_pairs(self):
        def load():
            pairs = []
            for m in self.orig_maps:
                if m['section'] == 'multiplayer' and m['class'] in self.pc_lists:
                    for s, items in self.pc_lists[m['class']]:
                        if s == 'ShaderPairs':
                            pairs += [i for i in items if i not in pairs]
            return pairs
        return self.cached('pairs', load)

    # ---- library
    def library(self):
        try:
            with open(self.lib_file, encoding='utf-8') as f:
                lib = json.load(f)
        except FileNotFoundError:
            lib = {}
        lib.setdefault('maps', [])
        lib.setdefault('hidden', [])
        lib.setdefault('overrides', [])     # edited level files: {level, name, type, file, sha1, time[, arcs]}
        return lib

    def save_library(self, lib):
        tmp = self.lib_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.lib_file)

    def bundle(self, m):
        return Bundle(os.path.join(self.lib_dir, m['file']))

    def applied(self):
        try:
            with open(self.applied_file, encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'maps': [], 'hidden': []}

    def pending(self):
        lib = self.library()
        ap = self.applied()
        return ([(m['class'], m['id']) for m in lib['maps']] != [tuple(x) for x in ap.get('maps', [])]
                or sorted(lib['hidden']) != sorted(ap.get('hidden', []))
                or sorted([o['name'], o['sha1']] for o in lib['overrides']) != sorted(ap.get('overrides', [])))

    # ---- edited level files (scripts / object properties)
    def level_override(self, cls):
        for o in self.library()['overrides']:
            if o['level'].lower() == cls.lower() and o['type'] == 16:
                return o
        return None

    def override_data(self, name, type, arc='main'):
        """Edited data of an archive entry, or None."""
        for o in self.library()['overrides']:
            if o['name'].lower() == name.lower() and o['type'] == type and arc in o.get('arcs', ['main']):
                with open(os.path.join(self.lib_dir, o['file']), 'rb') as f:
                    return f.read()
        return None

    def level_data(self, cls, original=False):
        """Current .lg of a map: the edited copy if there is one, else the one from the library / game."""
        if not original:
            o = self.level_override(cls)
            if o:
                with open(os.path.join(self.lib_dir, o['file']), 'rb') as f:
                    return f.read()
        name = cls + '.lg'
        for m in self.library()['maps']:
            if m['class'].lower() == cls.lower():
                for f, data in self.bundle(m).files():
                    if f['name'].lower() == name.lower() and f['type'] == 16:
                        return data
                raise MapToolError(_('%s has no level file', cls))
        e = self.orig['main'].get(name, 16)
        if e is None:
            raise MapToolError(_('%s has no level file', cls))
        return self.read_orig('main', e)

    def save_level_override(self, cls, data, log=_noop):
        self.save_override(cls, cls + '.lg', 16, data)
        log(_('Saved the edited level %s', cls))

    def save_override(self, cls, name, type, data, arcs=('main',)):
        """Replace an archive entry of a map (its level, preload list ...) in the given archives."""
        lib = self.library()
        arcs = list(arcs)
        keep, dropped = [], []
        for o in lib['overrides']:
            if o['name'].lower() == name.lower() and o['type'] == type:
                rest = [a for a in o.get('arcs', ['main']) if a not in arcs]
                if not rest:
                    dropped.append(o['file'])
                    continue
                o['arcs'] = rest            # still used by the other archives
            keep.append(o)
        lib['overrides'] = keep
        rel = 'overrides/' + re.sub(r'[^\w.-]', '_', name) + ('' if type == 16 else '.type%d' % type)
        if arcs != ['main']:
            rel += '.' + '_'.join(arcs)
        os.makedirs(os.path.join(self.lib_dir, 'overrides'), exist_ok=True)
        tmp = os.path.join(self.lib_dir, rel + '.tmp')
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, os.path.join(self.lib_dir, rel))
        o = {'level': cls, 'name': name, 'type': type, 'file': rel, 'sha1': hashlib.sha1(data).hexdigest(),
             'time': datetime.datetime.now().isoformat(timespec='seconds')}
        if arcs != ['main']:
            o['arcs'] = arcs
        lib['overrides'].append(o)
        self.save_library(lib)
        for f in dropped:
            if f != rel:
                try:
                    os.remove(os.path.join(self.lib_dir, f))
                except FileNotFoundError:
                    pass

    def map_list_data(self, cls, arc='main', original=False):
        """Current preload list of a map in one archive: edited, from the library or from the game (`original`:
        not the edited one)."""
        data = None if original else self.override_data(cls, 0, arc)
        if data is not None:
            return data
        for m in self.library()['maps']:
            if m['class'].lower() == cls.lower():
                for f, d in self.bundle(m).files():
                    if f['type'] == 0 and f['name'].lower() == cls.lower() and f['arc'] in ('all', arc):
                        return d
                return None
        e = self.orig[arc].get(cls, 0)
        return None if e is None else self.read_orig(arc, e)

    def save_map_lists(self, cls, lists):
        """{arc: preload list data} -> overrides; archives with the same list share one file."""
        groups = {}
        for arc, data in lists.items():
            groups.setdefault(data, []).append(arc)
        for data, arcs in groups.items():
            self.save_override(cls, cls, 0, data, arcs=[a for a in ARCS if a in arcs])

    def drop_level_override(self, cls, log=_noop):
        lib = self.library()
        keep, drop = [], []
        for o in lib['overrides']:
            (drop if o['level'].lower() == cls.lower() else keep).append(o)
        if not drop:
            return False
        lib['overrides'] = keep
        self.save_library(lib)
        for o in drop:
            try:
                os.remove(os.path.join(self.lib_dir, o['file']))
            except FileNotFoundError:
                pass
        log(_('Restored the original level %s', cls))
        return True

    def script_functions(self):
        """Built-in script functions (signatures) read from the game executable, cached per exe."""
        def load():
            cache = os.path.join(self.lib_dir, 'functions.json')
            exes = [os.path.join(self.dir, 'bin', f) for f in os.listdir(os.path.join(self.dir, 'bin'))
                    if f.lower().endswith('.exe')]
            exes.sort(key=lambda p: ('fixed' not in p.lower(), 'dev' in p.lower(), -os.path.getsize(p)))
            if not exes:
                return []
            exe = exes[0]
            key = [os.path.basename(exe), os.path.getsize(exe), int(os.path.getmtime(exe))]
            try:
                with open(cache, encoding='utf-8') as f:
                    c = json.load(f)
                if c.get('exe') == key:
                    return c['functions']
            except (OSError, ValueError):
                pass
            with open(exe, 'rb') as f:
                funcs = lg.script_functions(f.read())
            try:
                with open(cache, 'w', encoding='utf-8') as f:
                    json.dump({'exe': key, 'functions': funcs}, f, indent=0)
            except OSError:
                pass
            return funcs
        return self.cached('functions', load)

    def all_classes(self):
        return {m['class'].lower() for m in self.orig_maps} | {m['class'].lower() for m in self.library()['maps']}

    def free_id(self, lib):
        used = {m['id'] for m in self.orig_maps} | {m['id'] for m in lib['maps']}
        for r in ID_RANGES:
            for i in r:
                if i not in used:
                    return i
        raise MapToolError(_('no free map ids left'))

    def add_bundle(self, b, log=_noop):
        cls = b.cls
        if cls.lower() in self.all_classes():
            raise MapToolError(_('map %s already exists', cls))
        if (cls.lower(), 0) in self.known[0]:
            raise MapToolError(_('map %s clashes with an original level name', cls))
        lib = self.library()
        fname = re.sub(r'[^\w.-]', '_', cls) + '.tsmap'
        dst = os.path.join(self.lib_dir, fname)
        if os.path.abspath(b.path) != os.path.abspath(dst):
            shutil.copy2(b.path, dst)
        mid = self.free_id(lib)
        if mid >= 30:
            log(_('Warning: map id %d is above the singleplayer range (untested)', mid))
        lib['maps'].append({'class': cls, 'id': mid, 'file': fname, 'title': b.manifest.get('title', cls),
                            'source': b.manifest.get('source', ''),
                            'added': datetime.datetime.now().isoformat(timespec='seconds')})
        self.save_library(lib)
        log(_('Added %s (id %d)', cls, mid))
        return mid

    # ---- map listing
    def maps(self):
        """Rows for display: original multiplayer maps + library maps."""
        lib = self.library()
        hidden = {h.lower() for h in lib['hidden']}
        lang = self.language
        rows = []
        for m in self.orig_maps:
            if m['section'] != 'multiplayer':
                continue
            pd = dict(m['props'])
            rows.append({'class': m['class'], 'id': m['id'], 'title': unquote(pd.get('friendlyName', m['class'])),
                         'name': string_lookup(self.orig_strings(lang), unquote(pd.get('nameId', ''))) or '',
                         'modes': modes_of(m['props']), 'players': '%s-%s' % (pd.get('minPlayers', '?'), pd.get('maxPlayers', '?')),
                         'kind': 'hidden' if m['class'].lower() in hidden else 'original', 'props': m['props']})
        for m in lib['maps']:
            try:
                b = self.bundle(m)
            except (OSError, MapToolError, KeyError, ValueError) as e:
                rows.append({'class': m['class'], 'id': m['id'], 'title': m.get('title', m['class']), 'name': '',
                             'modes': [], 'players': '?', 'kind': 'broken', 'props': [], 'error': str(e)})
                continue
            entry = b.manifest['entry']
            pd = dict(entry)
            st = b.manifest['strings'].get(lang) or b.manifest['strings'].get('english', {})
            rows.append({'class': m['class'], 'id': m['id'], 'title': unquote(pd.get('friendlyName', m['class'])),
                         'name': unquote(st.get(unquote(pd.get('nameId', '')), '')),
                         'modes': modes_of(entry), 'players': '%s-%s' % (pd.get('minPlayers', '?'), pd.get('maxPlayers', '?')),
                         'kind': 'custom', 'props': [tuple(x) for x in entry], 'source': m.get('source', '')})
        edited = {o['level'].lower() for o in lib['overrides']}
        for r in rows:
            r['edited'] = r['class'].lower() in edited
        rows.sort(key=lambda r: r['id'])
        return rows

    def map_details(self, cls):
        """(description, preview PNG bytes or None) for a map."""
        lang = self.language
        for m in self.library()['maps']:
            if m['class'].lower() == cls.lower():
                b = self.bundle(m)
                pd = dict(b.manifest['entry'])
                st = b.manifest['strings'].get(lang) or b.manifest['strings'].get('english', {})
                desc = unquote(st.get(unquote(pd.get('description', '')), ''))
                tex = None
                small = (b.manifest.get('loading') or '').lower() + '_small'
                for f in b.manifest['files']:
                    if f['name'].lower() == small and f['type'] == 6:
                        tex = b.read(f['path'])
                return desc, _preview(tex)
        for m in self.orig_maps:
            if m['class'].lower() == cls.lower():
                pd = dict(m['props'])
                desc = string_lookup(self.orig_strings(lang), unquote(pd.get('description', ''))) or ''
                e = self.orig['main'].get(unquote(pd.get('LoadingTexture', '')) + '_small', 6)
                return desc, _preview(self.read_orig('main', e) if e else None)
        raise MapToolError(_('unknown map %s', cls))

    # ---- import
    def import_path(self, path, only=None, log=_noop):
        """Import maps from an Xbox 360 DLC package / extracted folder or a .tsmap bundle."""
        if path.lower().endswith('.tsmap'):
            b = Bundle(path)
            if only and b.cls.lower() not in {o.lower() for o in only}:
                return []
            self.add_bundle(b, log)
            return [b.cls]
        src = xbox.open_source(path)
        files = src.listdir()
        levels = sorted({p[:-7] for p in files if p.lower().endswith('.s3dpak')},
                        key=lambda s: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s)])
        if not levels:
            raise MapToolError(_('no maps (.s3dpak) found in %s', path))
        log('%s: %s' % (os.path.basename(path), getattr(src, 'display_name', '')))
        desc = src.read('gs_pkg_desc.cls').decode('latin1') if 'gs_pkg_desc.cls' in files else ''
        xstr = {}
        for pcl, xl in XBOX_LANGS.items():
            p = 'texts/strings_%s.str' % xl
            if p in files:
                xstr[pcl] = string_lines(src.read(p).decode('utf-16le'))
        added = []
        existing = self.all_classes()
        for lvl in levels:
            if only and lvl.lower() not in {o.lower() for o in only}:
                continue
            if lvl.lower() in existing:
                log(_('Skipped %s: already installed', lvl))
                continue
            log(_('Converting %s ...', lvl))
            tmp = os.path.join(self.lib_dir, re.sub(r'[^\w.-]', '_', lvl) + '.tsmap')
            try:
                b = self._convert_xbox_level(src, files, lvl, desc, xstr, getattr(src, 'display_name', ''), tmp, log)
                self.add_bundle(b, log)
            except Exception:
                for p in (tmp, tmp + '.tmp'):
                    if os.path.exists(p):
                        os.remove(p)
                raise
            added.append(lvl)
        return added

    def _convert_xbox_level(self, src, files, lvl, desc, xstr, source_name, out_path, log):
        known_nt, known_names = self.known
        # map list entry
        m = re.search(r'(?s)\b' + re.escape(lvl) + r'\s*\{(.*?)\n\s*\}', desc)
        if m:
            entry = []
            for ln in m.group(1).replace('\r', '').split('\n'):
                ln = ln.split('//')[0].strip()
                if '=' in ln:
                    k, v = ln.split('=', 1)
                    if k.strip() not in DROP_KEYS:
                        entry.append((k.strip(), v.strip()))
        else:
            entry = [('friendlyName', '"%s"' % lvl), ('nameId', '"GAME_MULT_%s"' % lvl),
                     ('description', '""'), ('LoadingTexture', 'MP_01')] + DEFAULT_ENTRY
        pd = dict(entry)
        name_key, desc_key = unquote(pd.get('nameId', '')), unquote(pd.get('description', ''))
        # strings: name, description and the short GAME_MP_* name; PC-only languages fall back to English
        keys = [k for k in (name_key, desc_key) if k]
        if name_key.startswith('GAME_MULT_'):
            keys.append('GAME_MP_' + name_key[len('GAME_MULT_'):])
        strings = {}
        for lang in LANGS:
            src_lines = xstr.get(lang) or xstr.get('english') or {}
            d = {}
            for k in keys:
                if k in src_lines:
                    d[k] = re.sub(r' \(dlc\d+\)"$', ' (DLC)"', src_lines[k])
            if d:
                strings[lang] = d
        en = strings.get('english', {})
        title = unquote(en.get(name_key, pd.get('friendlyName', lvl)))
        entry = [(k, '"%s"' % title if k == 'friendlyName' else v) for k, v in entry]
        out_files = []
        # loading screen textures
        lt = unquote(pd.get('LoadingTexture', ''))
        loading = ''
        if lt and 'textures/%s.pct' % lt in files:
            loading = lt.lower()
            for suf in ('', '_small'):
                p = 'textures/%s%s.pct' % (lt, suf)
                if p in files:
                    out_files.append((loading + suf, 6, 'main', xbox.normalize_texture(src.read(p))))
            entry = [(k, loading if k == 'LoadingTexture' else v) for k, v in entry]
        elif lt and lt.lower() not in known_names:
            entry = [(k, 'MP_01' if k == 'LoadingTexture' else v) for k, v in entry]
        # level resources that the PC game does not have
        new_names = {n for n, _, _, _ in out_files}
        for n, t, d in xbox.read_s3dpak(src.read(lvl + '.s3dpak')):
            if t == 4 or not d or (n.lower(), t) in known_nt:
                continue                      # Xbox shader pairs / stream stubs / assets the PC already has
            if t in (6, 7):
                d = xbox.normalize_texture(d, t == 6)
                if t == 6 and xbox.tex_format(d) in (36, 37):
                    out_files.append((n, t, 'ati', d))
                    out_files.append((n, t, 'nv', xbox.to_nvidia(d)))
                    new_names.add(n.lower())
                    continue
            out_files.append((n, t, 'main', d))
            new_names.add(n.lower())
        # preload list in the PC layout, without references the PC game cannot resolve
        xs = dict(xbox.read_s3dlst(src.read(lvl + '.s3dlst')))
        ksec = self.known_sections
        dropped = []

        def keep(sec, items):
            res = []
            for it in items:
                low = it.lower()
                if low in new_names:
                    res.append(it)
                elif low in ksec.get(sec, {}):
                    res.append(ksec[sec][low])
                elif low in known_names:
                    res.append(known_names[low])
                else:
                    dropped.append(it)
            return res
        secs = [('SceneData', [lvl + '.data']), ('ShaderPairs', list(self.mp_shader_pairs))]
        for s in ('Textures', 'Cubemaps', 'Sounds', 'WaveBanks_mem', 'WaveBanks_strm_file', 'Templates'):
            secs.append((s, keep(s, xs.get(s, []))))
        secs.append(('VoiceSplines', ['vo_splines']))
        secs.append(('Ragdolls', keep('Ragdolls', xs.get('Ragdolls', []))))
        for s in ('Scene', 'SceneGrs', 'SceneScr', 'SceneTrc', 'SceneRain', 'SceneCDT', 'SceneSM', 'SceneSLO', 'SceneVis'):
            if s in xs:
                secs.append((s, xs[s]))
        out_files.insert(0, (lvl, 0, 'all', xbox.format_list(secs)))
        if dropped:
            log(_('  %s: dropped %d references to Xbox-only resources (%s ...)', lvl, len(dropped), ', '.join(dropped[:4])))
        manifest = {'class': lvl, 'title': title, 'source': 'Xbox 360: %s' % source_name if source_name else 'Xbox 360',
                    'entry': entry, 'strings': strings, 'loading': loading}
        return Bundle.write(out_path, manifest, out_files)

    # ---- export
    def export_map(self, cls, out_path, log=_noop):
        for m in self.library()['maps']:
            if m['class'].lower() == cls.lower():
                if not any(o['level'].lower() == cls.lower() for o in self.library()['overrides']):
                    shutil.copy2(os.path.join(self.lib_dir, m['file']), out_path)
                else:                                   # export with the edited entries
                    b = self.bundle(m)
                    files = []
                    for f, data in b.files():
                        edited = self.override_data(f['name'], f['type'], 'main' if f['arc'] == 'all' else f['arc'])
                        files.append((f['name'], f['type'], f['arc'], data if edited is None else edited))
                    Bundle.write(out_path, {k: v for k, v in b.manifest.items() if k not in ('files', 'format')}, files)
                log(_('Exported %s -> %s', cls, out_path))
                return out_path
        om = [m for m in self.orig_maps if m['class'].lower() == cls.lower()]
        if not om:
            raise MapToolError(_('unknown map %s', cls))
        m = om[0]
        cls = m['class']
        pd = dict(m['props'])
        entry = [(k, v) for k, v in m['props'] if k not in DROP_KEYS]
        keys = [unquote(pd.get('nameId', '')), unquote(pd.get('description', ''))]
        if keys[0].startswith('GAME_MULT_'):
            keys.append('GAME_MP_' + keys[0][len('GAME_MULT_'):])
        strings = {}
        for lang in LANGS:
            lines = string_lines(self.orig_strings(lang))
            d = {k: lines[k] for k in keys if k in lines}
            if d:
                strings[lang] = d
        files = []
        lst = self.orig['main'].get(cls, 0)
        if lst is None:
            raise MapToolError(_('%s has no preload list', cls))
        main_list = self.override_data(cls, 0) or self.read_orig('main', lst)
        files.append((cls, 0, 'all', main_list))
        for arc in ('nv', 'ati'):
            e = self.orig[arc].get(cls, 0)
            if e is not None:
                d = self.override_data(cls, 0, arc) or self.read_orig(arc, e)
                if d != main_list:       # vendor list differs: store it separately
                    files[0] = (cls, 0, 'main', main_list)
                    files.append((cls, 0, arc, d))
        secs = dict(xbox.parse_list_text(main_list[4:].decode('latin1')))
        names = [n for s, items in secs.items() if s.startswith('Scene') for n in items]
        loading = unquote(pd.get('LoadingTexture', '')).lower()
        for e in self.orig['main'].entries:
            low = e.name.lower()
            if (low in {n.lower() for n in names}
                    or (e.type == 6 and re.match(re.escape(cls.lower()) + r'_\d+_lm', low))
                    or (e.type == 6 and loading and low in (loading, loading + '_small'))):
                data = self.level_data(cls) if (low, e.type) == ((cls + '.lg').lower(), 16) else self.read_orig('main', e)
                files.append((e.name, e.type, 'main', data))
        manifest = {'class': cls, 'title': unquote(pd.get('friendlyName', cls)), 'source': 'TimeShift PC (original map)',
                    'entry': entry, 'strings': strings, 'loading': loading, 'requires': 'TimeShift PC base game assets'}
        Bundle.write(out_path, manifest, [(n, t, a, d) for n, t, a, d in files])
        log(_('Exported %s -> %s (%d files)', cls, out_path, len(files)))
        return out_path

    # ---- remove / hide
    def remove_map(self, cls, log=_noop):
        lib = self.library()
        for m in lib['maps']:
            if m['class'].lower() == cls.lower():
                lib['maps'].remove(m)
                self.save_library(lib)
                try:
                    os.remove(os.path.join(self.lib_dir, m['file']))
                except FileNotFoundError:
                    pass
                self.drop_level_override(m['class'])
                log(_('Removed %s', m['class']))
                return 'removed'
        if any(m['class'].lower() == cls.lower() for m in self.orig_maps if m['section'] == 'multiplayer'):
            if cls.lower() not in {h.lower() for h in lib['hidden']}:
                lib['hidden'].append(cls)
                self.save_library(lib)
            log(_('Hidden original map %s', cls))
            return 'hidden'
        raise MapToolError(_('unknown map %s', cls))

    def unhide_map(self, cls, log=_noop):
        lib = self.library()
        before = len(lib['hidden'])
        lib['hidden'] = [h for h in lib['hidden'] if h.lower() != cls.lower()]
        if len(lib['hidden']) == before:
            raise MapToolError(_('%s is not hidden', cls))
        self.save_library(lib)
        log(_('Restored original map %s', cls))

    # ---- apply / restore
    def game_running(self):
        try:
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, timeout=20,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return b'timeshift' in out.lower()

    def apply(self, log=_noop):
        if self.game_running():
            raise MapToolError(_('Close TimeShift first'))
        lib = self.library()
        if not lib['maps'] and not lib['hidden'] and not lib['overrides']:
            self.restore(log)
            return
        overrides = {}      # (arc, name, type) -> data
        for o in lib['overrides']:
            with open(os.path.join(self.lib_dir, o['file']), 'rb') as f:
                data = f.read()
            for arc in o.get('arcs', ['main']):
                overrides[(arc, o['name'].lower(), o['type'])] = data
        log(_('Building the map list ...'))
        ps = PsContainer(self.orig_ps)
        fi = ps.find(b'gs_map_list {')
        text = strip_dev_block(ps.text(fi))
        hidden = {h.lower() for h in lib['hidden']}
        for m in sorted(parse_maps(text), key=lambda m: -m['start']):
            if m['class'].lower() in hidden and m['section'] == 'multiplayer':
                text = text[:m['start']] + text[m['end']:]
        bundles = [(m, self.bundle(m)) for m in lib['maps']]
        blocks = ''.join(map_block_text(b.cls, m['id'], [tuple(x) for x in b.manifest['entry']]) for m, b in bundles)
        pos = section_close_line(text, 'multiplayer')
        text = text[:pos] + blocks + text[pos:]
        check = {m['class'] for m in parse_maps(text)}
        missing = [b.cls for _, b in bundles if b.cls not in check]
        if missing:
            raise MapToolError(_('map list check failed for %s', missing))
        ps.set_text(fi, text)
        replace = {arc: {} for arc in ARCS}
        replace['main'][('ps', 2)] = ps.build()
        log(_('Adding strings ...'))
        for lang in LANGS:
            s = self.orig_strings(lang)
            add = []
            for _m, b in bundles:
                st = b.manifest['strings'].get(lang) or b.manifest['strings'].get('english', {})
                add += ['%s\t\t\t\t%s' % (k, v) for k, v in st.items()]
            if add:
                if not s.endswith('\r\n'):
                    s += '\r\n'
                replace['main'][('strings_' + lang, 14)] = (s + '\r\n'.join(add) + '\r\n').encode('utf-16le')
        used = set()
        for (arc, *key), data in overrides.items():
            if tuple(key) in self.orig[arc].by_key:        # edited entry of an original map
                replace[arc][tuple(key)] = data
                used.add((arc, *key))
        lists = {k: [] for k in ARCS}
        extra = {k: [] for k in ARCS}
        seen = set()
        for m, b in bundles:
            log(_('Packing %s ...', b.cls))
            for f, data in b.files():
                arcs = list(ARCS) if f['arc'] == 'all' else [f['arc']]
                for arc in arcs:
                    key = (arc, f['name'].lower(), f['type'])
                    if key in seen or (f['name'].lower(), f['type']) in self.orig[arc].by_key:
                        continue
                    seen.add(key)
                    okey = (arc, f['name'].lower(), f['type'])
                    fdata = overrides.get(okey, data)          # edited entry of an added map
                    if okey in overrides:
                        used.add(okey)
                    (lists if f['type'] == 0 else extra)[arc].append((f['name'], f['type'], fdata))
        for key in sorted({k[1] for k in set(overrides) - used}):
            log(_('Warning: edited file %s is not used by any map', key))
        tmps = {}
        try:
            for arc, fn in ARCS.items():
                log(_('Writing %s ...', fn))
                tmp = os.path.join(self.paks, fn + '.tmp')
                s3darc.write_archive(self.orig[arc], tmp, replace=replace[arc],
                                     insert_after_type0=lists[arc], append=extra[arc])
                tmps[fn] = tmp
            log(_('Verifying ...'))
            for tmp in tmps.values():
                s3darc.verify_archive(tmp)
            for fn, tmp in tmps.items():
                os.replace(tmp, os.path.join(self.paks, fn))
            tmps = {}
        finally:
            for tmp in tmps.values():
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self._write_applied(lib)
        log(_('Done: %d maps added, %d hidden', len(lib['maps']), len(lib['hidden'])))
        if lib['overrides']:
            log(_('Edited levels: %s', ', '.join(dict.fromkeys(o['level'] for o in lib['overrides']))))

    def restore(self, log=_noop):
        if self.game_running():
            raise MapToolError(_('Close TimeShift first'))
        self.ensure_backup(log)
        for fn in ARCS.values():
            shutil.copy2(os.path.join(self.backup, fn), os.path.join(self.paks, fn))
        self._write_applied({'maps': [], 'hidden': [], 'overrides': []})
        log(_('Original archives restored'))

    def _write_applied(self, lib):
        with open(self.applied_file, 'w', encoding='utf-8') as f:
            json.dump({'maps': [[m['class'], m['id']] for m in lib['maps']], 'hidden': lib['hidden'],
                       'overrides': sorted([o['name'], o['sha1']] for o in lib.get('overrides', [])),
                       'time': datetime.datetime.now().isoformat(timespec='seconds')}, f, indent=1)

    def verify(self, log=_noop):
        for fn in ARCS.values():
            n = s3darc.verify_archive(os.path.join(self.paks, fn))
            log(_('%s: OK (%d entries checked)', fn, n))
        a = s3darc.Archive(os.path.join(self.paks, ARCS['main']))
        try:
            ps = PsContainer(a.read(a.get('ps', 2), base_dir=self.paks))
            for i in range(len(ps.recs)):
                if sum(v for _, v in ps.recs[i][1]) != len(zlib.decompress(ps.streams[i])):
                    raise MapToolError(_('ps file %d is inconsistent', i))
            maps = parse_maps(ps.text(ps.find(b'gs_map_list {')))
        finally:
            a.close()
        mp = [m for m in maps if m['section'] == 'multiplayer']
        log(_('Map list: %d multiplayer maps installed', len(mp)))
        return mp


def _preview(tex):
    if not tex:
        return None
    try:
        r = xbox.decode_texture_rgb(tex)
    except (ValueError, struct.error):
        return None
    return xbox.png_bytes(*r) if r else None
