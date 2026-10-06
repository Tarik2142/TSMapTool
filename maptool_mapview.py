"""Map window: top view or 3D view of the level loaded on the Scripts tab; its objects are linked to the object list."""
import math
import threading
import time
import tkinter as tk
from tkinter import ttk

from tsmap import _
from tsmap.mapview import Camera, LevelView, png_base64

COLORS = {'start': '#4aa8ff', 'pickup': '#60d060', 'vehicle': '#ff9a2e', 'object': '#e8c84a', 'node': '#d080ff',
          'sound': '#9a9a9a', 'effect': '#b8a878', 'light': '#fff3a8'}
ZONE_COLORS = {'dom_ai': '#ff5a5a', 'dom_spawn': '#ff9a2e', 'dom_time': '#c080ff', 'dom_kill': '#ff3030',
               'dom_snd': '#7f97b0'}
LAYER_OF = {'zone': 'zones', 'start': 'players', 'pickup': 'players', 'vehicle': 'players', 'object': 'objects',
            'node': 'objects', 'sound': 'effects', 'effect': 'effects', 'light': 'lights'}
PREVIEW_SCALE = 3          # the 3D preview is drawn at 1/3 of the window size and zoomed
# movement keys (Windows virtual key codes): W A S D, arrows, Q E, Shift
MOVE_KEYS = {87: 'fwd', 38: 'fwd', 83: 'back', 40: 'back', 65: 'left', 37: 'left', 68: 'right', 39: 'right',
             81: 'down', 69: 'up', 16: 'fast'}


def zone_color(o):
    return ZONE_COLORS.get(o.template.split('&')[0], '#a0a0a0')


