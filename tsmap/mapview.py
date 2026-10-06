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
import math
import re
import struct
import zlib

from .lg import TEXT, _cstr

IDENT = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
LIGHT = (-0.45, 0.8, 0.4)
PREVIEW_FACES = 12000             # faces drawn while the 3D camera moves
HIDDEN_FLAGS ={'vis', 'nc', 'plrc', 'aiv', 'aic', 'ainv', 'ainc'}          # direction to the light: from the upper left of the view


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


class MapObject:
    """kind: zone, node, start, pickup, vehicle, sound, effect, object, light."""
    __slots__ = ('kind', 'name', 'template', 'pos', 'outline', 'texts', 'height', 'matrix', 'mpos')

    def __init__(self, kind, name, template, pos, outline=None):
        self.kind, self.name, self.template, self.pos, self.outline = kind, name, template, pos, outline
        self.texts = []            # indices of LevelFile.objects (property texts) that belong to this object
        self.height = None         # zones: DOMAIN { height } above the flat outline, None when not set
        self.matrix = None         # instances: the 4x4 matrix of the record (16 floats) ...
        self.mpos = None           # ... and its offset in the level file (it can be changed in place)

    @property
    def movable(self):
        return self.mpos is not None


def _kids(c):
    return {k.tag: k for k in c.children or ()}


def _is_node(c):
    return c.tag == 0xf0 and c.children and c.children[0].tag == 0xf4


class LevelView:
    def __init__(self, lf):
        self.lf = lf
        d = lf.data
        self.objects = []
        self.tris = []             # (y max, y min, x0, z0, x1, z1, x2, z2, light, y mean), view coordinates X/Z
        self.vx, self.vy, self.vz = [], [], []      # all vertices, for the 3D view
        self.faces = []            # (a, b, c, y min, light, y mean): vertex indices, front side counter-clockwise
        self._colors = None
        self._preview = None
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
                self.objects.append(o)

        # instances
        for c, name, tpl, cls, m, mpos in instance_records(lf):
            ref = tpl or cls
            kind = ('start' if name.startswith('start_pos') else 'vehicle' if name.startswith('veh_')
                    else 'pickup' if name.startswith('item_') or ref.startswith('item_')     # item_mp_*: weapons
                    else 'sound' if name.startswith(('sndActor', 'music')) or ref.startswith('snd_')
                    else 'effect' if (not ref and '|' in name                    # s3d_refLocator12|flareActor
                                      or ref.startswith('sob_flare') or 'sfx' in ref)
                    else 'object')
            o = MapObject(kind, name, ref, m[12:15])
            o.matrix, o.mpos = m, mpos
            o.texts = [text_index[t] for t in c.children or () if t.tag == TEXT and t in text_index]
            proto = protos.get(tpl)
            if proto is not None:
                pts = []
                for n in _walk_nodes(proto):
                    k = _kids(n)
                    verts = [_apply(m, v) for v in self._verts(k)]
                    if verts and _solid(_cstr(d[k[0x115].start:k[0x115].end]) if 0x115 in k else ''):
                        self._add_mesh(verts, k)
                        pts += [(v[0], v[2]) for v in verts]
                o.outline = _hull(pts) if pts else None
                o.texts += [i for t, i in text_index.items() if _inside(t, proto)]
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
        lx, ly, lz = LIGHT
        ll = math.sqrt(lx * lx + ly * ly + lz * lz)
        nv = len(verts)
        base = len(self.vx)
        self.vx.extend(v[0] for v in verts)
        self.vy.extend(v[1] for v in verts)
        self.vz.extend(v[2] for v in verts)
        for a, b, c in struct.iter_unpack('<3H', d[tc.start + 4:tc.end]):
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

    def render(self, view, w, h, cut=None):
        """Height-shaded top view of `view` = (x0, z0, x1, z1) at w x h pixels, triangles drawn from low to high.
        `cut` hides triangles that are entirely above that height. Returns (rgb bytearray, heights array)."""
        x0, z0, x1, z1 = view
        sx, sz = w / (x1 - x0), h / (z1 - z0)
        rgb = bytearray(b'\x1c\x1f\x24' * (w * h))
        hgt = array.array('f', [-1e9]) * (w * h)
        span = max(self.ymax - self.ymin, 1.0)
        for t in self.tris:
            if cut is not None and t[1] > cut:
                continue
            px = ((t[2] - x0) * sx, (t[4] - x0) * sx, (t[6] - x0) * sx)
            py = ((z1 - t[3]) * sz, (z1 - t[5]) * sz, (z1 - t[7]) * sz)
            if max(px) < 0 or min(px) >= w or max(py) < 0 or min(py) >= h:
                continue
            light = t[8]
            col = bytes(min(255, int(c * light)) for c in _ramp((t[9] - self.ymin) / span))
            _fill(rgb, hgt, array.array('f', [t[9]]), w, h, px, py, col)
        return rgb, hgt

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

    def render3d(self, cam, w, h, cut=None, preview=False):
        """Perspective view from Camera `cam` at w x h pixels: back faces culled, the rest drawn from far to near
        (painter's algorithm). `preview` draws only the largest faces (while the camera moves).
        Returns (rgb bytearray, face index per pixel array, -1 = empty; the index is valid without preview)."""
        (ex, ey, ez), (rx, ry, rz), (ux, uy, uz), (fx, fy, fz) = cam.basis()
        foc = cam.focal(w)
        cx, cy = w / 2, h / 2
        near = 0.1
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
        bg = (0x1c, 0x1f, 0x24)
        fog = cam.dist * 4 + 50
        todo = []
        for i in range(len(faces)):
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
        for depth, i, px, py in todo:
            k = min(0.7, depth / 3 / fog)
            col = bytes(int(v + (g - v) * k) for v, g in zip(colors[i], bg))
            _fill(rgb, ids, array.array('i', [i]), w, h, px, py, col)
        return rgb, ids

    def floor_at(self, x, z, below):
        """Height of the highest surface at (x, z) that is not above `below`, None when there is none."""
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


