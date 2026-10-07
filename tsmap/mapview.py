"""Top view of a level (.lg): the level geometry as a shaded height map plus the positions of its objects.

Where the positions come from:
  scene nodes   0xf0: 0xf1 vertices (u32 n + n x 3f, world space), 0xf2 triangles (u32 n + n x 3 u16),
                0x11d bounding box, 0xf9 4x4 matrix relative to the parent node, 0x115 flags
  instances     0x1b9 'SNIA' name tpl cls + 4x4 matrix; when tpl names a prototype of section 0x1ea
                ('LPTA' name), the prototype's meshes are placed with the instance matrix
  lights        0x280: 0x282 name, 0x283 4x4 matrix
Matrices are row-vector (D3D style): the translation is at floats 12..14. The view looks down the Y axis,
X to the right and Z up.
"""
import array
import base64
import collections
import math
import re
import struct
import zlib

from . import native
from .lg import TEXT, Chunk, _cstr, _parse, _seq

IDENT = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
LIGHT = (-0.45, 0.8, 0.4)          # direction to the light: from the upper left of the view
PREVIEW_FACES = 12000             # faces drawn while the 3D camera moves (Python drawing only)
CANCEL_EVERY = 2048               # the Python drawing asks `cancel` after so many triangles
BG = (0x1c, 0x1f, 0x24)           # background of the images
HIDDEN_FLAGS = {'vis', 'nc', 'plrc', 'aiv', 'aic', 'ainv', 'ainc'}
EFFECT_HIDDEN = {'vis', 'plrc', 'aiv', 'aic', 'ainv', 'ainc'}    # not even with "Transparent and effects"
# instance flags (chunk 0x1bb after the record): spawn points by team, CTF flag positions, game mode filters
SPAWN_FLAGS = ('swMP_SP_NONTEAM', 'swMP_SP_TEAM1', 'swMP_SP_TEAM2')
FLAG_POS = ('swMP_RED_FLAG_POS', 'swMP_BLUE_FLAG_POS')
MODES = ('DM', 'TDM', 'CTF', 'KOT', 'STM', '1V1')           # notIN_<mode>: the object is not there in that mode


def _mul(a, b):
    return tuple(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4))


