"""'Bots and vehicles' tab of the GUI: spare zones become spawners, pickups become vehicles (tsmap.spawn)."""
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from tsmap import MapToolError, _
from tsmap import spawn

KIND_NAMES = {'zone': 'sound zone', 'bots': 'bots', 'spawner': 'vehicle spawner', 'pickup': 'pickup',
              'vehicle': 'vehicle'}


def _fmt(p):
    return '%.1f, %.1f, %.1f' % tuple(p)


class SpawnTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.cls = None
        self.level = None          # spawn.MapLevel of the loaded map
        self.map_rows = []
        self.soldiers = []
        self.places = []           # [(label, (x, y, z))] for "take position from"
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        bar = ttk.Frame(self, padding=(0, 6))
        bar.pack(fill='x')
        ttk.Label(bar, text=_('Map:')).pack(side='left')
        self.map_var = tk.StringVar()
        self.map_box = ttk.Combobox(bar, textvariable=self.map_var, state='readonly', width=42)
        self.map_box.pack(side='left', padx=(4, 12))
        self.map_box.bind('<<ComboboxSelected>>', self._on_map)
        self.state_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.state_var, foreground='#b05000').pack(side='right')
        ttk.Label(self, foreground='#707070', wraplength=1100, justify='left', text=_(
            'A spare sound zone becomes a spawner, a pickup becomes a vehicle. Soldiers appear at random places of '
            'their zone when a player enters it and stand and shoot (moving needs navigation the multiplayer maps '
            'do not have). A vehicle spawner creates the vehicle 2 s after the start and again after it is '
            'destroyed. The resources are added to the map\'s preload list. Checked in single player (developer '
            'menu) only.')).pack(fill='x', pady=(0, 6))

        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True)

        left = ttk.Frame(body)
        body.add(left, weight=3)
        cols = ('name', 'kind', 'pos', 'size')
        self.tree = ttk.Treeview(left, columns=cols, show='tree headings', selectmode='browse')
        self.tree.column('#0', width=24, stretch=False)
        for c, t, w in (('name', _('Object'), 220), ('kind', _('What'), 120), ('pos', _('Position'), 150),
                        ('size', _('Size'), 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w', stretch=c == 'name')
        self.tree.tag_configure('added', foreground='#d08000')
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=2)
        self.sel_var = tk.StringVar(value=_('Select a zone or a pickup'))
        ttk.Label(right, textvariable=self.sel_var, font=('Segoe UI', 10, 'bold')).pack(anchor='w')

        kf = ttk.Frame(right)
        kf.pack(fill='x', pady=(6, 4))
        ttk.Label(kf, text=_('Make:')).pack(side='left')
        self.kind_var = tk.StringVar(value='bots')
        self.kind_btns = {}
        for k, t in (('bots', _('Bots')), ('spawner', _('Vehicle spawner')), ('vehicle', _('Vehicle'))):
            b = ttk.Radiobutton(kf, text=t, value=k, variable=self.kind_var, command=self._show_form)
            b.pack(side='left', padx=(6, 0))
            self.kind_btns[k] = b

        # bots
        self.bots_frame = ttk.LabelFrame(right, text=_('Bots'), padding=6)
        ttk.Label(self.bots_frame, text=_('Soldiers:')).grid(row=0, column=0, sticky='nw')
        lf = ttk.Frame(self.bots_frame)
        lf.grid(row=0, column=1, columnspan=3, sticky='ew')
        self.soldier_list = tk.Listbox(lf, selectmode='multiple', height=6, exportselection=False)
        ssb = ttk.Scrollbar(lf, orient='vertical', command=self.soldier_list.yview)
        self.soldier_list.configure(yscrollcommand=ssb.set)
        self.soldier_list.pack(side='left', fill='both', expand=True)
        ssb.pack(side='left', fill='y')
        ttk.Label(self.bots_frame, text=_('Weapons:')).grid(row=1, column=0, sticky='w', pady=(4, 0))
        wf = ttk.Frame(self.bots_frame)
        wf.grid(row=1, column=1, columnspan=3, sticky='w', pady=(4, 0))
        self.weapon_vars = {}
        for w in spawn.WEAPONS:
            v = tk.BooleanVar(value=w in ('arifle', 'shotgun'))
            ttk.Checkbutton(wf, text=w, variable=v).pack(side='left')
            self.weapon_vars[w] = v
        self.alive_var = tk.IntVar(value=3)
        self.kill_var = tk.IntVar(value=5)
        ttk.Label(self.bots_frame, text=_('At once:')).grid(row=2, column=0, sticky='w', pady=(4, 0))
        ttk.Spinbox(self.bots_frame, from_=1, to=8, width=5, textvariable=self.alive_var).grid(row=2, column=1, sticky='w', pady=(4, 0))
        ttk.Label(self.bots_frame, text=_('Next one after a kill, s:')).grid(row=2, column=2, sticky='e', pady=(4, 0))
        ttk.Spinbox(self.bots_frame, from_=1, to=120, width=5, textvariable=self.kill_var).grid(row=2, column=3, sticky='w', pady=(4, 0))
        self.bots_frame.columnconfigure(1, weight=1)

        # vehicles
        self.veh_frame = ttk.LabelFrame(right, text=_('Vehicle'), padding=6)
        ttk.Label(self.veh_frame, text=_('Vehicle:')).grid(row=0, column=0, sticky='w')
        self.veh_var = tk.StringVar()
        self.veh_box = ttk.Combobox(self.veh_frame, textvariable=self.veh_var, state='readonly', width=34,
                                    values=[self._veh_label(v) for v in spawn.VEHICLES])
        self.veh_box.current(0)
        self.veh_box.grid(row=0, column=1, sticky='w')
        self.respawn_lbl = ttk.Label(self.veh_frame, text=_('Respawn after destruction, s:'))
        self.respawn_lbl.grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.respawn_var = tk.IntVar(value=10)
        self.respawn_spin = ttk.Spinbox(self.veh_frame, from_=1, to=600, width=5, textvariable=self.respawn_var)
        self.respawn_spin.grid(row=1, column=1, sticky='w', pady=(4, 0))

        # position
        self.pos_frame = ttk.LabelFrame(right, text=_('Position'), padding=6)
        self.move_var = tk.BooleanVar(value=False)
        self.move_chk = ttk.Checkbutton(self.pos_frame, text=_('Move to'), variable=self.move_var, command=self._update_pos)
        self.move_chk.grid(row=0, column=0, sticky='w')
        self.xyz = [tk.StringVar() for _i in range(3)]
        self.size_var = tk.StringVar(value='6')
        pf = ttk.Frame(self.pos_frame)
        pf.grid(row=0, column=1, sticky='w')
        self.pos_entries = []
        for i, a in enumerate('XYZ'):
            ttk.Label(pf, text=a).pack(side='left', padx=(6 if i else 0, 2))
            e = ttk.Entry(pf, textvariable=self.xyz[i], width=7)
            e.pack(side='left')
            self.pos_entries.append(e)
        self.size_lbl = ttk.Label(pf, text=_('size'))
        self.size_lbl.pack(side='left', padx=(8, 2))
        self.size_entry = ttk.Entry(pf, textvariable=self.size_var, width=5)
        self.size_entry.pack(side='left')
        ttk.Label(self.pos_frame, text=_('Take from:')).grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.place_var = tk.StringVar()
        self.place_box = ttk.Combobox(self.pos_frame, textvariable=self.place_var, state='readonly', width=40)
        self.place_box.grid(row=1, column=1, sticky='w', pady=(4, 0))
        self.place_box.bind('<<ComboboxSelected>>', self._on_place)
        self.pos_note = ttk.Label(self.pos_frame, foreground='#707070', wraplength=420, justify='left')
        self.pos_note.grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))

        btns = ttk.Frame(right)
        btns.pack(side='bottom', fill='x', pady=(8, 0))
        self.buttons = [
            ttk.Button(btns, text=_('Add / change'), command=self._make),
            ttk.Button(btns, text=_('Undo'), command=self._undo),
            ttk.Button(btns, text=_('Apply'), command=self._apply),
        ]
        for b in self.buttons:
            b.pack(side='left', padx=(0, 6))
        self.app.buttons += self.buttons
        self._show_form()

    @staticmethod
    def _veh_label(v):
        tpl, _cls, checked = v
        return tpl + ('  ✓' if checked else '  (%s)' % _('not tested'))

    # ------------------------------------------------------------------ maps
    def refresh_maps(self, rows):
        self.map_rows = [r for r in rows if r['kind'] in ('original', 'custom', 'hidden')]
        self.map_box['values'] = ['%2d  %s  [%s]%s' % (r['id'], r['title'], r['class'], '  ✎' if r.get('edited') else '')
                                  for r in self.map_rows]
        if self.cls:
            for i, r in enumerate(self.map_rows):
                if r['class'] == self.cls:
                    self.map_box.current(i)

    def _on_map(self, event=None):
        i = self.map_box.current()
        if i >= 0 and self.map_rows[i]['class'] != self.cls:
            self.load_map(self.map_rows[i]['class'])

    def load_map(self, cls):
        self.state_var.set(_('Loading %s ...', cls))
        self.tree.delete(*self.tree.get_children())
        game = self.app.game

        def work():
            try:
                with self.app.lock:
                    data = game.level_data(cls)
                    orig = game.level_data(cls, original=True)
                    soldiers = sorted(e.name for e in game.orig['main'].entries
                                      if e.type == 12 and re.match(r'(soldier|police)_\d+(_\d+)?$|swat$|bfs$|qg_s', e.name))
                level = spawn.MapLevel(data, orig)
                self.app.msgs.put(('call', lambda: self._loaded(cls, level, soldiers)))
            except (MapToolError, ValueError, OSError) as e:
                msg = str(e)
                self.app.msgs.put(('call', lambda: (self.state_var.set(''), messagebox.showerror(_('Error'), msg))))
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, cls, level, soldiers):
        self.cls, self.level = cls, level
        if soldiers != self.soldiers:
            self.soldiers = soldiers
            self.soldier_list.delete(0, 'end')
            for i, s in enumerate(soldiers):
                self.soldier_list.insert('end', s)
                if s in spawn.DEFAULT_SOLDIERS:
                    self.soldier_list.selection_set(i)
        zones, pickups = level.zones(), level.pickups()
        self.places = ([(n, p) for n, p in level.start_positions()]
                       + [(p.name, p.pos) for p in pickups])
        self.place_box['values'] = ['%s  (%s)' % (n, _fmt(p)) for n, p in self.places]
        self.tree.delete(*self.tree.get_children())
        zp = self.tree.insert('', 'end', iid='#zones', text='', values=(_('Zones'), '', '', ''), open=True)
        for z in zones:
            w, d = z.size
            self.tree.insert(zp, 'end', iid='z:' + z.name, tags=('added',) if z.kind != 'zone' else (),
                             values=(z.name, _(KIND_NAMES[z.kind]), _fmt(z.center), '%.0f×%.0f' % (w, d)))
        pp = self.tree.insert('', 'end', iid='#pickups', text='', values=(_('Pickups'), '', '', ''), open=True)
        for p in pickups:
            what = _(KIND_NAMES[p.kind]) + (': ' + p.tpl if p.kind == 'vehicle' else '')
            self.tree.insert(pp, 'end', iid='p:' + p.name, tags=('added',) if p.kind == 'vehicle' else (),
                             values=(p.name, what, _fmt(p.pos), ''))
        n = len(level.additions())
        self.state_var.set(_('Added: %d', n) if n else '')
        self.app._say(_('%s: %d spare zones, %d pickups', cls, len(zones), len(pickups)))

    # ------------------------------------------------------------------ selection / form
    def _selected(self):
        sel = self.tree.selection()
        if not sel or sel[0].startswith('#') or not self.level:
            return None, None
        kind, name = sel[0].split(':', 1)
        objs = self.level.zones() if kind == 'z' else self.level.pickups()
        return kind, next((o for o in objs if o.name == name), None)

    def _on_select(self, event=None):
        kind, obj = self._selected()
        if obj is None:
            return
        self.sel_var.set(obj.name)
        if kind == 'z':
            self.kind_btns['bots'].state(['!disabled'])
            self.kind_btns['spawner'].state(['!disabled'])
            self.kind_btns['vehicle'].state(['disabled'])
            if self.kind_var.get() == 'vehicle':
                self.kind_var.set('spawner' if obj.kind == 'spawner' else 'bots')
            if obj.kind in ('bots', 'spawner'):
                self.kind_var.set(obj.kind)
            for v, c in zip(self.xyz, obj.center):
                v.set('%.1f' % c)
            self.size_var.set('%.0f' % max(2.0, min(obj.size)))
        else:
            for k in ('bots', 'spawner'):
                self.kind_btns[k].state(['disabled'])
            self.kind_btns['vehicle'].state(['!disabled'])
            self.kind_var.set('vehicle')
            for v, c in zip(self.xyz, obj.pos):
                v.set('%.1f' % c)
        self._show_form()

    def _show_form(self):
        for f in (self.bots_frame, self.veh_frame, self.pos_frame):
            f.pack_forget()
        k = self.kind_var.get()
        (self.bots_frame if k == 'bots' else self.veh_frame).pack(fill='x', pady=(4, 0))
        self.pos_frame.pack(fill='x', pady=(6, 0))
        spawner = k == 'spawner'
        for w in (self.respawn_lbl, self.respawn_spin):
            w.grid() if spawner else w.grid_remove()
        self._update_pos()

    def _update_pos(self):
        kind, obj = self._selected()
        zone = kind == 'z'
        movable = not zone or (obj is not None and obj.movable)
        if zone and not movable:
            self.move_var.set(False)
        self.move_chk.state(['!disabled'] if movable else ['disabled'])
        on = self.move_var.get() and movable
        for e in self.pos_entries + [self.size_entry, self.place_box]:
            e.state(['!disabled'] if on else ['disabled'])
        for w in (self.size_lbl, self.size_entry):
            w.pack(side='left') if zone else w.pack_forget()
        if zone and not movable:
            self.pos_note.configure(text=_('This zone has more than 4 corners and stays where it is.'))
        elif zone:
            self.pos_note.configure(text=_('The zone becomes a square of this size on the floor at Y. '
                                           'Bots appear inside it when a player enters it.'))
        else:
            self.pos_note.configure(text=_('By default the vehicle stands where the pickup was.'))

    def _on_place(self, event=None):
        i = self.place_box.current()
        if i >= 0:
            for v, c in zip(self.xyz, self.places[i][1]):
                v.set('%.1f' % c)

    def _place(self, zone):
        if not self.move_var.get():
            return None
        try:
            p = [float(v.get().replace(',', '.')) for v in self.xyz]
            if zone:
                p.append(max(1.0, float(self.size_var.get().replace(',', '.'))))
        except ValueError:
            raise MapToolError(_('Position: numbers expected'))
        return tuple(p)

    # ------------------------------------------------------------------ actions
    def _make(self):
        kind, obj = self._selected()
        if obj is None:
            messagebox.showinfo(_('Bots and vehicles'), _('Select a zone or a pickup'))
            return
        k = self.kind_var.get()
        try:
            place = self._place(kind == 'z')
        except MapToolError as e:
            messagebox.showerror(_('Error'), str(e))
            return
        cls, game, name = self.cls, self.app.game, obj.name
        if k == 'bots':
            soldiers = [self.soldiers[i] for i in self.soldier_list.curselection()]
            weapons = [w for w, v in self.weapon_vars.items() if v.get()]
            if not soldiers or not weapons:
                messagebox.showerror(_('Error'), _('Choose at least one soldier and one weapon'))
                return
            alive, after = int(self.alive_var.get()), int(self.kill_var.get())

            def build(level):
                return level.make_bots(name, soldiers, weapons, alive=alive, after_kill=after, place=place), \
                    [(s, '') for s in soldiers]
        else:
            tpl, vcls, _checked = spawn.VEHICLES[self.veh_box.current()]
            respawn = int(self.respawn_var.get())
            if k == 'spawner':
                def build(level):
                    return level.make_spawner(name, tpl, vcls, respawn=respawn, place=place), [(tpl, vcls)]
            else:
                pos = place[:3] if place else None

                def build(level):
                    return level.make_vehicle(name, tpl, vcls, pos), [(tpl, vcls)]

        def fn():
            level = spawn.MapLevel(game.level_data(cls), game.level_data(cls, original=True))
            try:
                data, templates = build(level)
            except ValueError as e:
                raise MapToolError(str(e))
            spawn.save_edit(game, cls, data, templates, self.app._say)
            self.app._say(_('%s: %s → %s', cls, name, _(KIND_NAMES['bots' if k == 'bots' else k])))
        self.app._run(_('Bots and vehicles'), fn, done=self._after_save)

    def _undo(self):
        kind, obj = self._selected()
        if obj is None or obj.kind not in ('bots', 'spawner', 'vehicle'):
            messagebox.showinfo(_('Bots and vehicles'), _('Select an added object (marked orange)'))
            return
        cls, game, name = self.cls, self.app.game, obj.name

        def fn():
            level = spawn.MapLevel(game.level_data(cls), game.level_data(cls, original=True))
            spawn.save_edit(game, cls, level.revert(name), (), self.app._say)
            self.app._say(_('%s: %s restored', cls, name))
        self.app._run(_('Bots and vehicles'), fn, done=self._after_save)

    def _apply(self):
        game = self.app.game
        self.app._run(_('Applying'), lambda: game.apply(self.app._say))

    def _after_save(self, err):
        if err or not self.cls:
            return
        cls = self.cls
        self.cls = None
        self.load_map(cls)
        self.app.scripts.reload_if_shown(cls)
