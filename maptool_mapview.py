"""Map window: top view or 3D view of the level loaded on the Scripts tab; its objects are linked to the object list."""
import math
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from tsmap import _
from tsmap.lg import LevelFile
from tsmap.mapview import (MODES, SPAWN_FLAGS, Camera, LevelView, instance_kind, instance_records, matrix_yaw,
                           mesh_thumbnail, move_matrix, png_base64, template_meshes, unique_name)

COLORS = {'start': '#4aa8ff', 'pickup': '#60d060', 'vehicle': '#ff9a2e', 'object': '#e8c84a', 'node': '#d080ff',
          'sound': '#9a9a9a', 'effect': '#b8a878', 'light': '#fff3a8', 'flag': '#ff60c0'}
ZONE_COLORS = {'dom_ai': '#ff5a5a', 'dom_spawn': '#ff9a2e', 'dom_time': '#c080ff', 'dom_kill': '#ff3030',
               'dom_snd': '#7f97b0'}
LAYER_OF = {'zone': 'zones', 'start': 'players', 'flag': 'players', 'pickup': 'players', 'vehicle': 'players',
            'object': 'objects',
            'node': 'objects', 'sound': 'effects', 'effect': 'effects', 'light': 'lights'}
PREVIEW_SCALE = 3          # the 3D preview is drawn at 1/3 of the window size and zoomed
PREVIEW_W, PREVIEW_H = 240, 180    # catalog picture
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
        self._pivot = None         # 3D: world point the camera turns around while dragging
        self._keys = set()         # movement keys held down
        self._move_job = None
        self._move_time = 0.0
        # changes on the map, saved by the Scripts tab together with the text edits
        self.moves = {}            # existing objects moved: {matrix offset: matrix}
        self.added = []            # new objects (copies of existing ones), also in view.objects with .new
        self.deleted = set()       # names of existing objects to delete (still in view.objects with .deleted)
        self.flag_edits = {}       # name -> new flags of an existing object
        self._loaded = {}          # _key(object) -> (pos, matrix, outline) as loaded or created, for "Undo changes"
        self._originals = {}       # level name -> {instance name: matrix} of the original game level
        self._catalog_win = None
        self._moving = None        # dragging an object: (object, grab dx, grab dz, height above the floor or None)
        self.title(_('Map'))
        self.geometry('1020x840')
        self._build()
        self.protocol('WM_DELETE_WINDOW', self._close)

    # ------------------------------------------------------------------ layout
    def _build(self):
        bar = ttk.Frame(self, padding=(6, 6))
        bar.pack(fill='x')
        self.mode3d = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='3D', variable=self.mode3d, command=self._toggle_3d).pack(side='left', padx=(0, 8))
        self.edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=_('Edit objects'), variable=self.edit_var,
                        command=self._toggle_edit).pack(side='left', padx=(0, 12))
        self.bar = bar
        ttk.Label(bar, text=_('Height cut:')).pack(side='left')
        self.cut_var = tk.DoubleVar()
        self.cut_scale = ttk.Scale(bar, orient='horizontal', length=150, variable=self.cut_var,
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

        # object editing: shown with "Move objects"
        self.edit_bar = ttk.Frame(self, padding=(6, 0, 6, 6))
        ed = ttk.Frame(self.edit_bar)
        ed.pack(fill='x')
        ed2 = ttk.Frame(self.edit_bar)
        ed2.pack(fill='x', pady=(4, 0))
        self.edit_name = ttk.Label(ed, width=30, anchor='w')
        self.edit_name.pack(side='left')
        self.edit_vars = {}
        for key in ('X', 'Y', 'Z', _('Angle')):
            ttk.Label(ed, text=key).pack(side='left', padx=(6, 2))
            v = self.edit_vars[key] = tk.StringVar()
            e = ttk.Entry(ed, textvariable=v, width=7)
            e.pack(side='left')
            e.bind('<Return>', lambda ev: self._edit_set())
        self.edit_buttons = [
            ttk.Button(ed, text=_('Set'), command=self._edit_set, width=7),
            ttk.Button(ed, text='⟲ 15°', command=lambda: self._edit_turn(15), width=6),
            ttk.Button(ed, text='⟳ 15°', command=lambda: self._edit_turn(-15), width=6),
            ttk.Button(ed2, text=_('On the floor'), command=self._edit_floor),
            ttk.Button(ed2, text=_('Original position'), command=self._edit_original),
            ttk.Button(ed2, text=_('Copy'), command=self._edit_copy),
            ttk.Button(ed2, text=_('Delete'), command=self._edit_delete),
            ttk.Button(ed2, text=_('Undo changes'), command=self._edit_undo),
        ]
        for b in self.edit_buttons:
            b.pack(side='left', padx=(4, 0) if b.master is ed else (0, 4))
        # flags of the instance (chunk 0x1bb): spawn point teams and the game modes it is left out of
        ed3 = ttk.Frame(self.edit_bar)
        ed3.pack(fill='x', pady=(4, 0))
        self.flag_vars, self.flag_checks = {}, []
        ttk.Label(ed3, text=_('Spawn point:')).pack(side='left')
        for flag, text in zip(SPAWN_FLAGS, (_('no teams'), _('team 1'), _('team 2'))):
            self._flag_check(ed3, flag, text)
        ttk.Label(ed3, text=_('Not in modes:')).pack(side='left', padx=(16, 0))
        for mode in MODES:
            self._flag_check(ed3, 'notIN_' + mode, mode)
        self.flags_other = ttk.Label(ed3, foreground='#808080')
        self.flags_other.pack(side='left', padx=(16, 0))
        ttk.Button(ed2, text=_('Catalog...'), command=self._open_catalog).pack(side='left', padx=(12, 0))
        ttk.Button(ed2, text=_('Save and apply to the game'), command=lambda: self.tab.save(apply=True)).pack(side='right')
        ttk.Button(ed2, text=_('Save'), command=self.tab.save).pack(side='right', padx=4)
        self.moved_label = ttk.Label(ed2, foreground='#b05000')
        self.moved_label.pack(side='right', padx=6)

        self.canvas = tk.Canvas(self, background='#1c1f24', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)

        legend = ttk.Frame(self, padding=(6, 2))
        legend.pack(fill='x')
        for color, text in ((COLORS['start'], _('start point')), (COLORS['flag'], _('CTF flag')),
                            (COLORS['pickup'], _('pickup')),
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
        self.hint.configure(text=_('Drag: rotate around the point under the cursor, Shift+drag or middle button: move, '
                                   'wheel: zoom, double click: centre on that point, right click: copy coordinates') + '\n'
                            + _('WASD / arrows: move, Q / E: down / up, Shift: faster')
                            + ('   ' + _('Drag an object to move it, R / Shift+R: turn it by 15°')
                               if self.edit_var.get() else '')
                            if self.mode3d.get() else
                            _('Wheel: zoom, drag: move, right click: copy coordinates') + '\n'
                            + _('WASD / arrows: move, Shift: faster')
                            + ('   ' + _('Drag an object to move it, R / Shift+R: turn it by 15°')
                               if self.edit_var.get() else ''))

    # ------------------------------------------------------------------ level
    def set_level(self, cls, level):
        """Show a level (LevelFile); the view is kept when the same level is loaded again."""
        keep = cls == self.cls and self.s
        self.cls = cls
        self.title(_('Map') + ' — ' + cls)
        self.status.set(_('Loading %s ...', cls))

        templates = template_meshes(self.app.game, self.app.lock)     # boxes, barrels, vehicles ... are drawn

        def work():
            view = LevelView(level, templates)
            self.app.msgs.put(('call', lambda: self._view_ready(cls, view, keep)))
        threading.Thread(target=work, daemon=True).start()

    def _view_ready(self, cls, view, keep):
        if cls != self.cls or not self.winfo_exists():
            return
        self.view = view
        self.picks = None
        self.moves.clear()                  # a new level (or the saved one): nothing is changed in it yet
        self.added, self.deleted, self.flag_edits, self._loaded = [], set(), {}, {}
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
        self._fill_edit()
        self._changed(fit=not keep)
        if self._pending is not None:
            pending, self._pending = self._pending, None
            if isinstance(pending, tuple):
                self.highlight_instance(pending[1])
            else:
                self.highlight_text(pending)

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
        editing = self.edit_var.get()
        for i, o in enumerate(self.view.objects):
            if not self.layers[LAYER_OF[o.kind]].get() and o is not self.selected or o.deleted and not editing:
                continue
            p = self._screen(*o.pos)
            if p is None:
                continue
            px, py = p
            tags = ('ov', 'o%d' % i)
            if o.deleted:                                 # deleted, not saved yet: a red cross to restore it
                c.create_line(px - 6, py - 6, px + 6, py + 6, fill='#ff5050', width=2, tags=tags)
                c.create_line(px - 6, py + 6, px + 6, py - 6, fill='#ff5050', width=2, tags=tags)
                continue
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
            if o.new and editing:                         # added, not saved yet
                c.create_oval(px - r - 4, py - r - 4, px + r + 4, py + r + 4, outline='#ff9a2e', dash=(2, 2),
                              tags=tags)
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
        if self.edit_var.get():
            for o in self.view.objects:                   # where the moved objects were: a dashed line
                if o.mpos in self.moves and o.mpos in self._loaded and not o.deleted:
                    a, b = self._screen(*self._loaded[o.mpos][0]), self._screen(*o.pos)
                    if a and b:
                        c.create_line(a[0], a[1], b[0], b[1], fill='#ff9a2e', dash=(3, 3), tags='ov')
                        c.create_oval(a[0] - 3, a[1] - 3, a[0] + 3, a[1] + 3, outline='#ff9a2e', tags='ov')
            o = self._editable()
            if o is not None:                             # heading: the object's local Z axis
                n = math.hypot(o.matrix[8], o.matrix[10]) or 1.0
                tip = (o.pos[0] + 1.5 * o.matrix[8] / n, o.pos[1], o.pos[2] + 1.5 * o.matrix[10] / n)
                a, b = self._screen(*o.pos), self._screen(*tip)
                if a and b:
                    c.create_line(a[0], a[1], b[0], b[1], fill='#ffffff', width=2, arrow='last', tags='ov')
        p = self._pivot is not None and self.mode3d.get() and self._screen(*self._pivot)
        if p:                                         # the point the camera turns around while dragging
            px, py = p
            c.create_line(px - 7, py, px + 7, py, fill='#ffffff', width=2, tags='ov')
            c.create_line(px, py - 7, px, py + 7, fill='#ffffff', width=2, tags='ov')

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

    def _object_at(self, px, py, markers_only=False):
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
        if best is not None or markers_only:
            return best
        if self.mode3d.get():
            p = self.pick(px, py)
            if p is None:
                return None
            x, z = p[0], p[2]
        else:
            x, z = self.to_world(px, py)
        inside = [o for o in self.view.objects if o.outline and len(o.outline) > 2 and o.kind != 'light'
                  and not o.deleted
                  and (self.layers[LAYER_OF[o.kind]].get()) and _contains(o.outline, x, z)]
        return min(inside, key=lambda o: _area(o.outline)) if inside else None

    def _describe(self, o):
        kind = {'zone': _('zone'), 'start': _('start point'), 'pickup': _('pickup'), 'vehicle': _('vehicle'),
                'object': _('object'), 'node': _('scene object'), 'sound': _('sound'), 'effect': _('effect'),
                'light': _('light'), 'flag': _('CTF flag')}[o.kind]
        text = '%s  [%s%s]  (%.1f, %.1f, %.1f)' % (o.name, kind, ', ' + o.template if o.template else '', *o.pos)
        if o.flags:
            text += '  &' + '&'.join(o.flags)
        if o.new:
            text += '  ' + _('(added, not saved)')
        elif o.deleted:
            text += '  ' + _('(deleted, not saved)')
        if o.kind == 'zone':
            text += '  ' + (_('height %g m', o.height) if o.height is not None else _('height not set'))
        if (o.texts or o.movable) and not o.new:
            text += '  ' + _('click: show in the list')
        return text

    # ------------------------------------------------------------------ selection
    def _show_in_list(self, o):
        """Select the object in the Scripts list: its property text, or its read-only row when it has none."""
        if o.new:
            return                                    # in the list after saving
        if o.texts:
            self.tab.show_object(o.texts[0])
        elif o.movable:
            self.tab.show_instance(o.mpos)

    def highlight_instance(self, mpos):
        """An instance without a property text was selected in the Scripts list: mark it on the map."""
        if not self.view:
            self._pending = ('instance', mpos)
            return
        self._highlight(next((o for o in self.view.objects if o.mpos == mpos), None))

    def highlight_text(self, index):
        """An object was selected in the Scripts list: mark it on the map."""
        if not self.view:
            self._pending = index                 # shown when the view is ready
            return
        self._highlight(self.view.by_text.get(index))

    def _highlight(self, o):
        self.selected = o
        self._fill_edit()
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

    # ------------------------------------------------------------------ moving objects
    # Only instances (0x1b9 records: start points, pickups, vehicles, objects, effects, sounds) can be moved: their
    # matrix is changed in place. Zones and static geometry are part of the level's spatial grid (0x21f) and of
    # its collision files, so they stay where they are.
    def _toggle_edit(self):
        if self.edit_var.get():
            self.edit_bar.pack(fill='x', after=self.bar)
        else:
            self.edit_bar.pack_forget()
        self._show_hint()
        self._fill_edit()
        self._draw()

    def _flag_check(self, parent, flag, text):
        v = self.flag_vars[flag] = tk.BooleanVar()
        cb = ttk.Checkbutton(parent, text=text, variable=v, command=self._edit_flags)
        cb.pack(side='left', padx=(6, 0))
        self.flag_checks.append(cb)

    def _editable(self):
        o = self.selected
        return o if self.edit_var.get() and o is not None and o.movable else None

    @staticmethod
    def _key(o):
        return ('new', id(o)) if o.new else o.mpos

    def pending(self):
        """Number of changes on the map that are not saved."""
        return len(self.moves) + len(self.added) + len(self.deleted) + len(self.flag_edits)

    def changes(self):
        """Everything to save: (moves {offset: matrix}, adds [(name, Instance copied, matrix, flags)],
        deletes {name}, flags {name: flags}, [(template, class)] of catalog objects for the preload list)."""
        return (dict(self.moves), [(o.name, o.record, o.matrix, tuple(o.flags)) for o in self.added],
                set(self.deleted), dict(self.flag_edits),
                [(o.record.tpl, o.record.cls) for o in self.added if o.catalog])

    def _changed_objects(self):
        self.tab.moves_changed()
        self._fill_edit()
        self._draw()

    def _fill_edit(self):
        """Show the selected object in the edit bar."""
        o = self._editable()
        n = self.pending()
        self.moved_label.configure(text=_('Changes: %d', n) if n else '')
        state = ['!disabled'] if o else ['disabled']
        for b in self.edit_buttons + self.flag_checks:
            b.state(state)
        if o is None:
            self.edit_name.configure(text=_('Select an object to edit') if self.edit_var.get() else '')
            for v in self.edit_vars.values():
                v.set('')
            for v in self.flag_vars.values():
                v.set(False)
            self.flags_other.configure(text='')
            return
        changed = o.new or o.deleted or o.mpos in self.moves or o.name in self.flag_edits
        self.edit_name.configure(text=o.name + (' ●' if changed else ''))
        for key, val in zip(('X', 'Y', 'Z', _('Angle')), (*o.pos, matrix_yaw(o.matrix))):
            self.edit_vars[key].set('%.2f' % val if key != _('Angle') else '%.0f' % val)
        for flag, v in self.flag_vars.items():
            v.set(flag in o.flags)
        other = [f for f in o.flags if f not in self.flag_vars]
        self.flags_other.configure(text='&' + '&'.join(other) if other else '')

    def _set_matrix(self, o, m):
        """Move / turn an instance on the map; the move is saved together with the Scripts tab edits."""
        key = self._key(o)
        if key not in self._loaded:
            self._loaded[key] = (o.pos, o.matrix, o.outline)
        pos0, m0, outline0 = self._loaded[key]
        if o.outline and outline0:                        # the outline follows: turn around the old origin, move
            turn = math.radians(matrix_yaw(m) - matrix_yaw(m0))
            c, s = math.cos(turn), math.sin(turn)
            o.outline = [(m[12] + (x - pos0[0]) * c + (z - pos0[2]) * s,
                          m[14] - (x - pos0[0]) * s + (z - pos0[2]) * c) for x, z in outline0]
        o.matrix, o.pos = tuple(m), tuple(m[12:15])
        if not o.new:
            if all(abs(a - b) < 1e-6 for a, b in zip(m, m0)):
                self.moves.pop(o.mpos, None)
            else:
                self.moves[o.mpos] = o.matrix
        self._changed_objects()

    def _floor_offset(self, o):
        """How high the object stands above the floor where it was loaded (kept when it is moved)."""
        pos0 = self._loaded.get(self._key(o), (o.pos,))[0]
        f = self.view.floor_at(pos0[0], pos0[2], pos0[1] + 0.5)
        return pos0[1] - f if f is not None and 0 <= pos0[1] - f <= 1.5 else None

    def _edit_set(self):
        o = self._editable()
        if o is None:
            return
        try:
            x, y, z, a = (float(self.edit_vars[k].get().replace(',', '.')) for k in ('X', 'Y', 'Z', _('Angle')))
        except ValueError:
            self.status.set(_('Position: numbers expected'))
            return
        self._set_matrix(o, move_matrix(o.matrix, (x, y, z), a - matrix_yaw(o.matrix)))

    def _edit_turn(self, deg):
        o = self._editable()
        if o is not None:
            self._set_matrix(o, move_matrix(o.matrix, o.pos, deg))

    def _edit_floor(self):
        o = self._editable()
        if o is None:
            return
        f = self.view.floor_at(o.pos[0], o.pos[2], o.pos[1] + 1.0)
        if f is None:
            self.status.set(_('No floor under %s', o.name))
            return
        off = self._floor_offset(o)
        self._set_matrix(o, move_matrix(o.matrix, (o.pos[0], f + (off if off is not None else 0.0), o.pos[2])))

    def _edit_undo(self):
        """Drop every change of the selected object: a new one is removed, a deleted one comes back, the flags
        and the position are the loaded ones again."""
        o = self._editable()
        if o is None:
            return
        if o.new:
            self._remove_new(o)
            return
        o.deleted = False
        self.deleted.discard(o.name)
        if o.name in self.flag_edits:
            del self.flag_edits[o.name]
            self._set_flags(o, o.record.flags)
        if o.mpos in self._loaded:
            self._set_matrix(o, self._loaded[o.mpos][1])
        self._changed_objects()

    def _remove_new(self, o):
        self.added.remove(o)
        self.view.objects.remove(o)
        self._loaded.pop(self._key(o), None)
        self.selected = None
        self._changed_objects()

    def _set_flags(self, o, flags):
        o.flags = tuple(flags)
        o.kind = instance_kind(o.name, o.record.tpl, o.record.cls, o.flags)

    def _edit_flags(self):
        o = self._editable()
        if o is None:
            return
        managed = list(self.flag_vars)
        flags = [f for f in o.flags if f not in managed]           # other flags keep their place
        flags += [f for f in managed if self.flag_vars[f].get()]
        self._set_flags(o, flags)
        if not o.new:
            if o.flags == o.record.flags:
                self.flag_edits.pop(o.name, None)
            else:
                self.flag_edits[o.name] = o.flags
        self._changed_objects()

    def _edit_copy(self):
        """A copy of the selected object next to it (with its flags and property text), selected to be moved."""
        src = self._editable()
        if src is None or src.deleted:
            return
        taken = {o.name for o in self.view.objects}
        name = unique_name(src.name, taken)
        m = move_matrix(src.matrix, (src.pos[0] + 1.0, src.pos[1], src.pos[2] + 1.0))
        o = type(src)(src.kind, name, src.template, m[12:15],
                      [(x + 1.0, z + 1.0) for x, z in src.outline] if src.outline else None)
        o.matrix, o.flags, o.record, o.new = m, tuple(src.flags), src.record, True
        self.view.objects.append(o)
        self.added.append(o)
        self._loaded[self._key(o)] = (o.pos, o.matrix, o.outline)
        self.selected = o
        self._changed_objects()
        self.status.set(_('Added %s: drag it into place', name))

    def _open_catalog(self):
        if self._catalog_win is not None and self._catalog_win.winfo_exists():
            self._catalog_win.lift()
            return
        self._catalog_win = CatalogDialog(self)

    def add_from_catalog(self, entry):
        """A new object of a catalog entry in the middle of the view, on the floor there; selected to be moved."""
        if not self.view:
            return
        if not self.edit_var.get():
            self.edit_var.set(True)
            self._toggle_edit()
        cut = self._cut()
        if self.mode3d.get():
            x, ref, z = self.cam.target
            floor = self.view.floor_at(x, z, ref + 0.5)
        else:
            x, z = self.cx, self.cz
            ref = 0.0
            floor = self.view.floor_at(x, z, cut if cut is not None else 1e9)    # what is seen from above
        lift = {'pickup': 0.4, 'vehicle': 0.5}.get(entry.kind, 0.0)
        m = move_matrix(entry.record.matrix, (x, (floor if floor is not None else ref) + lift, z))
        taken = {o.name for o in self.view.objects}
        n = 1
        while '%s_%d' % (entry.label, n) in taken:
            n += 1
        name = '%s_%d' % (entry.label, n)
        rec = entry.record
        o = type(self.view.objects[0])(instance_kind(name, rec.tpl, rec.cls, rec.flags), name, entry.label, m[12:15],
                                       self.view.outline_of(rec, m))        # its footprint until it is saved
        o.matrix, o.flags, o.record, o.new, o.catalog = m, tuple(rec.flags), rec, True, True
        self.view.objects.append(o)
        self.added.append(o)
        self._loaded[self._key(o)] = (o.pos, o.matrix, o.outline)
        self.selected = o
        p = self._screen(*o.pos)
        w, h = self._size()
        if p is None or not (0 < p[0] < w and 0 < p[1] < h):
            self._highlight(o)
        self._changed_objects()
        self.status.set(_('Added %s: drag it into place', name))
        self.lift()

    def _edit_delete(self):
        o = self._editable()
        if o is None or o.deleted:
            return
        if o.new:
            self._remove_new(o)
            return
        # scripts that call it by name ($name) would fail
        ref = re.compile(r'\$' + re.escape(o.name) + r'(?![\w|])')
        users = sorted({t.name for t in self.tab.level.objects if ref.search(self.tab.edits.get(t.index, t.text))})
        if users and not messagebox.askyesno(_('Map'), _('%s is used in the scripts of: %s\n\nDelete it anyway?',
                                                         o.name, ', '.join(users[:10])), parent=self):
            return
        o.deleted = True
        self.deleted.add(o.name)
        self._changed_objects()
        self.status.set(self._describe(o))

    def _edit_original(self):
        """Put the object where the original game level has it (the level is read once per map)."""
        o = self._editable()
        if o is None:
            return
        cls, game = self.cls, self.app.game
        if cls in self._originals:
            self._place_original(o, self._originals[cls])
            return

        def work():
            try:
                with self.app.lock:
                    data = game.level_data(cls, original=True)
                mats = {}
                for _c, name, _t, _k, m, _p in instance_records(LevelFile(data)):
                    mats.setdefault(name, m)
            except Exception as e:                          # noqa: BLE001 - reported in the status line
                msg = str(e)
                self.app.msgs.put(('call', lambda: self.status.set(msg)))
                return
            self.app.msgs.put(('call', lambda: (self._originals.__setitem__(cls, mats),
                                                self.winfo_exists() and self._place_original(o, mats))))
        self.status.set(_('Loading %s ...', cls))
        threading.Thread(target=work, daemon=True).start()

    def _place_original(self, o, mats):
        if o.name not in mats or o not in self.view.objects:
            self.status.set(_('%s is not in the original level', o.name))
            return
        self._set_matrix(o, mats[o.name])
        self.status.set(self._describe(o))

    def forget_changes(self):
        """The changes were saved (or the level restored): the view of the reloaded level replaces them."""
        self.moves.clear()
        self.added, self.deleted, self.flag_edits = [], set(), {}

    def _close(self):
        if self.pending() and not messagebox.askyesno(_('Map'), _('Discard %d changes on the map?', self.pending()),
                                                      parent=self):
            return
        self.forget_changes()
        self.destroy()
        self.tab.moves_changed()

    def _world_under(self, px, py, height):
        """World (x, z) under a canvas point: the top view, or the 3D plane Y = height."""
        if self.mode3d.get():
            w, h = self._size()
            p = self.cam.ground(w, h, px + 0.5, py + 0.5, height)
            return (p[0], p[2]) if p else None
        return self.to_world(px, py)

    def _start_moving(self, e):
        """Edit mode: a press on a movable object starts moving it. True when it did."""
        if not self.edit_var.get() or e.state & 0x1:
            return False
        o = self._object_at(e.x, e.y, markers_only=True)
        if o is None or not o.movable or o.deleted:
            return False
        p = self._world_under(e.x, e.y, o.pos[1])
        if p is None:
            return False
        self.selected = o
        self._moving = (o, o.pos[0] - p[0], o.pos[2] - p[1], o.pos[1], self._floor_offset(o))
        self._fill_edit()
        self.status.set(self._describe(o))
        self._show_in_list(o)
        return True

    def _move_to(self, e):
        o, gx, gz, y0, off = self._moving
        p = self._world_under(e.x, e.y, y0)            # 3D: the plane at the height the drag started from
        if p is None:
            return
        x, z, y = p[0] + gx, p[1] + gz, y0
        if off is not None:
            # onto the highest floor at the new place that is at most 0.5 m above where the object was
            # (a stair or a low step, not a roof); does not depend on the way it was dragged
            f = self.view.floor_at(x, z, y0 + 0.5)
            if f is not None:
                y = f + off
        self._set_matrix(o, move_matrix(o.matrix, (x, y, z)))

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
        self._pivot = None
        self._moving = None
        if self.view and getattr(e, 'num', 1) == 1 and self._start_moving(e):
            return
        if self.view and self.mode3d.get():
            # turn around the point under the cursor; where nothing is drawn (or the full image is not ready),
            # around the point where the cursor ray meets the ground plane at the height of the target
            w, h = self._size()
            self._pivot = self.pick(e.x, e.y) or self.cam.ground(w, h, e.x + 0.5, e.y + 0.5, self.cam.target[1])

    # ------------------------------------------------------------------ keyboard
    def _key_down(self, e):
        if isinstance(getattr(e, 'widget', None), (tk.Entry, ttk.Entry)):
            return                                # typing a coordinate
        if e.keycode == 82 and self._editable():  # R / Shift+R: turn the object being edited
            self._edit_turn(-15 if e.state & 0x1 else 15)
            return
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
        if self._moving:
            self._move_to(e)
            return
        if self.mode3d.get():
            if pan or e.state & 0x1:              # middle button or Shift: move the point the camera looks at
                self._pivot = None
                self.cam.pan(dx, dy, self._size()[0])
            else:
                self.cam.orbit(-dx * 0.4, dy * 0.3, self._pivot)
            self._camera_changed()
            return
        self.canvas.move('all', dx, dy)
        self.cx -= dx / self.s
        self.cz += dy / self.s
        self._schedule()

    def _on_release(self, e, select=True):
        drag, self._drag = self._drag, None
        if self._pivot is not None:
            self._pivot = None
            self._draw()
        if self._moving:
            if drag and (drag[2], drag[3]) != (drag[0], drag[1]):
                self._move_to(e)
            self._moving = None
            return
        if not drag or not self.view or (drag[2], drag[3]) != (drag[0], drag[1]) or not select:
            return
        o = self._object_at(e.x, e.y)
        self.selected = o
        self._fill_edit()
        self._draw()
        if o is None:
            return
        self.status.set(self._describe(o))
        self._show_in_list(o)

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


class CatalogDialog(tk.Toplevel):
    """Objects found on the multiplayer maps (and the vehicles of the Bots and vehicles tab) to add to this map."""
    CATEGORIES = ('pickup', 'vehicle', 'object', 'plant')

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.entries = []
        self.title(_('Catalog'))
        self.geometry('900x560')
        self.transient(win)
        self.photo = None
        self._shown = None
        names = {'pickup': _('Weapons and items'), 'vehicle': _('Vehicles'), 'object': _('Objects'),
                 'plant': _('Plants')}
        self.cat_names = [('', _('All'))] + [(c, names[c]) for c in self.CATEGORIES]
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill='x')
        ttk.Label(bar, text=_('Category:')).pack(side='left')
        self.cat_box = ttk.Combobox(bar, state='readonly', width=20, values=[n for c, n in self.cat_names])
        self.cat_box.current(0)
        self.cat_box.pack(side='left', padx=4)
        self.cat_box.bind('<<ComboboxSelected>>', lambda e: self._fill())
        ttk.Label(bar, text=_('Search:')).pack(side='left', padx=(12, 4))
        self.search = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.search, width=24)
        e.pack(side='left')
        e.bind('<KeyRelease>', lambda ev: self._fill())
        body = ttk.Frame(self, padding=(6, 0))
        body.pack(fill='both', expand=True)
        cols = ('name', 'cls', 'cat', 'maps', 'note')
        self.tree = ttk.Treeview(body, columns=cols, show='headings', selectmode='browse')
        for c, t, w in (('name', _('Object'), 200), ('cls', _('Class'), 130), ('cat', _('Category'), 120),
                        ('maps', _('Maps'), 50), ('note', '', 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w' if c != 'maps' else 'center', stretch=c == 'name')
        sb = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        right = ttk.Frame(body, padding=(8, 0, 0, 0))
        right.pack(side='left', fill='y')
        self.preview = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, background='#1c1f24',
                                 highlightthickness=0)
        self.preview.pack()
        self.preview_note = ttk.Label(right, foreground='#808080', wraplength=PREVIEW_W)
        self.preview_note.pack(anchor='w', pady=(4, 0))
        self.tree.bind('<Double-Button-1>', lambda e: self._add())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._show())
        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, padding=(6, 4), wraplength=620, justify='left').pack(fill='x')
        btns = ttk.Frame(self, padding=6)
        btns.pack(fill='x')
        ttk.Button(btns, text=_('Close'), command=self.destroy).pack(side='right')
        ttk.Button(btns, text=_('Add to the map'), command=self._add).pack(side='right', padx=6)
        self.info.set(_('Loading the catalog ...'))
        game = win.app.game

        def work():
            from tsmap.catalog import catalog
            try:
                entries = catalog(game, win.app.lock)
            except Exception as ex:                     # noqa: BLE001 - shown in the dialog
                msg = str(ex)
                win.app.msgs.put(('call', lambda: self.winfo_exists() and self.info.set(msg)))
                return
            win.app.msgs.put(('call', lambda: self.winfo_exists() and self._loaded(entries)))
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, entries):
        self.entries = entries
        self._fill()
        self.info.set(_('The object is placed in the middle of the view, drag it into place. What it needs is '
                        'added to the map\'s preload list when the level is saved.'))

    def _fill(self):
        cat = self.cat_names[self.cat_box.current()][0]
        q = self.search.get().lower()
        names = dict(self.cat_names)
        here = self.win.cls
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.entries):
            if cat and e.category != cat or q and q not in e.label.lower() and q not in e.cls.lower():
                continue
            note = _('not tested') if not e.tested else _('on this map') if here in e.maps else ''
            self.tree.insert('', 'end', iid=str(i), values=(e.label, e.cls, names[e.category], len(e.maps), note))

    def _entry(self):
        sel = self.tree.selection()
        return self.entries[int(sel[0])] if sel else None

    def _show(self):
        e = self._entry()
        if e is not None:
            self._draw_preview(e)
            self.info.set('%s  (%s)  %s' % (e.label, e.cls or '—', _('maps: %s', ', '.join(e.maps[:8])
                                                                       + (' ...' if len(e.maps) > 8 else ''))
                                             if e.maps else _('not on any multiplayer map')))

    def _draw_preview(self, e):
        """The template of the entry, drawn in the background (a big vehicle takes a fraction of a second)."""
        self._shown = e
        self.preview.delete('all')
        self.preview_note.configure(text=_('Loading ...'))
        app = self.win.app
        templates = template_meshes(app.game, app.lock)

        def work():
            rgb = mesh_thumbnail(templates.get(e.tpl, e.cls), PREVIEW_W, PREVIEW_H)
            data = png_base64(rgb, PREVIEW_W, PREVIEW_H) if rgb else None
            app.msgs.put(('call', lambda: self.winfo_exists() and self._preview_ready(e, data)))
        threading.Thread(target=work, daemon=True).start()

    def _preview_ready(self, e, data):
        if e is not self._shown:
            return
        self.preview.delete('all')
        if data is None:
            self.photo = None
            self.preview_note.configure(text=_('No model: the object is only an effect or a marker in game.'))
            return
        self.photo = tk.PhotoImage(data=data)
        self.preview.create_image(0, 0, anchor='nw', image=self.photo)
        self.preview_note.configure(text='')

    def _add(self):
        e = self._entry()
        if e is not None and self.win.winfo_exists():
            self.win.add_from_catalog(e)


def _contains(poly, x, z):
    inside = False
    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]):
        if (az > z) != (bz > z) and x < ax + (z - az) * (bx - ax) / (bz - az):
            inside = not inside
    return inside


def _area(poly):
    return abs(sum(ax * bz - bx * az for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]))) / 2