class MapWindow(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.app = tab.app
        self.cls = None
        self.view = None           # LevelView
        self.cx = self.cz = 0.0    # 2D: world point in the middle of the canvas
        self.s = 0.0               # 2D: pixels per metre (0: not fitted yet)
        self.heights = None        # 2D: (x0, z1, scale, w, h, heights) of the last rendered image
        self.cam = None            # 3D: Camera
        self.picks = None          # 3D: (camera state, w, h, face ids) of the last full image
        self.photo = None
        self.selected = None
        self._pending = None       # object list index to mark once the view is ready
        self.gen = 0
        self._job = None
        self._preview_job = None
        self._drag = None
        self._keys = set()         # movement keys held down
        self._move_job = None
        self._move_time = 0.0
        self.title(_('Map'))
        self.geometry('980x800')
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        bar = ttk.Frame(self, padding=(6, 6))
        bar.pack(fill='x')
        self.mode3d = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='3D', variable=self.mode3d, command=self._toggle_3d).pack(side='left', padx=(0, 12))
        ttk.Label(bar, text=_('Height cut:')).pack(side='left')
        self.cut_var = tk.DoubleVar()
        self.cut_scale = ttk.Scale(bar, orient='horizontal', length=200, variable=self.cut_var,
                                   command=lambda v: self._on_cut())
        self.cut_scale.pack(side='left', padx=4)
        self.cut_label = ttk.Label(bar, width=8)
        self.cut_label.pack(side='left')
        self.layers = {}
        for key, text, on in (('zones', _('Zones'), True), ('players', _('Start points and pickups'), True),
                              ('objects', _('Objects'), True), ('effects', _('Effects and sounds'), False),
                              ('lights', _('Lights'), False),
                              ('names', _('Names'), False)):
            v = self.layers[key] = tk.BooleanVar(value=on)
            ttk.Checkbutton(bar, text=text, variable=v, command=self._draw).pack(side='left', padx=(10, 0))
        ttk.Button(bar, text=_('Fit'), command=self._fit).pack(side='right')

        self.canvas = tk.Canvas(self, background='#1c1f24', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)

        legend = ttk.Frame(self, padding=(6, 2))
        legend.pack(fill='x')
        for color, text in ((COLORS['start'], _('start point')), (COLORS['pickup'], _('pickup')),
                            (COLORS['vehicle'], _('vehicle')), (COLORS['object'], _('object')),
                            (COLORS['node'], _('scene object')), (ZONE_COLORS['dom_snd'], _('sound zone')),
                            (ZONE_COLORS['dom_ai'], _('bots')), (COLORS['light'], _('light'))):
            tk.Label(legend, text='●', foreground=color).pack(side='left')
            ttk.Label(legend, text=text).pack(side='left', padx=(0, 10))
        self.hint = ttk.Label(self, foreground='#808080', padding=(6, 0))
        self.hint.pack(fill='x')
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, padding=(6, 0, 6, 6)).pack(fill='x')
        self._show_hint()

        c = self.canvas
        c.bind('<Configure>', lambda e: self._changed(fit=not self.s))
        c.bind('<MouseWheel>', self._on_wheel)
        c.bind('<ButtonPress-1>', self._on_press)
        c.bind('<B1-Motion>', self._on_drag)
        c.bind('<ButtonRelease-1>', self._on_release)
        c.bind('<ButtonPress-2>', self._on_press)
        c.bind('<B2-Motion>', lambda e: self._on_drag(e, pan=True))
        c.bind('<ButtonRelease-2>', lambda e: self._on_release(e, select=False))
        c.bind('<Double-Button-1>', self._on_double)
        c.bind('<Motion>', self._on_motion)
        c.bind('<Button-3>', self._copy_coords)
        # keys by Windows virtual key code, so that they work with any keyboard layout
        self.bind('<KeyPress>', self._key_down)
        self.bind('<KeyRelease>', self._key_up)
        self.bind('<FocusOut>', lambda e: self._keys.clear())

    def _show_hint(self):
        self.hint.configure(text=_('Drag: rotate, Shift+drag or middle button: move, wheel: zoom, double click: '
                                   'rotate around that point, right click: copy coordinates') + '\n'
                            + _('WASD / arrows: move, Q / E: down / up, Shift: faster')
                            if self.mode3d.get() else
                            _('Wheel: zoom, drag: move, right click: copy coordinates') + '\n'
                            + _('WASD / arrows: move, Shift: faster'))

    # ------------------------------------------------------------------ level
    def set_level(self, cls, level):
        """Show a level (LevelFile); the view is kept when the same level is loaded again."""
        keep = cls == self.cls and self.s
        self.cls = cls
        self.title(_('Map') + ' — ' + cls)
        self.status.set(_('Loading %s ...', cls))

        def work():
            view = LevelView(level)
            self.app.msgs.put(('call', lambda: self._view_ready(cls, view, keep)))
        threading.Thread(target=work, daemon=True).start()

    def _view_ready(self, cls, view, keep):
        if cls != self.cls or not self.winfo_exists():
            return
        self.view = view
        self.picks = None
        threading.Thread(target=view.prepare_3d, daemon=True).start()
        sel = self.selected
        self.selected = None
        if sel is not None:                 # the same object in the new view
            self.selected = next((o for o in view.objects if o.name == sel.name and o.kind == sel.kind), None)
        lo, hi = view.yrange
        self.cut_scale.configure(from_=math.floor(lo) - 1, to=math.ceil(hi) + 1)
        if not keep:
            cut = view.default_cut()
            self.cut_var.set(min(cut, math.ceil(hi) + 1) if cut is not None else math.ceil(hi) + 1)
            self.cam = None
        self._show_cut()
        self.status.set(_('%s: %d triangles, %d objects', cls, len(view.tris), len(view.objects)))
        if self.mode3d.get() and self.cam is None:
            self._reset_camera()
        self._changed(fit=not keep)
        if self._pending is not None:
            index, self._pending = self._pending, None
            self.highlight_text(index)

    # ------------------------------------------------------------------ transform
    def _size(self):
        return max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())

    def to_canvas(self, x, z):
        w, h = self._size()
        return w / 2 + (x - self.cx) * self.s, h / 2 - (z - self.cz) * self.s

    def to_world(self, px, py):
        w, h = self._size()
        return self.cx + (px - w / 2) / self.s, self.cz - (py - h / 2) / self.s

    def _screen(self, x, y, z):
        """Canvas position of a world point (None behind the 3D camera)."""
        if self.mode3d.get():
            w, h = self._size()
            p = self.cam.project(w, h, (x, y, z))
            return p and p[:2]
        return self.to_canvas(x, z)

    def _fit(self):
        if not self.view:
            return
        if self.mode3d.get():
            self._reset_camera()
            self._camera_changed(preview=False)
            return
        x0, z0, x1, z1 = self.view.bounds()
        w, h = self._size()
        if w < 50 or h < 50:                      # not laid out yet: <Configure> fits it later
            return
        self.cx, self.cz = (x0 + x1) / 2, (z0 + z1) / 2
        self.s = 0.98 * min(w / (x1 - x0), h / (z1 - z0))
        self._changed()

    def _changed(self, fit=False):
        """The view moved or changed: draw the objects now, the image a moment later."""
        if not self.view:
            return
        if fit:
            self._fit()
            return
        self._draw()
        self._schedule()

    # ------------------------------------------------------------------ 3D camera
    def _floor_height(self):
        ys = sorted(o.pos[1] for o in self.view.objects if o.kind in ('start', 'pickup'))
        return ys[len(ys) // 2] if ys else 0.0

    def _reset_camera(self):
        x0, z0, x1, z1 = self.view.bounds()
        self.cam = Camera(((x0 + x1) / 2, self._floor_height(), (z0 + z1) / 2), yaw=0.0, pitch=55.0,
                          dist=0.9 * max(x1 - x0, z1 - z0))

    def _toggle_3d(self):
        self._show_hint()
        if not self.view:
            return
        if self.mode3d.get():
            if self.cam is None:
                w, h = self._size()
                if self.s:                        # start from what the top view shows
                    self.cam = Camera((self.cx, self._floor_height(), self.cz), yaw=0.0, pitch=60.0,
                                      dist=0.9 * w / self.s)
                else:
                    self._reset_camera()
            self.canvas.delete('img')
            self._camera_changed(preview=False)
        else:
            self.canvas.delete('img')
            self._changed(fit=not self.s)

    def _cam_state(self):
        c = self.cam
        return c.target, c.yaw, c.pitch, c.dist

    def _camera_changed(self, preview=True):
        """The camera moved: a quick preview now (when asked), the full image a moment later."""
        self.gen += 1                             # images being rendered for the old camera are dropped
        self.picks = None
        if preview and not self._preview_job:
            self._preview_job = self.after(10, self._preview)
        elif not preview:
            self._draw()
        self._schedule(250)

    def _preview(self):
        self._preview_job = None
        if not self.view or not self.mode3d.get():
            return
        w, h = self._size()
        pw, ph = max(1, w // PREVIEW_SCALE), max(1, h // PREVIEW_SCALE)
        rgb, _ids = self.view.render3d(self.cam, pw, ph, self._cut(), preview=True)
        self.photo = tk.PhotoImage(data=png_base64(rgb, pw, ph)).zoom(PREVIEW_SCALE)
        self.canvas.delete('img')
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo, tags='img')
        self.canvas.tag_lower('img')
        self._draw()

    def pick(self, px, py):
        """3D: the world point under a canvas point, from the last full image (None while the camera moves)."""
        if not self.picks or self.picks[0] != self._cam_state() or self.picks[1:3] != self._size():
            return None
        _state, w, h, ids = self.picks
        return self.view.pick(self.cam, w, h, ids, int(px), int(py))

    # ------------------------------------------------------------------ height cut
    def _cut(self):
        """Current cut height, None when the slider is at the top (nothing hidden)."""
        v = self.cut_var.get()
        return None if v >= float(self.cut_scale.cget('to')) - 0.01 else v

    def _show_cut(self):
        cut = self._cut()
        self.cut_label.configure(text=_('none') if cut is None else '%.1f m' % cut)

    def _on_cut(self):
        self._show_cut()
        if self.mode3d.get() and self.view:
            self.gen += 1
            self.picks = None
            if not self._preview_job:
                self._preview_job = self.after(10, self._preview)
        self._schedule(300)

    # ------------------------------------------------------------------ image
    def _schedule(self, delay=150):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(delay, self._render)

    def _render(self):
        self._job = None
        if not self.view:
            return
        w, h = self._size()
        self.gen += 1
        gen, view, cut = self.gen, self.view, self._cut()
        if self.mode3d.get():
            state = self._cam_state()
            cam = Camera(*state, fov=self.cam.fov)       # a copy: the window may move the camera meanwhile

            def work3d():
                rgb, ids = view.render3d(cam, w, h, cut)
                data = png_base64(rgb, w, h)
                self.app.msgs.put(('call', lambda: self._rendered3d(gen, state, w, h, data, ids)))
            threading.Thread(target=work3d, daemon=True).start()
            return
        x0, z1 = self.to_world(0, 0)
        x1, z0 = self.to_world(w, h)
        s = self.s

        def work():
            rgb, hgt = view.render((x0, z0, x1, z1), w, h, cut)
            data = png_base64(rgb, w, h)
            self.app.msgs.put(('call', lambda: self._rendered(gen, x0, z1, s, w, h, data, hgt)))
        threading.Thread(target=work, daemon=True).start()

    def _rendered(self, gen, x0, z1, s, w, h, data, hgt):
        if gen != self.gen or not self.winfo_exists() or self.mode3d.get():
            return
        self.photo = tk.PhotoImage(data=data)
        self.canvas.delete('img')
        px, py = self.to_canvas(x0, z1)
        self.canvas.create_image(px, py, anchor='nw', image=self.photo, tags='img')
        self.canvas.tag_lower('img')
        self.heights = (x0, z1, s, w, h, hgt)

    def _rendered3d(self, gen, state, w, h, data, ids):
        if gen != self.gen or not self.winfo_exists() or not self.mode3d.get():
            return
        self.photo = tk.PhotoImage(data=data)
        self.canvas.delete('img')
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo, tags='img')
        self.canvas.tag_lower('img')
        self.picks = (state, w, h, ids)
        self._draw()                              # the markers hidden behind walls are known now

    def height_at(self, x, z):
        if not self.heights:
            return None
        x0, z1, s, w, h, hgt = self.heights
        px, py = int((x - x0) * s), int((z1 - z) * s)
        if 0 <= px < w and 0 <= py < h and hgt[py * w + px] > -1e8:
            return hgt[py * w + px]
        return None

    # ------------------------------------------------------------------ objects
    def _hidden(self, o, px, py):
        """3D: the marker is behind the level geometry (known only for a full image)."""
        if not self.mode3d.get() or self.picks is None:
            return False
        p = self.pick(px, py)
        if p is None:
            return False
        e = self.cam.basis()[0]
        return math.dist(e, p) < math.dist(e, o.pos) - 0.5

    def _draw(self):
        c = self.canvas
        c.delete('ov')
        if not self.view or (self.mode3d.get() and self.cam is None):
            return
        w, h = self._size()
        names = self.layers['names'].get()
        for i, o in enumerate(self.view.objects):
            if not self.layers[LAYER_OF[o.kind]].get() and o is not self.selected:
                continue
            p = self._screen(*o.pos)
            if p is None:
                continue
            px, py = p
            tags = ('ov', 'o%d' % i)
            if o.outline and len(o.outline) > 1:
                shape = self._outline(o)
                if shape is None:
                    continue
                bottom, top = shape
                xs, ys = [q[0] for q in bottom + (top or [])], [q[1] for q in bottom + (top or [])]
                if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                    continue
                if o.kind == 'zone':
                    col = zone_color(o)
                    special = col != ZONE_COLORS['dom_snd']
                    dash = () if special else (4, 3)
                    if self.mode3d.get() and o.height is None:
                        dash = (2, 4)                 # no DOMAIN height: only the flat outline is known
                    self._draw_shape(bottom, top, col, 2 if special else 1, dash, tags)
                else:
                    self._draw_shape(bottom, None, COLORS[o.kind], 1, (), tags)
            elif not (-20 < px < w + 20 and -20 < py < h + 20):
                continue
            col = zone_color(o) if o.kind == 'zone' else COLORS[o.kind]
            r = {'light': 2, 'sound': 3, 'effect': 2, 'object': 3, 'node': 3}.get(o.kind, 5)
            fill, edge = (col, '#10141a') if not self._hidden(o, px, py) else ('', col)   # behind a wall: outline
            if o.kind == 'start':
                c.create_polygon(px, py - r - 1, px - r, py + r, px + r, py + r, fill=fill, outline=edge, tags=tags)
            elif o.kind == 'vehicle':
                c.create_rectangle(px - r, py - r, px + r, py + r, fill=fill, outline=edge, tags=tags)
            else:
                c.create_oval(px - r, py - r, px + r, py + r, fill=fill,
                              outline=edge if r > 2 or not fill else '', tags=tags)
            if names and o.kind != 'light':
                c.create_text(px + r + 3, py, text=o.name, anchor='w', fill=col, font=('Segoe UI', 8), tags=tags)
        o = self.selected
        p = o is not None and self._screen(*o.pos)
        if p:
            px, py = p
            shape = o.outline and len(o.outline) > 1 and self._outline(o)
            if shape:
                self._draw_shape(shape[0], shape[1], '#ffffff', 3, (), 'ov')
            c.create_oval(px - 10, py - 10, px + 10, py + 10, outline='#ffffff', width=2, tags='ov')
            c.create_text(px + 13, py - 12, text=o.name, anchor='w', fill='#ffffff', font=('Segoe UI', 9, 'bold'),
                          tags='ov')

    def _outline(self, o):
        """Canvas points of an object's outline: (bottom, top). In 3D a zone with a height is a prism and `top`
        is its upper outline, otherwise None. None when a point is behind the camera."""
        y = o.pos[1]
        bottom = [self._screen(x, y, z) for x, z in o.outline]
        top = None
        if self.mode3d.get() and o.kind == 'zone' and o.height:
            top = [self._screen(x, y + o.height, z) for x, z in o.outline]
            if None in top:
                return None
        return None if None in bottom else (bottom, top)

    def _draw_shape(self, bottom, top, col, width, dash, tags):
        c = self.canvas
        if len(bottom) > 2:
            c.create_polygon([v for q in bottom for v in q], outline=col, fill='', width=width, dash=dash, tags=tags)
        else:
            c.create_line([v for q in bottom for v in q], fill=col, width=width, dash=dash, tags=tags)
        if top:
            c.create_polygon([v for q in top for v in q], outline=col, fill='', width=width, dash=dash, tags=tags)
            for a, b in zip(bottom, top):                 # vertical edges
                c.create_line(a[0], a[1], b[0], b[1], fill=col, width=width, dash=dash, tags=tags)

    def _object_at(self, px, py):
        """The object under the cursor: the nearest marker, else the smallest outline that contains the point."""
        best, dist = None, 9.0
        for item in self.canvas.find_overlapping(px - 6, py - 6, px + 6, py + 6):
            tag = next((t for t in self.canvas.gettags(item) if t.startswith('o') and t[1:].isdigit()), None)
            if tag is None:
                continue
            o = self.view.objects[int(tag[1:])]
            p = self._screen(*o.pos)
            if p is None:
                continue
            d = math.hypot(p[0] - px, p[1] - py)
            if d < dist:
                best, dist = o, d
        if best is not None:
            return best
        if self.mode3d.get():
            p = self.pick(px, py)
            if p is None:
                return None
            x, z = p[0], p[2]
        else:
            x, z = self.to_world(px, py)
        inside = [o for o in self.view.objects if o.outline and len(o.outline) > 2 and o.kind != 'light'
                  and (self.layers[LAYER_OF[o.kind]].get()) and _contains(o.outline, x, z)]
        return min(inside, key=lambda o: _area(o.outline)) if inside else None

    def _describe(self, o):
        kind = {'zone': _('zone'), 'start': _('start point'), 'pickup': _('pickup'), 'vehicle': _('vehicle'),
                'object': _('object'), 'node': _('scene object'), 'sound': _('sound'), 'effect': _('effect'),
                'light': _('light')}[o.kind]
        text = '%s  [%s%s]  (%.1f, %.1f, %.1f)' % (o.name, kind, ', ' + o.template if o.template else '', *o.pos)
        if o.kind == 'zone':
            text += '  ' + (_('height %g m', o.height) if o.height is not None else _('height not set'))
        if o.texts:
            text += '  ' + _('click: show in the list')
        return text

    # ------------------------------------------------------------------ selection
    def highlight_text(self, index):
        """An object was selected in the Scripts list: mark it on the map."""
        if not self.view:
            self._pending = index                 # shown when the view is ready
            return
        o = self.view.by_text.get(index)
        self.selected = o
        if o is None:
            self.status.set(_('This object has no position on the map'))
            self._draw()
            return
        w, h = self._size()
        p = self._screen(*o.pos)
        if p is None or not (40 < p[0] < w - 40 and 40 < p[1] < h - 40):
            if self.mode3d.get():
                self.cam.target = tuple(o.pos)
                self._camera_changed(preview=False)
            else:
                self.cx, self.cz = o.pos[0], o.pos[2]
                self._changed()
        else:
            self._draw()
        self.status.set(self._describe(o))

    # ------------------------------------------------------------------ mouse
    def _on_wheel(self, e):
        if not self.view:
            return
        if self.mode3d.get():
            self.cam.dist = min(3000.0, max(1.0, self.cam.dist * (0.8 if e.delta > 0 else 1.25)))
            self._camera_changed()
            return
        f = 1.25 if e.delta > 0 else 0.8
        x, z = self.to_world(e.x, e.y)
        self.s = min(400.0, max(0.2, self.s * f))
        w, h = self._size()
        self.cx, self.cz = x - (e.x - w / 2) / self.s, z + (e.y - h / 2) / self.s
        self._changed()

    def _on_press(self, e):
        self.canvas.focus_set()                   # for the movement keys
        self._drag = (e.x, e.y, e.x, e.y)

    # ------------------------------------------------------------------ keyboard
    def _key_down(self, e):
        key = MOVE_KEYS.get(e.keycode)
        if key is None or not self.view:
            return
        self._keys.add(key)
        if not self._move_job and self._keys - {'fast'}:
            self._move_time = time.monotonic()
            self._move_job = self.after(15, self._move_step)

    def _key_up(self, e):
        self._keys.discard(MOVE_KEYS.get(e.keycode))

    def _move_step(self):
        """Move while keys are held: the speed does not depend on how long a frame takes."""
        self._move_job = None
        keys = self._keys
        if not self.view or not keys - {'fast'} or not self.winfo_exists() or not (self.s or self.mode3d.get()):
            return
        now = time.monotonic()
        dt = min(0.25, now - self._move_time)
        self._move_time = now
        fwd = ('fwd' in keys) - ('back' in keys)
        side = ('right' in keys) - ('left' in keys)
        up = ('up' in keys) - ('down' in keys)
        fast = 3.0 if 'fast' in keys else 1.0
        if self.mode3d.get():
            cam = self.cam
            step = max(4.0, 0.6 * cam.dist) * fast * dt             # metres
            y = math.radians(cam.yaw)
            f, r = (math.sin(y), math.cos(y)), (math.cos(y), -math.sin(y))   # along the ground
            x, h, z = cam.target
            cam.target = (x + (f[0] * fwd + r[0] * side) * step, h + up * step, z + (f[1] * fwd + r[1] * side) * step)
            self._camera_changed()
        else:
            px = 500.0 * fast * dt                                   # pixels
            self.canvas.move('all', -side * px, fwd * px)
            self.cx += side * px / self.s
            self.cz += fwd * px / self.s
            self._schedule()
        self._move_job = self.after(15, self._move_step)

    def _on_drag(self, e, pan=False):
        if not self._drag or not self.view:
            return
        x0, y0, lx, ly = self._drag
        if abs(e.x - x0) + abs(e.y - y0) < 4 and (lx, ly) == (x0, y0):
            return                                # still a click
        dx, dy = e.x - lx, e.y - ly
        self._drag = (x0, y0, e.x, e.y)
        if self.mode3d.get():
            if pan or e.state & 0x1:              # middle button or Shift: move the point the camera looks at
                self.cam.pan(dx, dy, self._size()[0])
            else:
                self.cam.yaw = (self.cam.yaw - dx * 0.4) % 360
                self.cam.pitch = min(89.0, max(-20.0, self.cam.pitch + dy * 0.3))
            self._camera_changed()
            return
        self.canvas.move('all', dx, dy)
        self.cx -= dx / self.s
        self.cz += dy / self.s
        self._schedule()

    def _on_release(self, e, select=True):
        drag, self._drag = self._drag, None
        if not drag or not self.view or (drag[2], drag[3]) != (drag[0], drag[1]) or not select:
            return
        o = self._object_at(e.x, e.y)
        self.selected = o
        self._draw()
        if o is None:
            return
        self.status.set(self._describe(o))
        if o.texts:
            self.tab.show_object(o.texts[0])

    def _on_double(self, e):
        """3D: rotate around the point under the cursor."""
        if not self.view or not self.mode3d.get():
            return
        p = self.pick(e.x, e.y)
        if p is not None:
            self.cam.target = p
            self._camera_changed(preview=False)

    def _point_at(self, px, py):
        """World point under the cursor: (x, y or None, z), or None."""
        if self.mode3d.get():
            return self.pick(px, py)
        x, z = self.to_world(px, py)
        return x, self.height_at(x, z), z

    def _on_motion(self, e):
        if not self.view or self._drag:
            return
        p = self._point_at(e.x, e.y)
        pos = '' if p is None else 'X %.1f   Y %s   Z %.1f' % (p[0], '—' if p[1] is None else '%.1f' % p[1], p[2])
        o = self._object_at(e.x, e.y)
        self.status.set(pos + ('      ' + self._describe(o) if o is not None else ''))

    def _copy_coords(self, e):
        if not self.view:
            return
        p = self._point_at(e.x, e.y)
        if p is None:
            return
        text = '%.1f, %.1f, %.1f' % (p[0], 0.0 if p[1] is None else p[1], p[2])
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set(_('Copied: %s', text))


def _contains(poly, x, z):
    inside = False
    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]):
        if (az > z) != (bz > z) and x < ax + (z - az) * (bx - ax) / (bz - az):
            inside = not inside
    return inside


def _area(poly):
    return abs(sum(ax * bz - bx * az for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]))) / 2