def instance_records(lf):
    """Instance records (0x1b9) of a LevelFile: (chunk, name, template, class, matrix, matrix offset)."""
    d = lf.data
    for c in lf.chunks:
        if c.tag != 0x1b9:
            continue
        raw = d[c.start:c.start + c.pre] if c.children else d[c.start:c.end]
        if not raw.startswith(b'SNIA'):
            continue
        parts = raw[4:].split(b'\0', 3)               # 'SNIA' name\0 tpl\0 cls\0 \0 + 16 floats
        if len(parts) < 4 or len(parts[3]) < 65:
            continue
        name, tpl, cls = (p.decode('latin1') for p in parts[:3])
        yield (c, name, tpl, cls, struct.unpack_from('<16f', parts[3], 1),
               c.start + 4 + sum(len(p) + 1 for p in parts[:3]) + 1)


def matrix_yaw(m):
    """Heading of an instance in degrees: its local Z axis (row 2), 0 = +Z, as the camera yaw."""
    return math.degrees(math.atan2(m[8], m[10])) % 360


def move_matrix(m, pos, turn=0.0):
    """Matrix m moved to `pos` and turned by `turn` degrees around the vertical axis (tilt and scale kept)."""
    a = math.radians(turn)
    c, s = math.cos(a), math.sin(a)
    out = list(m)
    for row in range(3):
        x, y, z = m[row * 4:row * 4 + 3]
        out[row * 4:row * 4 + 3] = (x * c + z * s, y, -x * s + z * c)
    out[12:15] = pos
    return tuple(out)


def apply_moves(data, moves):
    """Level data with the instance matrices {offset: 16 floats} written in place (the size does not change)."""
    out = bytearray(data)
    for pos, m in moves.items():
        if out[pos - 1] != 0:
            raise ValueError('not an instance matrix at %d' % pos)
        struct.pack_into('<16f', out, pos, *m)
    return bytes(out)


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


def _solid(flags):
    """Geometry worth drawing: not a domain, not the sky (&vis), not a mesh without collision (&nc: light cones,
    decals, distant scenery), not a transparent one (&tra_N, &trp_N) and not an invisible helper (&plrc player
    clip, &aiv / &aic / &ainv / &ainc AI blockers)."""
    if flags.startswith('&dom_'):
        return False
    f = set(flags.replace(' ', '').split('&'))
    return not (f & HIDDEN_FLAGS or any(x.startswith(('tra', 'trp')) for x in f))


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


def png_base64(rgb, w, h):
    """RGB pixels -> PNG as base64 text (what tk.PhotoImage(data=...) takes)."""
    raw = b''.join(b'\0' + bytes(rgb[3 * w * y:3 * w * (y + 1)]) for y in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b''))
    return base64.b64encode(png)