def _apply(m, v):
    x, y, z = v
    return (x * m[0] + y * m[4] + z * m[8] + m[12], x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


RAMP = ((0.0, (52, 84, 140)), (0.35, (70, 150, 140)), (0.65, (190, 190, 110)), (1.0, (250, 225, 190)))


def _ramp(k):
    """Colour of a relative height 0..1: blue (low) - green - yellow - light (high)."""
    k = min(1.0, max(0.0, k))
    for (a, ca), (b, cb) in zip(RAMP, RAMP[1:]):
        if k <= b:
            f = (k - a) / (b - a)
            return tuple(x + (y - x) * f for x, y in zip(ca, cb))
    return RAMP[-1][1]


def _hull(points):
    """Convex hull of 2D points (monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    lo, hi = half(pts), half(reversed(pts))
    return lo[:-1] + hi[:-1]


ZoneGeom = collections.namedtuple('ZoneGeom', 'verts vert_off normals normal_off box_off centre axis size')
ZoneGeom.__doc__ = """A zone as loaded, for moving it in place: its vertices (world space) and their offset in the level file
(chunk 0xf1 after the count), the normals (0x110, None when missing), the bounding box (0x11d after its count:
6 floats), the centre of the vertices, around which the zone turns, and its own axes: the direction of its width
(radians from +X towards +Z, the side of the smallest rectangle around it that is nearest to X) and its (width,
length) along them.

The matrix of a zone holds in rows 0 and 2 the 2x2 map of its (x, z) as loaded (row vectors: p' = p * M, the
part P that stretches it along its axes, then the turn R: M = P R) and in row 3 where its centre goes."""


class MapObject:
    """kind: zone, node, start, flag, pickup, vehicle, sound, effect, object, light."""
    __slots__ = ('kind', 'name', 'template', 'pos', 'outline', 'texts', 'height', 'matrix', 'mpos', 'flags',
                 'record', 'new', 'deleted', 'catalog', 'geom')

    def __init__(self, kind, name, template, pos, outline=None):
        self.kind, self.name, self.template, self.pos, self.outline = kind, name, template, pos, outline
        self.texts = []            # indices of LevelFile.objects (property texts) that belong to this object
        self.height = None         # zones: DOMAIN { height } above the flat outline, None when not set
        self.matrix = None         # instances: the 4x4 matrix of the record (16 floats) ...
        self.mpos = None           # ... and its offset in the level file (it can be changed in place)
        self.flags = ()            # instances: flag names of chunk 0x1bb
        self.record = None         # instances: the Instance it was read from (the one copied for a new object)
        self.new = False           # added on the map, not saved yet
        self.deleted = False       # deleted on the map, not saved yet
        self.catalog = False       # new object from the catalog: its resources go to the preload list
        self.geom = None           # zones: ZoneGeom; their matrix is a turn around the vertical axis at the centre
                                   # (identity as loaded), mpos the offset of the vertex chunk (the key of a move)

    @property
    def movable(self):
        return self.mpos is not None or self.new


def _kids(c):
    return {k.tag: k for k in c.children or ()}


def _is_node(c):
    return c.tag == 0xf0 and c.children and c.children[0].tag == 0xf4


class LevelView:
    def __init__(self, lf, templates=None):
        """`templates` (TemplateMeshes) draws the instances whose template is not a prototype of the level
        (boxes, barrels, vehicles, plants ...); without it they are markers only. The transparent and
        non-colliding geometry (effects, glass, light cones) goes to `fx`, drawn by with_effects()."""
        self.lf = lf
        self.templates = templates
        d = lf.data
        fx = self.fx = LevelView.__new__(LevelView)
        fx.lf, fx.vx, fx.vy, fx.vz, fx.faces, fx.tris = lf, [], [], [], [], []
        self._fx_view = None
        self.objects = []
        self.tris = []             # (y max, y min, x0, z0, x1, z1, x2, z2, light, y mean), view coordinates X/Z
        self.vx, self.vy, self.vz = [], [], []      # all vertices, for the 3D view
        self.faces = []            # (a, b, c, y min, light, y mean): vertex indices, front side counter-clockwise
        self._colors = None
        self._preview = None
        self._packed = {}          # arrays for the C drawing (tsmap/native), built on first use
        text_index = {o.chunk: o.index for o in lf.objects}
        sections = {c.tag: c for c in lf.top}

        # prototypes of section 0x1ea: name -> record chunk
        protos = {}
        for rec in (sections[0x1ea].children if 0x1ea in sections else ()):
            if rec.tag != 0x2e4:
                continue
            head = [k for k in rec.children or () if k.tag == 0x2e5]
            if head:
                raw = d[head[0].start:head[0].end]
                protos[_cstr(raw[4:] if raw.startswith(b'LPTA') else raw)] = rec

        # scene: meshes and nodes with a text or a domain flag
        if 0xf0 in sections:
            for c in _walk_nodes(sections[0xf0]):
                k = _kids(c)
                name = _cstr(d[k[0xf4].start:k[0xf4].end])
                flags = _cstr(d[k[0x115].start:k[0x115].end]) if 0x115 in k else ''
                verts = self._verts(k)
                if verts and _solid(flags):
                    self._add_mesh(verts, k)
                elif verts and _effect(flags):
                    fx._add_mesh(verts, k)
                tc = k.get(0xfd)
                texts = [text_index[t] for t in (tc.children or ()) if t.tag == TEXT and t in text_index] if tc else []
                if not texts and not flags.startswith('&dom_'):
                    continue
                if verts:
                    pos = tuple((min(v[i] for v in verts) + max(v[i] for v in verts)) / 2 for i in range(3))
                    outline = _hull([(v[0], v[2]) for v in verts])
                else:
                    pos, outline = self._node_matrix(c)[12:15], None
                o = MapObject('zone' if flags.startswith('&dom_') else 'node', name, flags.lstrip('&'), pos, outline)
                o.texts = texts
                if o.kind == 'zone':
                    o.height = zone_height(' '.join(lf.objects[i].text for i in texts))
                    o.geom = _zone_geom(d, k, verts, pos)
                    if o.geom is not None:
                        o.mpos = k[0xf1].start
                        o.matrix = IDENT[:12] + (*pos, 1.0)
                self.objects.append(o)

        # instances
        for rec in instance_records(lf):
            c, name, tpl, m = rec.chunk, rec.name, rec.tpl, rec.matrix
            o = MapObject(instance_kind(name, tpl, rec.cls, rec.flags), name, tpl or rec.cls, m[12:15])
            o.matrix, o.mpos, o.flags, o.record = m, rec.mpos, rec.flags, rec
            o.texts = [text_index[t] for t in c.children or () if t.tag == TEXT and t in text_index]
            proto = protos.get(tpl)
            if proto is not None:
                pts, fx_pts = [], []
                for n in _walk_nodes(proto):
                    k = _kids(n)
                    chain = _mul(node_chain(d, n, proto), m)
                    verts = [_apply(chain, v) for v in self._verts(k)]
                    flags = _cstr(d[k[0x115].start:k[0x115].end]) if 0x115 in k else ''
                    if verts and _solid(flags):
                        self._add_mesh(verts, k)
                        pts += [(v[0], v[2]) for v in verts]
                    elif verts and _effect(flags):
                        fx._add_mesh(verts, k)
                        fx_pts += [(v[0], v[2]) for v in verts]
                # the footprint: of the solid parts, of the transparent ones when it has nothing else (an effect)
                o.outline = _hull(pts or fx_pts) if pts or fx_pts else None
                o.texts += [i for t, i in text_index.items() if _inside(t, proto)]
            elif templates is not None and instance_kind(name, tpl, rec.cls, rec.flags) not in ('effect', 'sound'):
                pts, fx_pts = [], []
                for verts, tris in templates.get(tpl, rec.cls):
                    wv = [_apply(m, v) for v in verts]
                    self._add_faces(wv, tris)
                    pts += [(v[0], v[2]) for v in wv]
                for verts, tris in templates.effects(tpl, rec.cls):
                    wv = [_apply(m, v) for v in verts]
                    fx._add_faces(wv, tris)
                    fx_pts += [(v[0], v[2]) for v in wv]
                o.outline = _hull(pts or fx_pts) if pts or fx_pts else None
            self.objects.append(o)

        # lights
        if 0x280 in sections:
            name = None
            for k in sections[0x280].children:
                if k.tag == 0x282:
                    name = _cstr(d[k.start:k.end])
                elif k.tag == 0x283 and k.end - k.start == 64:
                    m = struct.unpack_from('<16f', d, k.start)
                    self.objects.append(MapObject('light', (name or 'light').split('|')[-1], '', m[12:15]))

        self.tris.sort(key=lambda t: t[0])
        self.by_text = {}
        for o in self.objects:
            for i in o.texts:
                self.by_text.setdefault(i, o)
        # height range for the colours: the bulk of the playable area, not the far scenery
        x0, z0, x1, z1 = self.bounds()
        ys = sorted(t[9] for t in self.tris if x0 <= t[2] <= x1 and z0 <= t[3] <= z1)
        self.ymin, self.ymax = (ys[len(ys) // 50], ys[-1 - len(ys) // 50]) if ys else (0.0, 1.0)
        self.yrange = (ys[0], ys[-1]) if ys else (0.0, 1.0)       # for the height cut

    # ---- geometry
    def _verts(self, k):
        vc, tc = k.get(0xf1), k.get(0xf2)
        if vc is None or tc is None:
            return []
        d = self.lf.data
        n = struct.unpack_from('<I', d, vc.start)[0]
        if 4 + 12 * n != vc.end - vc.start:
            return []
        return list(struct.iter_unpack('<3f', d[vc.start + 4:vc.end]))

    def _add_mesh(self, verts, k):
        d = self.lf.data
        tc = k[0xf2]
        n = struct.unpack_from('<I', d, tc.start)[0]
        if 4 + 6 * n != tc.end - tc.start:
            return
        self._add_faces(verts, list(struct.iter_unpack('<3H', d[tc.start + 4:tc.end])))

    def outline_of(self, record, m):
        """Footprint of an instance record placed with matrix m (its template mesh), None when unknown."""
        if self.templates is None:
            return None
        meshes = self.templates.get(record.tpl, record.cls) or self.templates.effects(record.tpl, record.cls)
        pts = [(_apply(m, v)[0], _apply(m, v)[2]) for verts, tris in meshes for v in verts]
        return _hull(pts) if pts else None

    def with_effects(self):
        """The view with the transparent and non-colliding geometry too (the "Transparent and effects" switch):
        a view of its own over the same objects, made on first use. The floor (floor_at) stays the solid one."""
        fx = self.fx
        if not fx.faces:
            return self
        if self._fx_view is None:
            v = LevelView.__new__(LevelView)
            v.__dict__.update(self.__dict__)
            n = len(self.vx)
            v.vx, v.vy, v.vz = self.vx + fx.vx, self.vy + fx.vy, self.vz + fx.vz
            v.faces = self.faces + [(f[0] + n, f[1] + n, f[2] + n) + f[3:] for f in fx.faces]
            v.tris = sorted(self.tris + fx.tris, key=lambda t: t[0])
            v._colors = v._tcolors = v._preview = None
            v._packed = {}
            v._fx_view = v
            self._fx_view = v
        return self._fx_view

    def _add_faces(self, verts, tris):
        lx, ly, lz = LIGHT
        ll = math.sqrt(lx * lx + ly * ly + lz * lz)
        nv = len(verts)
        base = len(self.vx)
        self.vx.extend(v[0] for v in verts)
        self.vy.extend(v[1] for v in verts)
        self.vz.extend(v[2] for v in verts)
        for a, b, c in tris:
            if a >= nv or b >= nv or c >= nv:
                continue
            p, q, r = verts[a], verts[b], verts[c]
            ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
            vx, vy, vz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl < 1e-9:
                continue
            ys = (p[1], q[1], r[1])
            lit = (nx * lx + ny * ly + nz * lz) / (nl * ll)     # the winding matches the vertex normals (0x110)
            self.faces.append((base + a, base + b, base + c, min(ys), 0.35 + 0.65 * max(0.0, lit), sum(ys) / 3))
            if ny < 0:
                nx, ny, nz = -nx, -ny, -nz          # winding is not reliable for a top view
            light = 0.45 + 0.55 * max(0.0, (nx * lx + ny * ly + nz * lz) / (nl * ll))
            self.tris.append((max(ys), min(ys), p[0], p[2], q[0], q[2], r[0], r[2], light, sum(ys) / 3))

    def _node_matrix(self, c):
        m = IDENT
        while c is not None:
            if _is_node(c):
                k = _kids(c)
                if 0xf9 in k:
                    m = _mul(m, struct.unpack_from('<16f', self.lf.data, k[0xf9].start))
            c = c.parent
        return m

    def bounds(self):
        """(x0, z0, x1, z1) of the playable area: start points and pickups, with a margin."""
        pts = [(o.pos[0], o.pos[2]) for o in self.objects if o.kind in ('start', 'pickup', 'vehicle')]
        if len(pts) < 2:
            pts = [(t[2], t[3]) for t in self.tris] or [(-50.0, -50.0), (50.0, 50.0)]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
        m = max(10.0, 0.2 * max(x1 - x0, z1 - z0))
        return x0 - m, z0 - m, x1 + m, z1 + m

    def default_cut(self):
        """A height that shows every floor with start points or pickups, without the roofs above them."""
        ys = [o.pos[1] for o in self.objects if o.kind in ('start', 'pickup', 'vehicle')]
        return max(ys) + 3.0 if ys else None

    def render(self, view, w, h, cut=None, cancel=None):
        """Height-shaded top view of `view` = (x0, z0, x1, z1) at w x h pixels, triangles drawn from low to high.
        `cut` hides triangles that are entirely above that height. Returns (rgb bytearray, heights array), None
        when `cancel()` turned true meanwhile (asked by the slow Python drawing only)."""
        x0, z0, x1, z1 = view
        sx, sz = w / (x1 - x0), h / (z1 - z0)
        rgb = bytearray(bytes(BG) * (w * h))
        hgt = array.array('f', [-1e9]) * (w * h)
        if native.lib is not None:
            tris, colors = self._packed2d()
            native.render2d(tris, colors, len(self.tris), x0, z1, sx, sz, cut, w, h, rgb, hgt)
            return rgb, hgt
        colors = self._tri_colors()
        for n, (t, col) in enumerate(zip(self.tris, colors)):
            if cancel is not None and n % CANCEL_EVERY == 0 and cancel():
                return None
            if cut is not None and t[1] > cut:
                continue
            px = ((t[2] - x0) * sx, (t[4] - x0) * sx, (t[6] - x0) * sx)
            py = ((z1 - t[3]) * sz, (z1 - t[5]) * sz, (z1 - t[7]) * sz)
            lo, hi = min(px), max(px)
            if hi < 0 or lo >= w:
                continue
            top, bottom = min(py), max(py)
            if bottom < 0 or top >= h:
                continue
            if hi - lo < 1 and bottom - top < 1:          # smaller than a pixel (detail of an object): one pixel
                i = int(top) * w + int(lo)
                if 0 <= lo and 0 <= top:
                    rgb[3 * i:3 * i + 3] = col
                    hgt[i] = t[9]
                continue
            _fill(rgb, hgt, array.array('f', [t[9]]), w, h, px, py, col)
        return rgb, hgt

    def _tri_colors(self):
        """Colour bytes of the top view triangles (height ramp times the light), computed once."""
        if getattr(self, '_tcolors', None) is None:
            span = max(self.ymax - self.ymin, 1.0)
            self._tcolors = [bytes(min(255, int(c * t[8])) for c in _ramp((t[9] - self.ymin) / span))
                             for t in self.tris]
        return self._tcolors

    def _packed2d(self):
        """Top view triangles for the C drawing: 8 doubles each (y min, x0, z0, x1, z1, x2, z2, y mean) and the
        colours, 3 bytes each."""
        if '2d' not in self._packed:
            height = array.array('d', [t[9] for t in self.tris])
            colors = native.shade(array.array('d', [t[8] for t in self.tris]), height, self.ymin,
                                  max(self.ymax - self.ymin, 1.0), RAMP)
            self._packed['2d'] = (array.array('d', [v for t in self.tris for v in (t[1],) + t[2:8] + (t[9],)]),
                                  colors)
        return self._packed['2d']

    def _packed3d(self):
        """The mesh for the C drawing: vertices (3 double arrays), faces (3 ints each), y min of every face
        (doubles) and the colours (3 bytes each)."""
        if '3d' not in self._packed:
            if self._colors is None:
                colors = native.shade(array.array('d', [f[4] for f in self.faces]),
                                      array.array('d', [f[5] for f in self.faces]), self.ymin,
                                      max(self.ymax - self.ymin, 1.0), RAMP)
            else:                                  # set by the caller (mesh_thumbnail)
                colors = bytearray(b''.join(bytes(c) for c in self._colors))
            self._packed['3d'] = (array.array('d', self.vx), array.array('d', self.vy), array.array('d', self.vz),
                                  array.array('i', [v for f in self.faces for v in f[:3]]),
                                  array.array('d', [f[3] for f in self.faces]), colors)
        return self._packed['3d']

    def prepare(self):
        """Build what the drawing needs ahead of time (in the thread that made the view): the arrays for the C
        drawing, or the top view colours for the Python one."""
        if native.lib is not None:
            self._packed2d()
            self._packed3d()
        else:
            self._tri_colors()
    # ---- 3D view
    def face_colors(self):
        """Colour of every face: height ramp times the light (the camera does not change it)."""
        if self._colors is None:
            span = max(self.ymax - self.ymin, 1.0)
            self._colors = [tuple(min(255, int(c * f[4])) for c in _ramp((f[5] - self.ymin) / span))
                            for f in self.faces]
        return self._colors

    def prepare_3d(self):
        """Build the face colours and the preview mesh ahead of time (they take a moment on big levels)."""
        if native.lib is not None:
            self._packed3d()
        else:
            self._preview_mesh()

    def _preview_mesh(self):
        """The PREVIEW_FACES largest faces with their own vertex lists: (vx, vy, vz, faces, colors)."""
        if self._preview is None:
            vx, vy, vz = self.vx, self.vy, self.vz

            def area(f):
                a, b, c = f[:3]
                ux, uy, uz = vx[b] - vx[a], vy[b] - vy[a], vz[b] - vz[a]
                wx, wy, wz = vx[c] - vx[a], vy[c] - vy[a], vz[c] - vz[a]
                return (uy * wz - uz * wy) ** 2 + (uz * wx - ux * wz) ** 2 + (ux * wy - uy * wx) ** 2
            colors = self.face_colors()
            keep = sorted(range(len(self.faces)), key=lambda i: area(self.faces[i]), reverse=True)[:PREVIEW_FACES]
            keep.sort()
            remap, pvx, pvy, pvz, faces = {}, [], [], [], []
            for i in keep:
                f = self.faces[i]
                idx = []
                for v in f[:3]:
                    if v not in remap:
                        remap[v] = len(pvx)
                        pvx.append(vx[v])
                        pvy.append(vy[v])
                        pvz.append(vz[v])
                    idx.append(remap[v])
                faces.append(tuple(idx) + f[3:])
            self._preview = (pvx, pvy, pvz, faces, [colors[i] for i in keep])
        return self._preview

    def render3d(self, cam, w, h, cut=None, preview=False, cancel=None):
        """Perspective view from Camera `cam` at w x h pixels: back faces culled, the rest drawn from far to near
        (painter's algorithm); the C drawing also keeps a depth buffer, so a big triangle (the ground under the
        map) does not cover nearer ones. `preview` draws only the largest faces (while the camera moves) when the
        drawing is done in Python; the C drawing is fast enough to draw them all.
        Returns (rgb bytearray, face index per pixel array, -1 = empty; the index is valid without preview), None
        when `cancel()` turned true meanwhile (asked by the slow Python drawing only)."""
        (ex, ey, ez), (rx, ry, rz), (ux, uy, uz), (fx, fy, fz) = cam.basis()
        foc = cam.focal(w)
        cx, cy = w / 2, h / 2
        near = 0.1
        bg = BG
        fog = cam.dist * 4 + 50
        if native.lib is not None:
            rgb = bytearray(bytes(bg) * (w * h))
            ids = array.array('i', [-1]) * (w * h)
            if self.faces:
                vx, vy, vz, faces, ylo, colors = self._packed3d()
                basis = array.array('d', (ex, ey, ez, rx, ry, rz, ux, uy, uz, fx, fy, fz, foc))
                native.render3d(vx, vy, vz, faces, ylo, colors, basis, cut, fog, bytearray(bg), w, h, rgb, ids)
            return rgb, ids
        if preview:
            vx, vy, vz, faces, colors = self._preview_mesh()
        else:
            vx, vy, vz, faces, colors = self.vx, self.vy, self.vz, self.faces, self.face_colors()
        # vertices to screen space (None behind the near plane)
        X = [(x - ex) * rx + (z - ez) * rz for x, z in zip(vx, vz)]
        Y = [(x - ex) * ux + (y - ey) * uy + (z - ez) * uz for x, y, z in zip(vx, vy, vz)]
        Z = [(x - ex) * fx + (y - ey) * fy + (z - ez) * fz for x, y, z in zip(vx, vy, vz)]
        SX = [cx + x * foc / z if z > near else None for x, z in zip(X, Z)]
        SY = [cy - y * foc / z if z > near else None for y, z in zip(Y, Z)]
        todo = []
        for i in range(len(faces)):
            if cancel is not None and i % CANCEL_EVERY == 0 and cancel():
                return None
            a, b, c, ylo = faces[i][:4]
            if cut is not None and ylo > cut:
                continue
            ax, bx, qx = SX[a], SX[b], SX[c]
            if ax is None or bx is None or qx is None:
                continue
            ay, by, qy = SY[a], SY[b], SY[c]
            if (bx - ax) * (qy - ay) - (by - ay) * (qx - ax) <= 0:
                continue                                         # back face
            if max(ax, bx, qx) < 0 or min(ax, bx, qx) >= w or max(ay, by, qy) < 0 or min(ay, by, qy) >= h:
                continue
            todo.append((Z[a] + Z[b] + Z[c], i, (ax, bx, qx), (ay, by, qy)))
        todo.sort(reverse=True)
        rgb = bytearray(bytes(bg) * (w * h))
        ids = array.array('i', [-1]) * (w * h)
        shades = {}                                       # (colour, fog step) -> bytes
        for n, (depth, i, px, py) in enumerate(todo):
            if cancel is not None and n % CANCEL_EVERY == 0 and cancel():
                return None
            step = min(16, int(depth / 3 / fog / 0.7 * 16))   # the fog in 16 steps, up to 70 %
            key = (colors[i], step)
            col = shades.get(key)
            if col is None:
                k = 0.7 * step / 16
                col = shades[key] = bytes(int(v + (g - v) * k) for v, g in zip(colors[i], bg))
            lo, hi, top, bottom = min(px), max(px), min(py), max(py)
            if hi - lo < 1 and bottom - top < 1:          # smaller than a pixel: one pixel
                if 0 <= lo and 0 <= top:
                    j = int(top) * w + int(lo)
                    rgb[3 * j:3 * j + 3] = col
                    ids[j] = i
                continue
            _fill(rgb, ids, array.array('i', [i]), w, h, px, py, col)
        return rgb, ids

    def floor_at(self, x, z, below):
        """Height of the highest surface at (x, z) that is not above `below`, None when there is none."""
        if native.lib is not None:
            if not self.faces:
                return None
            vx, vy, vz, faces = self._packed3d()[:4]
            return native.floor_at(vx, vy, vz, faces, x, z, below)
        vx, vy, vz = self.vx, self.vy, self.vz
        best = None
        for a, b, c in (f[:3] for f in self.faces):
            x0, x1, x2 = vx[a], vx[b], vx[c]
            if (x < x0 and x < x1 and x < x2) or (x > x0 and x > x1 and x > x2):
                continue
            z0, z1, z2 = vz[a], vz[b], vz[c]
            if (z < z0 and z < z1 and z < z2) or (z > z0 and z > z1 and z > z2):
                continue
            den = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
            if abs(den) < 1e-9:
                continue                                   # a vertical face
            l0 = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / den
            l1 = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / den
            if l0 < -1e-6 or l1 < -1e-6 or l0 + l1 > 1 + 1e-6:
                continue
            y = l0 * vy[a] + l1 * vy[b] + (1 - l0 - l1) * vy[c]
            if y <= below and (best is None or y > best):
                best = y
        return best

    def pick(self, cam, w, h, ids, px, py):
        """World point under pixel (px, py) of a render3d image, or None."""
        if not (0 <= px < w and 0 <= py < h):
            return None
        i = ids[py * w + px]
        if i < 0:
            return None
        a, b, c = self.faces[i][:3]
        p = (self.vx[a], self.vy[a], self.vz[a])
        u = (self.vx[b] - p[0], self.vy[b] - p[1], self.vz[b] - p[2])
        v = (self.vx[c] - p[0], self.vy[c] - p[1], self.vz[c] - p[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        e, d = cam.ray(w, h, px + 0.5, py + 0.5)
        den = sum(n[k] * d[k] for k in range(3))
        if abs(den) < 1e-12:
            return None
        t = sum(n[k] * (p[k] - e[k]) for k in range(3)) / den
        return tuple(e[k] + d[k] * t for k in range(3))


class Camera:
    """Orbit camera: looks at `target` from `dist` metres; yaw 0 looks along +Z, pitch 90 straight down
    (then X is to the right and Z up, as in the top view)."""

    def __init__(self, target, yaw=0.0, pitch=45.0, dist=40.0, fov=60.0):
        self.target, self.yaw, self.pitch, self.dist, self.fov = tuple(target), yaw, pitch, dist, fov

    def basis(self):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        f = (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))
        r = (math.cos(y), 0.0, -math.sin(y))                            # up x forward, horizontal
        u = (f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0])
        e = tuple(t - fv * self.dist for t, fv in zip(self.target, f))
        return e, r, u, f

    def focal(self, w):
        return w / 2 / math.tan(math.radians(self.fov) / 2)

    def project(self, w, h, point):
        """(screen x, screen y, depth) of a world point, None behind the camera."""
        (ex, ey, ez), r, u, f = self.basis()
        d = (point[0] - ex, point[1] - ey, point[2] - ez)
        z = d[0] * f[0] + d[1] * f[1] + d[2] * f[2]
        if z <= 0.1:
            return None
        foc = self.focal(w)
        return (w / 2 + (d[0] * r[0] + d[2] * r[2]) * foc / z,
                h / 2 - (d[0] * u[0] + d[1] * u[1] + d[2] * u[2]) * foc / z, z)

    def ray(self, w, h, px, py):
        """(eye, direction) through a screen point."""
        e, r, u, f = self.basis()
        foc = self.focal(w)
        a, b = (px - w / 2) / foc, -(py - h / 2) / foc
        return e, tuple(f[k] + r[k] * a + u[k] * b for k in range(3))

    def orbit(self, dyaw, dpitch, pivot=None):
        """Turn by dyaw / dpitch degrees around a world point (the target when None): the eye and the target
        move around the pivot together, so the pivot stays where it is on the screen."""
        pitch = min(89.0, max(-20.0, self.pitch + dpitch))
        dpitch = pitch - self.pitch
        if pivot is None:
            pivot = self.target
        e = self.basis()[0]
        a, p = math.radians(dyaw), math.radians(dpitch)
        ny = math.radians(self.yaw + dyaw)
        r = (math.cos(ny), 0.0, -math.sin(ny))          # the right axis after the turn around Y

        def turn(v):
            x, y, z = (v[k] - pivot[k] for k in range(3))
            x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)     # around Y: yaw
            c, s = math.cos(p), math.sin(p)                                                  # around r: pitch
            cross = (r[1] * z - r[2] * y, r[2] * x - r[0] * z, r[0] * y - r[1] * x)
            dot = r[0] * x + r[1] * y + r[2] * z
            return tuple(pivot[k] + (x, y, z)[k] * c + cross[k] * s + r[k] * dot * (1 - c) for k in range(3))
        self.target, e = turn(self.target), turn(e)
        self.yaw, self.pitch = (self.yaw + dyaw) % 360, pitch
        self.dist = math.dist(self.target, e)

    def ground(self, w, h, px, py, height):
        """Where the ray through a screen point meets the horizontal plane Y = height, None if it does not."""
        e, d = self.ray(w, h, px, py)
        if abs(d[1]) < 1e-9:
            return None
        t = (height - e[1]) / d[1]
        return tuple(e[k] + d[k] * t for k in range(3)) if t > 0 else None

    def pan(self, dx, dy, w):
        """Move the target by a screen offset in pixels, along the ground."""
        _, r, u, f = self.basis()
        m = self.dist / self.focal(w)
        n = math.hypot(f[0], f[2]) or 1.0
        fwd = (f[0] / n, 0.0, f[2] / n)
        self.target = tuple(t - r[k] * dx * m + fwd[k] * dy * m for k, t in enumerate(self.target))


Instance = collections.namedtuple('Instance', 'chunk name tpl cls matrix mpos fchunk flags rest children')
Instance.__doc__ = """An instance record of section 0x1b8: the record chunk 0x1b9 ('SNIA' name tpl cls \\0 + 16 floats,
sometimes a property text 0x1ba inside) and the flags chunk 0x1bb that follows it. `mpos` is the offset of the
matrix in the file, `flags` the flag names, `rest` the record bytes after the names (matrix + anything after it),
`children` the record's child chunks as (tag, payload)."""


class _Sections:
    """Just the top sections a LevelFile-like reader needs (instance_records): parsing only these is fast."""

    def __init__(self, data, tags):
        self.data = data
        self.top, self.chunks = [], []
        for tag, a, b in _seq(data, 0, len(data)) or ():
            c = Chunk()
            c.tag, c.start, c.end, c.pre, c.children, c.parent = tag, a, b, 0, None, None
            if tag in tags:
                sub = _parse(data, a, b, c, 14)        # the records and their children only
                if sub is not None:
                    c.pre, c.children = sub
            self.top.append(c)
        stack = list(reversed(self.top))
        while stack:
            c = stack.pop()
            self.chunks.append(c)
            stack.extend(reversed(c.children or ()))


def scan_instances(data):
    """Instances of a level and the names of its prototypes (section 0x1ea), without parsing the whole file."""
    lf = _Sections(data, (0x1b8, 0x1ea))
    protos = set()
    for c in lf.top:
        if c.tag == 0x1ea:
            for rec in c.children or ():
                for k in rec.children or ():
                    if k.tag == 0x2e5:
                        raw = data[k.start:k.end]
                        protos.add(_cstr(raw[4:] if raw.startswith(b'LPTA') else raw))
    return list(instance_records(lf)), protos


def instance_records(lf):
    """Instance records (0x1b9) of a LevelFile, as Instance."""
    d = lf.data
    for c in lf.chunks:
        if c.tag != 0x1b9:
            continue
        raw = d[c.start:c.start + c.pre] if c.children else d[c.start:c.end]
        if not raw.startswith(b'SNIA'):
            continue
        parts = raw[4:].split(b'\0', 3)               # 'SNIA' name\0 tpl\0 cls\0 \0 + 16 floats
        if len(parts) < 4 or len(parts[3]) < 65 or any(k.children for k in c.children or ()):
            continue
        name, tpl, cls = (p.decode('latin1') for p in parts[:3])
        sib = c.parent.children if c.parent is not None else lf.top
        i = sib.index(c)
        fchunk = sib[i + 1] if i + 1 < len(sib) and sib[i + 1].tag == 0x1bb else None
        flags = tuple(t for t in _cstr(d[fchunk.start:fchunk.end]).split('&') if t) if fchunk else ()
        yield Instance(c, name, tpl, cls, struct.unpack_from('<16f', parts[3], 1),
                       c.start + 4 + sum(len(p) + 1 for p in parts[:3]) + 1, fchunk, flags, parts[3],
                       [(k.tag, d[k.start:k.end]) for k in c.children or ()])


def flags_payload(flags):
    """Payload of a flags chunk 0x1bb: '&a&b' + NUL, or a single NUL."""
    return ('&' + '&'.join(flags) if flags else '').encode('latin1') + b'\0'


def edit_instances(lf, adds=(), deletes=(), flags=None, texts=None):
    """Level bytes with instances added / deleted and flags changed, all by instance name:
        adds     [(new name, Instance to copy, matrix, flags)]  appended to the end of section 0x1b8
        deletes  {name}
        flags    {name: flags}
        texts    {name: text} a property text (and script) for an instance that has none: its record gets
                 a text chunk 0x1ba, as the records of the game with a text have
    The record count at the start of section 0x1b8 follows."""
    recs = {r.name: r for r in instance_records(lf)}
    for name, *_rest in adds:
        if name in recs:
            raise ValueError('instance %s exists already' % name)
    section = next((c for c in lf.top if c.tag == 0x1b8), None)
    if section is None or section.pre != 4:
        raise ValueError('unexpected instance section layout')
    payloads, delete = {}, set()
    for name, fl in (flags or {}).items():
        r = recs.get(name)
        if r is not None and r.fchunk is not None and name not in deletes:
            payloads[r.fchunk] = flags_payload(fl)
    for name in deletes:
        r = recs.get(name)
        if r is None:
            raise ValueError('instance %s not found' % name)
        delete.add(r.chunk)
        if r.fchunk is not None:
            delete.add(r.fchunk)
    replace = {}
    for name, text in (texts or {}).items():
        if name in deletes:
            continue
        r = recs.get(name)
        if r is None:
            raise ValueError('instance %s not found' % name)
        if any(tag == TEXT for tag, _p in r.children):
            raise ValueError('instance %s has a text already' % name)
        body = text.replace('\r\n', '\n').replace('\n', '\r\n').encode('latin1')   # UnicodeEncodeError: not Latin
        if b'\0' in body:
            raise ValueError('text must not contain NUL characters')
        c = r.chunk
        head = lf.data[c.start:c.end] if c.children is None else lf.data[c.start:c.start + c.pre]
        replace[c] = (0x1b9, head, list(r.children) + [(TEXT, body + b'\0')])
    new = []
    for name, src, m, fl in adds:
        head = (b'SNIA' + b'\0'.join(s.encode('latin1') for s in (name, src.tpl, src.cls)) + b'\0'
                + src.rest[:1] + struct.pack('<16f', *m) + src.rest[65:])
        new.append((0x1b9, head, list(src.children)) if src.children else (0x1b9, head))
        new.append((0x1bb, flags_payload(fl)))
    keep = [k for k in section.children if k not in delete]
    count = sum(1 for k in keep if k.tag == 0x1b9) + len(adds)
    insert = {}
    if new:
        if not keep:
            raise ValueError('cannot add to an empty instance section')
        insert[keep[-1]] = new
    prefix = struct.pack('<I', count)
    return lf.rebuild(payloads=payloads, prefixes={section: prefix}, insert_after=insert, delete=delete,
                      replace=replace)


def unique_name(base, taken):
    """A free instance name: start_posN keeps the numbering, others get _2, _3 ..."""
    m = re.fullmatch(r'(start_pos)(\d+)', base) or re.fullmatch(r'(.*?)_(\d+)', base)
    stem = m.group(1) + ('' if base.startswith('start_pos') else '_') if m else base + '_'
    n = int(m.group(2)) + 1 if m else 2
    while stem + str(n) in taken:
        n += 1
    return stem + str(n)


def instance_kind(name, tpl, cls, flags):
    ref = tpl or cls
    if name.startswith('start_pos') or any(f in SPAWN_FLAGS for f in flags):
        return 'start'
    if any(f in FLAG_POS for f in flags):
        return 'flag'
    if name.startswith('veh_') or ref.lower().startswith('vhc_') or ref.lower() == 'bike':
        return 'vehicle'
    if name.startswith('item_') or ref.startswith('item_'):     # item_mp_*: weapons
        return 'pickup'
    if name.startswith(('sndActor', 'music')) or ref.startswith('snd_'):
        return 'sound'
    if not ref and '|' in name or ref.startswith('sob_flare') or 'sfx' in ref:   # s3d_refLocator12|flareActor
        return 'effect'
    return 'object'


def matrix_yaw(m):
    """Heading of an instance in degrees: its local Z axis (row 2), 0 = +Z, as the camera yaw."""
    return math.degrees(math.atan2(m[8], m[10])) % 360


def matrix_scale(m):
    """(x, y, z) scale of an instance: the lengths of the rows of its matrix (its local axes)."""
    return tuple(math.sqrt(m[r * 4] ** 2 + m[r * 4 + 1] ** 2 + m[r * 4 + 2] ** 2) for r in range(3))


def scale_matrix(m, scale):
    """Matrix m scaled to (x, y, z) along the object's own axes (its direction and place are kept). The game's
    levels have scaled instances too (lamp flares 0.71, boxes stretched 1.29 along one axis)."""
    out = list(m)
    for r, (old, new) in enumerate(zip(matrix_scale(m), scale)):
        k = new / old if old > 1e-9 else 0.0
        out[r * 4:r * 4 + 3] = [v * k for v in m[r * 4:r * 4 + 3]]
    return tuple(out)


def move_matrix(m, pos, turn=0.0):
    """Matrix m moved to `pos` and turned by `turn` degrees around the vertical axis (tilt and scale kept; for a
    zone its stretch, see ZoneGeom)."""
    a = math.radians(turn)
    c, s = math.cos(a), math.sin(a)
    out = list(m)
    for row in range(3):
        x, y, z = m[row * 4:row * 4 + 3]
        out[row * 4:row * 4 + 3] = (x * c + z * s, y, -x * s + z * c)
    out[12:15] = pos
    return tuple(out)


def apply_moves(data, moves):
    """Level data with the instance matrices {offset: 16 floats} written in place (the size does not change);
    a bytes value is written as it is (the vertices, normals and box of a zone, see zone_patches)."""
    out = bytearray(data)
    for pos, m in moves.items():
        if isinstance(m, (bytes, bytearray)):
            if pos + len(m) > len(out):
                raise ValueError('patch past the end at %d' % pos)
            out[pos:pos + len(m)] = m
            continue
        if out[pos - 1] != 0:
            raise ValueError('not an instance matrix at %d' % pos)
        struct.pack_into('<16f', out, pos, *m)
    return bytes(out)


def _zone_geom(d, k, verts, centre):
    """ZoneGeom of a zone node (children by tag), None when it cannot be moved in place."""
    if not verts or 0x11d not in k or k[0x11d].end - k[0x11d].start != 28:
        return None
    n = len(verts)
    nc = k.get(0x110)
    normals = None
    if nc is not None and nc.end - nc.start == 4 + 12 * n:
        normals = tuple(struct.iter_unpack('<3f', d[nc.start + 4:nc.end]))
    axis, size = _zone_axes([(v[0], v[2]) for v in verts])
    return ZoneGeom(tuple(verts), k[0xf1].start + 4, normals, nc.start + 4 if normals else None, k[0x11d].start + 4,
                    tuple(centre), axis, size)


def _zone_axes(points):
    """(axis, (width, length)) of the smallest rectangle around 2D points: one of its sides is a side of the
    convex hull; the axis is turned by a multiple of 90 degrees to be the side nearest to X."""
    hull = _hull(points)
    best = (0.0, (max(p[0] for p in points) - min(p[0] for p in points),
                  max(p[1] for p in points) - min(p[1] for p in points)))
    if len(hull) >= 3:
        area = None
        for (ax, az), (bx, bz) in zip(hull, hull[1:] + hull[:1]):
            if (ax, az) == (bx, bz):
                continue
            a = math.atan2(bz - az, bx - ax)
            u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
            pu = [x * u[0] + z * u[1] for x, z in points]
            pv = [x * v[0] + z * v[1] for x, z in points]
            ext = (max(pu) - min(pu), max(pv) - min(pv))
            if area is None or ext[0] * ext[1] < area - 1e-9:
                area, best = ext[0] * ext[1], (a, ext)
    a, (w, l) = best
    while a > math.pi / 4 + 1e-9:            # the side nearest to X is the width
        a, w, l = a - math.pi / 2, l, w
    while a <= -math.pi / 4 + 1e-9:
        a, w, l = a + math.pi / 2, l, w
    return a, (w, l)


def _lin(m):
    """The 2x2 map of a zone's (x, z) in its matrix: ((m0, m2), (m8, m10))."""
    return (m[0], m[2]), (m[8], m[10])


def _polar(m):
    """M = P R of a zone matrix: P symmetric (the stretch), R the turn; (P, R) as 2x2 tuples."""
    (a, b), (c, e) = _lin(m)
    # P = sqrt(M M^T); for a 2x2 positive definite S: sqrt(S) = (S + sqrt(det S) I) / sqrt(trace S + 2 sqrt(det S))
    s11, s12, s22 = a * a + b * b, a * c + b * e, c * c + e * e
    r = math.sqrt(max(s11 * s22 - s12 * s12, 0.0))
    t = math.sqrt(max(s11 + s22 + 2 * r, 1e-18))
    p11, p12, p22 = (s11 + r) / t, s12 / t, (s22 + r) / t
    det = p11 * p22 - p12 * p12
    if abs(det) < 1e-12:
        return ((1.0, 0.0), (0.0, 1.0)), ((1.0, 0.0), (0.0, 1.0))
    i11, i12, i22 = p22 / det, -p12 / det, p11 / det
    rot = ((i11 * a + i12 * c, i11 * b + i12 * e), (i12 * a + i22 * c, i12 * b + i22 * e))
    return ((p11, p12), (p12, p22)), rot


def zone_shape(geom, m):
    """(width, length, angle in degrees) of a zone with matrix m: its size along its own axes and its turn since
    it was loaded (the angle field of the edit bar)."""
    (p11, p12), (_p21, p22) = _polar(m)[0]
    u = (math.cos(geom.axis), math.sin(geom.axis))
    v = (-u[1], u[0])
    su = u[0] * u[0] * p11 + 2 * u[0] * u[1] * p12 + u[1] * u[1] * p22
    sv = v[0] * v[0] * p11 + 2 * v[0] * v[1] * p12 + v[1] * v[1] * p22
    rot = _polar(m)[1]
    return geom.size[0] * su, geom.size[1] * sv, math.degrees(math.atan2(rot[1][0], rot[1][1])) % 360


def zone_matrix(geom, m, width, length):
    """Matrix m with the zone stretched to width x length along its own axes (the turn and place are kept)."""
    rot = _polar(m)[1]
    sx = width / geom.size[0] if geom.size[0] > 1e-6 else 1.0
    sz = length / geom.size[1] if geom.size[1] > 1e-6 else 1.0
    u = (math.cos(geom.axis), math.sin(geom.axis))
    v = (-u[1], u[0])
    p11 = sx * u[0] * u[0] + sz * v[0] * v[0]
    p12 = sx * u[0] * u[1] + sz * v[0] * v[1]
    p22 = sx * u[1] * u[1] + sz * v[1] * v[1]
    out = list(m)
    out[0], out[2] = p11 * rot[0][0] + p12 * rot[1][0], p11 * rot[0][1] + p12 * rot[1][1]
    out[8], out[10] = p12 * rot[0][0] + p22 * rot[1][0], p12 * rot[0][1] + p22 * rot[1][1]
    return tuple(out)


def zone_points(geom, m, points):
    """World points of a zone as loaded, mapped by its matrix m (stretched and turned around its centre, moved)."""
    (a, b), (c, e) = _lin(m)
    cx, cy, cz = geom.centre
    return [(m[12] + (x - cx) * a + (z - cz) * c, y + m[13] - cy, m[14] + (x - cx) * b + (z - cz) * e)
            for x, y, z in points]


def zone_patches(geom, m):
    """{offset: bytes} that put a zone where matrix m says, written in place: the vertices (0xf1), the normals
    (0x110, turned) and the bounding box (0x11d: 1 cm around the vertices, as in the game's levels). The zone stays
    in the level's spatial grid (0x21f) where it was; the game finds a moved zone anyway (a moved bot spawner
    triggered at its new place)."""
    verts = [struct.unpack('<3f', struct.pack('<3f', *v)) for v in zone_points(geom, m, geom.verts)]
    out = {geom.vert_off: b''.join(struct.pack('<3f', *v) for v in verts)}
    if geom.normal_off is not None:
        (a, b), (c, e) = _polar(m)[1]
        out[geom.normal_off] = b''.join(struct.pack('<3f', x * a + z * c, y, x * b + z * e)
                                        for x, y, z in geom.normals)
    lo = [min(v[i] for v in verts) - 0.01 for i in range(3)]
    hi = [max(v[i] for v in verts) + 0.01 for i in range(3)]
    out[geom.box_off] = struct.pack('<6f', *lo, *hi)
    return out


def zone_vertices(lf):
    """{name: [vertices]} of the zones of a level (the first one of a name)."""
    d = lf.data
    out = {}
    for top in lf.top:
        if top.tag != 0xf0:
            continue
        for c in _walk_nodes(top):
            k = _kids(c)
            flags = _cstr(d[k[0x115].start:k[0x115].end]) if 0x115 in k else ''
            vc = k.get(0xf1)
            if not flags.startswith('&dom_') or vc is None:
                continue
            n = struct.unpack_from('<I', d, vc.start)[0]
            if 4 + 12 * n == vc.end - vc.start:
                out.setdefault(_cstr(d[k[0xf4].start:k[0xf4].end]),
                               list(struct.iter_unpack('<3f', d[vc.start + 4:vc.end])))
    return out


def zone_matrix_to(geom, verts):
    """The matrix of a zone that puts its loaded vertices onto `verts` (the same zone moved, turned or stretched:
    the best fit by least squares), None when they do not match."""
    if len(verts) != len(geom.verts) or len(verts) < 2:
        return None
    n = len(verts)
    cx, cy, cz = geom.centre
    d = [(x - cx, z - cz) for x, _y, z in geom.verts]
    o = [(x, z) for x, _y, z in verts]
    dm = (sum(p[0] for p in d) / n, sum(p[1] for p in d) / n)
    om = (sum(p[0] for p in o) / n, sum(p[1] for p in o) / n)
    D = [(p[0] - dm[0], p[1] - dm[1]) for p in d]
    W = [(p[0] - om[0], p[1] - om[1]) for p in o]
    # M = (D^T D)^-1 D^T W (row vectors: w = d M)
    a11 = sum(p[0] * p[0] for p in D)
    a12 = sum(p[0] * p[1] for p in D)
    a22 = sum(p[1] * p[1] for p in D)
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        return None                          # a line: the turn is not known
    b = [[sum(p[i] * q[j] for p, q in zip(D, W)) for j in range(2)] for i in range(2)]
    m00 = (a22 * b[0][0] - a12 * b[1][0]) / det
    m01 = (a22 * b[0][1] - a12 * b[1][1]) / det
    m10 = (-a12 * b[0][0] + a11 * b[1][0]) / det
    m11 = (-a12 * b[0][1] + a11 * b[1][1]) / det
    m = list(IDENT)
    m[0], m[2], m[8], m[10] = m00, m01, m10, m11
    m[12] = om[0] - (dm[0] * m00 + dm[1] * m10)
    m[14] = om[1] - (dm[0] * m01 + dm[1] * m11)
    m[13] = cy + sum(v[1] - g[1] for v, g in zip(verts, geom.verts)) / n
    return tuple(m)


def zone_height(text):
    """A zone is a flat outline (all its vertices have one Y); its height is DOMAIN { height = N } in the
    property text. None when the text does not set it (the game then uses its own default)."""
    m = re.search(r'(?s)\bDOMAIN\s*\{[^{}]*?\bheight\s*=\s*(-?\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None


def _fill(rgb, buf, val, w, h, px, py, col):
    """Fill a screen triangle: rgb gets the colour, buf (an array) the one-element array val."""
    (x0, y0), (x1, y1), (x2, y2) = sorted(zip(px, py), key=lambda p: p[1])
    r0 = math.floor(y0)
    r1 = math.floor(y2)
    if r0 < 0:
        r0 = 0
    if r1 > h - 1:
        r1 = h - 1
    if y2 == y0:                             # completely flat: one row, the whole width
        xs = sorted((x0, x1, x2))
        da = db = dl = 0.0
    else:
        # slopes of the long edge (top-bottom) and the two short ones
        dl = (x2 - x0) / (y2 - y0)
        da = (x1 - x0) / (y1 - y0) if y1 > y0 else 0.0
        db = (x2 - x1) / (y2 - y1) if y2 > y1 else 0.0
    for row in range(r0, r1 + 1):
        # the row centre, kept inside the triangle: its first and last rows and triangles thinner than a row
        # (a wall seen edge-on) still get the pixels they touch
        yc = row + 0.5
        if yc < y0:
            yc = y0
        elif yc > y2:
            yc = y2
        if y2 == y0:
            lo, hi = xs[0], xs[2]
        else:
            lo = x0 + (yc - y0) * dl
            if yc < y1 or (yc == y1 and y1 > y0):
                hi = x0 + (yc - y0) * da
            elif y2 > y1:
                hi = x1 + (yc - y1) * db
            else:                            # flat top edge
                hi = x1
            if lo > hi:
                lo, hi = hi, lo
        c0 = int(lo) if lo > 0 else 0
        c1 = int(hi) if hi < w - 1 else w - 1
        if c0 > c1:
            continue
        n = c1 - c0 + 1
        base = row * w
        rgb[3 * (base + c0):3 * (base + c1 + 1)] = col * n
        buf[base + c0:base + c1 + 1] = val * n


PART_HIDDEN = {'glr', 'plrc', 'aiv', 'aic', 'ainv', 'ainc', 'vis', 'h'}    # collision hulls, AI helpers, emitters


def _visible_part(flags):
    """A part of a template worth drawing: not a collision hull, helper or transparent effect (the boost flame
    of the bike is a 40 m cone). Unlike the level, &nc parts count (leaves of plants)."""
    if flags.startswith('&dom_'):
        return False
    f = set(flags.replace(' ', '').split('&'))
    return not (f & PART_HIDDEN or any(x.startswith(('tra', 'trp')) for x in f))


def _effect_part(flags):
    """A transparent part of a template (an effect, glass, the boost flame of the bike), not a hull or helper."""
    if flags.startswith('&dom_'):
        return False
    f = set(flags.replace(' ', '').split('&'))
    return not (f & PART_HIDDEN) and any(x.startswith(('tra', 'trp')) for x in f)


def node_chain(d, node, stop):
    """Matrix from a node's space to the space of `stop` (a template / prototype record): the 0xf9 matrices of
    the node and its parents (row vectors, child first). Mesh vertices are in their node's space; in the scene of a
    level the chains of mesh nodes are identity (the vertices are world), in templates they are not (the wheels of
    the bike)."""
    m, p = IDENT, node
    while p is not None and p is not stop:
        if _is_node(p):
            k = _kids(p)
            if 0xf9 in k and k[0xf9].end - k[0xf9].start == 64:
                m = _mul(m, struct.unpack_from('<16f', d, k[0xf9].start))
        p = p.parent
    return m


def node_mesh(d, k):
    """(vertices, triangles) of a scene node (its children by tag), None without a valid mesh. The triangles are
    0xf2 (u32 n + n x 3 u16) or, in some templates (metal containers, carts, the monitor), 0x106 with one 0xff
    chunk of 3 u16 per triangle."""
    vc, tc = k.get(0xf1), k.get(0xf2)
    if vc is None or (tc is None and 0x106 not in k):
        return None
    nv = struct.unpack_from('<I', d, vc.start)[0]
    if 4 + 12 * nv != vc.end - vc.start:
        return None
    if tc is not None:
        nt = struct.unpack_from('<I', d, tc.start)[0]
        if 4 + 6 * nt != tc.end - tc.start:
            return None
        tris = struct.iter_unpack('<3H', d[tc.start + 4:tc.end])
    else:
        tris = (struct.unpack_from('<3H', d, t.start) for t in k[0x106].children or ()
                if t.tag == 0xff and t.end - t.start == 6)
    verts = list(struct.iter_unpack('<3f', d[vc.start + 4:vc.end]))
    return verts, [t for t in tris if max(t) < nv]


class TemplateMeshes:
    """Meshes of templates (archive entries of type 12, the same 0x2e4 records as the prototypes of a level),
    in the template's own space: {name: [(vertices, triangles)]}, read once."""

    def __init__(self, game, lock=None):
        self.game, self.lock, self.cache = game, lock, {}
        self.arc = game.orig['main']

    def get(self, *names):
        """Meshes of the first name that is a template (an instance gives its template, then its class)."""
        return self._parts(names)[0]

    def effects(self, *names):
        """The transparent parts of that template (effects, glass), drawn by "Transparent and effects"."""
        return self._parts(names)[1]

    def _parts(self, names):
        for name in names:
            if not name:
                continue
            key = name.lower()
            if key not in self.cache:
                self.cache[key] = self._load(key)
            if self.cache[key] is not None:
                return self.cache[key]
        return [], []

    def _load(self, name):
        e = self.arc.get(name, 12)
        if e is None:
            return None
        try:
            if self.lock is not None:
                with self.lock:
                    d = self.game.read_orig('main', e)
            else:
                d = self.game.read_orig('main', e)
            r = _parse(d, 0, len(d), None, 0)
        except Exception:                 # noqa: BLE001 - an unreadable template is drawn as a marker
            return None
        solid, fx = [], []
        for top in (r[1] if r else ()):
            for n in _walk_nodes(top):
                k = _kids(n)
                flags = _cstr(d[k[0x115].start:k[0x115].end]) if 0x115 in k else ''
                mesh = node_mesh(d, k)
                if not mesh:
                    continue
                part = solid if _visible_part(flags) else fx if _effect_part(flags) else None
                if part is not None:
                    m = node_chain(d, n, top)
                    if m != IDENT:
                        mesh = ([_apply(m, v) for v in mesh[0]], mesh[1])
                    part.append(mesh)
        return solid, fx


def template_meshes(game, lock=None):
    """The TemplateMeshes of this game (kept between maps)."""
    t = getattr(game, '_template_meshes', None)
    if t is None:
        t = game._template_meshes = TemplateMeshes(game, lock)
    return t


def mesh_thumbnail(meshes, w, h, yaw=35.0, pitch=25.0):
    """A small shaded picture of template meshes (RGB bytes), None when there is nothing to draw."""
    v = LevelView.__new__(LevelView)
    v.vx, v.vy, v.vz, v.faces, v.tris, v._preview, v._packed = [], [], [], [], [], None, {}
    for verts, tris in meshes:
        v._add_faces(verts, tris)
    if not v.faces:
        return None
    lo = [min(c) for c in (v.vx, v.vy, v.vz)]
    hi = [max(c) for c in (v.vx, v.vy, v.vz)]
    centre = tuple((a + b) / 2 for a, b in zip(lo, hi))
    size = math.dist(lo, hi) or 1.0
    v._colors = [tuple(int(c * f[4]) for c in (205, 200, 185)) for f in v.faces]
    cam = Camera(centre, yaw, pitch, size * 1.5, fov=50.0)
    rgb, _ids = v.render3d(cam, w, h)
    return rgb


def _solid(flags):
    """Geometry worth drawing: not a domain, not the sky (&vis), not a mesh without collision (&nc: light cones,
    decals, distant scenery), not a transparent one (&tra_N, &trp_N) and not an invisible helper (&plrc player
    clip, &aiv / &aic / &ainv / &ainc AI blockers)."""
    if flags.startswith('&dom_'):
        return False
    f = set(flags.replace(' ', '').split('&'))
    return not (f & HIDDEN_FLAGS or any(x.startswith(('tra', 'trp')) for x in f))


def _effect(flags):
    """Geometry that _solid leaves out but "Transparent and effects" draws: transparent (&tra_N, &trp_N) and
    non-colliding (&nc) meshes - effects such as the time fields, glass, light cones, decals; not the sky (&vis),
    domains or invisible helpers."""
    if flags.startswith('&dom_'):
        return False
    f = set(flags.replace(' ', '').split('&'))
    return not (f & EFFECT_HIDDEN) and ('nc' in f or any(x.startswith(('tra', 'trp')) for x in f))


def _walk_nodes(c):
    """Named scene nodes under chunk c (c included)."""
    stack = [c]
    while stack:
        n = stack.pop()
        if _is_node(n):
            yield n
        stack.extend(reversed([k for k in n.children or () if k.tag in (0xf0, 0x2e4)]))


def _inside(c, ancestor):
    while c is not None:
        if c is ancestor:
            return True
        c = c.parent
    return False


def ppm(rgb, w, h):
    """RGB pixels -> binary PPM: tk.PhotoImage(data=...) reads it several times faster than a PNG."""
    return b'P6 %d %d 255\n' % (w, h) + bytes(rgb)


def png_base64(rgb, w, h):
    """RGB pixels -> PNG as base64 text (what tk.PhotoImage(data=...) takes)."""
    raw = b''.join(b'\0' + bytes(rgb[3 * w * y:3 * w * (y + 1)]) for y in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b''))
    return base64.b64encode(png)
