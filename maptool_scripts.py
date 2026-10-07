"""'Scripts' tab of the GUI: object properties and #ssl scripts of a level (.lg)."""
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from tsmap import MapToolError, _
from tsmap.lg import TEXT, LevelFile, check_script
from tsmap.mapview import apply_moves, edit_instances, instance_records
from tsmap.spawn import add_resources, placed_templates
from maptool_mapview import MapWindow

KEYWORDS = r'\b(override|func|if|else|end|return|var|not|and|or|true|false|while|for)\b'
SECTION_NAMES = {'scene': 'scene', 'templates': 'templates', 'effects': 'effects', 'other': 'other'}


class ScriptsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.level = None          # LevelFile of the loaded map
        self.cls = None            # its level name
        self.edits = {}            # object index -> edited text (not saved yet)
        self.current = None        # object index shown in the editor
        self.bare = []             # instances without a property text: (matrix offset, name, template, pos)
        self.current_bare = None   # matrix offset of the one shown
        self.new_texts = {}        # matrix offset -> property text written for an instance that has none
        self.functions = []
        self.map_rows = []
        self.saved_edited = False
        self._hl_job = None
        self.map_win = None        # MapWindow when open
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
        self.only_scripts = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=_('Only objects with scripts'), variable=self.only_scripts,
                        command=self._fill_objects).pack(side='left')
        ttk.Label(bar, text=_('Search:')).pack(side='left', padx=(12, 4))
        self.search_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.search_var, width=22)
        e.pack(side='left')
        e.bind('<KeyRelease>', lambda ev: self._fill_objects())
        ttk.Button(bar, text=_('Map'), command=self._open_map).pack(side='left', padx=(12, 0))
        self.state_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.state_var, foreground='#b05000').pack(side='right')

        btns = ttk.Frame(self)
        btns.pack(fill='x', pady=(0, 6))
        self.buttons = [
            ttk.Button(btns, text=_('Save changes'), command=self._save),
            ttk.Button(btns, text=_('Save and apply to the game'), command=lambda: self._save(apply=True)),
            ttk.Button(btns, text=_('Undo object changes'), command=self._revert_object),
            ttk.Button(btns, text=_('Restore original level'), command=self._restore_level),
        ]
        for b in self.buttons:
            b.pack(side='left', padx=(0, 6))
        self.app.buttons += self.buttons

        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True)

        left = ttk.Frame(body)
        body.add(left, weight=2)
        cols = ('name', 'template', 'section', 'script', 'changed')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', selectmode='browse')
        for c, t, w, a in (('name', _('Object'), 170, 'w'), ('template', _('Template'), 110, 'w'),
                           ('section', _('Section'), 70, 'w'), ('script', 'ssl', 32, 'center'),
                           ('changed', '✎', 26, 'center')):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=c == 'name')
        self.tree.tag_configure('changed', foreground='#d08000')
        self.tree.tag_configure('bare', foreground='#808080')
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_object)

        mid = ttk.Frame(body)
        body.add(mid, weight=5)
        self.obj_var = tk.StringVar()
        ttk.Label(mid, textvariable=self.obj_var).pack(anchor='w')
        ed = ttk.Frame(mid)
        ed.pack(fill='both', expand=True)
        self.text = tk.Text(ed, wrap='none', undo=True, font=('Consolas', 10), background='#1e1e1e',
                            foreground='#d4d4d4', insertbackground='#ffffff', selectbackground='#264f78',
                            tabs=('1c',))
        ys = ttk.Scrollbar(ed, orient='vertical', command=self.text.yview)
        xs = ttk.Scrollbar(ed, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        ed.rowconfigure(0, weight=1)
        ed.columnconfigure(0, weight=1)
        for tag, color in (('kw', '#569cd6'), ('obj', '#4ec9b0'), ('str', '#ce9178'), ('com', '#6a9955'),
                           ('num', '#b5cea8'), ('sect', '#c586c0')):
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure('sect', font=('Consolas', 10, 'bold'))
        self.text.bind('<<Modified>>', self._on_modified)
        self.text.configure(state='disabled')

        right = ttk.Frame(body)
        body.add(right, weight=2)
        ttk.Label(right, text=_('Built-in functions')).pack(anchor='w')
        self.fn_var = tk.StringVar()
        fe = ttk.Entry(right, textvariable=self.fn_var)
        fe.pack(fill='x')
        fe.bind('<KeyRelease>', lambda ev: self._fill_functions())
        fl = ttk.Frame(right)
        fl.pack(fill='both', expand=True)
        self.fn_list = tk.Listbox(fl, activestyle='none', font=('Consolas', 9))
        fsb = ttk.Scrollbar(fl, orient='vertical', command=self.fn_list.yview)
        self.fn_list.configure(yscrollcommand=fsb.set)
        self.fn_list.pack(side='left', fill='both', expand=True)
        fsb.pack(side='left', fill='y')
        self.fn_list.bind('<Double-Button-1>', self._insert_function)
        ttk.Label(right, text=_('Double click inserts the call'), foreground='#808080').pack(anchor='w')

    # ------------------------------------------------------------------ maps
    def refresh_maps(self, rows):
        """Called by the app whenever the map list changes. The single player levels follow the multiplayer
        maps (an experiment: the same viewing and editing)."""
        self.map_rows = [r for r in rows if r['kind'] in ('original', 'custom', 'hidden')]
        try:
            self.map_rows += self.app.game.campaign_maps() if self.app.game else []
        except Exception:                 # noqa: BLE001 - the multiplayer maps are listed anyway
            pass
        self.map_box['values'] = ['%2d  %s  [%s]%s%s' % (r['id'], r['title'], r['class'],
                                                        '  — ' + _('campaign') if r['kind'] == 'campaign' else '',
                                                        '  ✎' if r.get('edited') else '')
                                  for r in self.map_rows]
        if self.cls:
            for i, r in enumerate(self.map_rows):
                if r['class'] == self.cls:
                    self.map_box.current(i)
        if not self.functions and self.app.game:
            threading.Thread(target=self._load_functions, daemon=True).start()

    def _load_functions(self):
        try:
            funcs = self.app.game.script_functions()
        except Exception:      # the reference is optional
            funcs = []
        self.app.msgs.put(('call', lambda: self._set_functions(funcs)))

    def _set_functions(self, funcs):
        self.functions = funcs
        self._fill_functions()

    def _fill_functions(self):
        q = self.fn_var.get().lower()
        self.fn_list.delete(0, 'end')
        for f in self.functions:
            if q in f.lower():
                self.fn_list.insert('end', f)

    def _insert_function(self, event=None):
        sel = self.fn_list.curselection()
        if not sel or str(self.text['state']) == 'disabled':
            return
        sig = self.fn_list.get(sel[0])
        name = sig.split('(', 1)[0]
        self.text.insert('insert', name + ('()' if sig.split('(', 1)[1].startswith(')') else '('))
        self.text.focus_set()

    def reload_if_shown(self, cls):
        """The level was changed elsewhere (Bots and vehicles tab): show the new one unless edits are pending."""
        if self.cls == cls and not self.unsaved():
            self.cls = None
            self.load_map(cls)

    def unsaved(self):
        self._store_current()
        return bool(self.edits or self.new_texts or self._map_pending())

    def _map_pending(self):
        """Number of changes in the map window (moved / added / deleted objects, flags) not saved yet."""
        return self.map_win.pending() if self._map_open() else 0

    def _on_map(self, event=None):
        i = self.map_box.current()
        if i < 0:
            return
        cls = self.map_rows[i]['class']
        if cls == self.cls:
            return
        if self.unsaved() and not messagebox.askyesno(_('Scripts'), _('Discard unsaved changes in %s?', self.cls)):
            self.refresh_maps(self.app.rows_list)
            return
        self.load_map(cls)

    def load_map(self, cls):
        self.state_var.set(_('Loading %s ...', cls))
        self._clear()
        game = self.app.game

        def work():
            try:
                with self.app.lock:
                    data = game.level_data(cls)
                    edited = game.level_override(cls) is not None
                level = LevelFile(data)
                self.app.msgs.put(('call', lambda: self._loaded(cls, level, edited)))
            except (MapToolError, ValueError, OSError) as e:
                msg = str(e)
                self.app.msgs.put(('call', lambda: self._load_failed(cls, msg)))
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, cls, level, edited):
        self.cls, self.level, self.edits, self.current = cls, level, {}, None
        self.new_texts = {}
        # instances without a property text (most pickups, start points ...): listed read-only, for the map
        self.bare = [(r.mpos, r.name, r.tpl or r.cls, r.matrix[12:15]) for r in instance_records(level)
                     if not any(tag == TEXT for tag, payload in r.children)]
        self.current_bare = None
        n = sum(o.has_script for o in level.objects)
        self.saved_edited = edited
        self._fill_objects()
        self._update_state()
        self.app._say(_('%s: %d objects with properties, %d with scripts', cls, len(level.objects), n))
        if self._map_open():
            self.map_win.set_level(cls, level)

    def _load_failed(self, cls, msg):
        self.state_var.set('')
        messagebox.showerror(_('Error'), msg)

    # ------------------------------------------------------------------ objects
    def _clear(self):
        self.tree.delete(*self.tree.get_children())
        self._set_text('', editable=False)
        self.obj_var.set('')

    def _visible_objects(self):
        if not self.level:
            return []
        q = self.search_var.get().lower()
        out = []
        for o in self.level.objects:
            text = self.edits.get(o.index, o.text)
            if self.only_scripts.get() and not re.search(r'(?m)^#ssl\s*$', text):
                continue
            if q and q not in o.name.lower() and q not in o.template.lower() and q not in text.lower():
                continue
            out.append(o)
        return out

    def _fill_objects(self):
        self._store_current()
        self.tree.delete(*self.tree.get_children())
        for o in self._visible_objects():
            changed = o.index in self.edits
            text = self.edits.get(o.index, o.text)
            self.tree.insert('', 'end', iid=str(o.index), tags=('changed',) if changed else (),
                             values=(o.name, o.template, _(SECTION_NAMES.get(o.section, o.section)),
                                     '✓' if re.search(r'(?m)^#ssl\s*$', text) else '', '●' if changed else ''))
        if not self.only_scripts.get():
            q = self.search_var.get().lower()
            for mpos, name, ref, _pos in self.bare:
                if not q or q in name.lower() or q in ref.lower():
                    text = self.new_texts.get(mpos)
                    self.tree.insert('', 'end', iid='x%d' % mpos, tags=('changed',) if text else ('bare',),
                                     values=(name, ref, _(SECTION_NAMES['effects']),
                                             '✓' if text and re.search(r'(?m)^#ssl\s*$', text) else '',
                                             '●' if text else ''))
        iid = 'x%d' % self.current_bare if self.current_bare is not None else str(self.current)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _on_object(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        if sel[0].startswith('x'):
            self._on_bare(int(sel[0][1:]))
            return
        idx = int(sel[0])
        if idx == self.current:
            return
        self._store_current()
        self.current, self.current_bare = idx, None
        o = self.level.objects[idx]
        self.obj_var.set('%s   (%s, %s)' % (o.name, o.template or '—', _(SECTION_NAMES.get(o.section, o.section))))
        self._set_text(self.edits.get(idx, o.text).replace('\r\n', '\n'), editable=True)
        if self._map_open():
            self.map_win.highlight_text(idx)

    def _on_bare(self, mpos):
        """An instance without a property text: what is typed here becomes its text (and script) when saved."""
        if mpos == self.current_bare and self.current is None:
            return
        self._store_current()
        self.current, self.current_bare = None, mpos
        name, ref, pos = next((n, r, p) for m, n, r, p in self.bare if m == mpos)
        if self._map_open() and mpos in self.map_win.moves:
            pos = self.map_win.moves[mpos][12:15]
        self.obj_var.set('%s   (%s, %s)   %s   %s' % (name, ref or '—', _(SECTION_NAMES['effects']),
                                                     _('Position: %.2f, %.2f, %.2f', *pos),
                                                     _('no property text yet: what you type here becomes it')))
        self._set_text(self.new_texts.get(mpos, ''), editable=True)
        if self._map_open():
            self.map_win.highlight_instance(mpos)

    def show_instance(self, mpos):
        """An instance without a text was clicked on the map: select its read-only row."""
        iid = 'x%d' % mpos
        if not self.tree.exists(iid):
            self.only_scripts.set(False)
            self.search_var.set('')
            self._fill_objects()
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    # ------------------------------------------------------------------ map window
    def _map_open(self):
        return self.map_win is not None and self.map_win.winfo_exists()

    def _open_map(self):
        if not self.level:
            messagebox.showinfo(_('Map'), _('Select a map first'))
            return
        if self._map_open():
            self.map_win.lift()
            return
        self.map_win = MapWindow(self)
        self.map_win.set_level(self.cls, self.level)
        if self.current is not None:
            self.map_win.highlight_text(self.current)
        elif self.current_bare is not None:
            self.map_win.highlight_instance(self.current_bare)

    def moves_changed(self):
        """The map window moved an object or dropped a move."""
        self._update_state()

    def show_object(self, idx):
        """An object was clicked on the map: select it in the list (the filters are cleared when they hide it)."""
        if not self.tree.exists(str(idx)):
            self.only_scripts.set(False)
            self.search_var.set('')
            self._fill_objects()
        self.tree.selection_set(str(idx))
        self.tree.see(str(idx))

    def _store_current(self):
        """Keep the editor content of the current object in self.edits (self.new_texts for an instance that
        has no text yet)."""
        if self.level is None or str(self.text['state']) == 'disabled':
            return
        if self.current is None and self.current_bare is not None:
            text = self.text.get('1.0', 'end-1c')
            mpos = self.current_bare
            if text.strip():
                self.new_texts[mpos] = text
            else:
                self.new_texts.pop(mpos, None)
            if self.tree.exists('x%d' % mpos):
                vals = list(self.tree.item('x%d' % mpos, 'values'))
                vals[3] = '✓' if re.search(r'(?m)^#ssl\s*$', text) else ''
                vals[4] = '●' if mpos in self.new_texts else ''
                self.tree.item('x%d' % mpos, values=vals, tags=('changed',) if mpos in self.new_texts else ('bare',))
            self._update_state()
            return
        if self.current is None:
            return
        o = self.level.objects[self.current]
        text = self.text.get('1.0', 'end-1c')
        if text == o.text.replace('\r\n', '\n'):
            self.edits.pop(self.current, None)
        else:
            self.edits[self.current] = text
        self._mark_row(self.current)
        self._update_state()

    def _mark_row(self, idx):
        if self.tree.exists(str(idx)):
            changed = idx in self.edits
            vals = list(self.tree.item(str(idx), 'values'))
            vals[4] = '●' if changed else ''
            self.tree.item(str(idx), values=vals, tags=('changed',) if changed else ())

    def _update_state(self):
        on_map = self._map_pending() if self.level else 0
        if not self.level:
            self.state_var.set('')
        elif self.edits or self.new_texts or on_map:
            changed = len(self.edits) + len(self.new_texts)
            self.state_var.set(', '.join(
                ([_('Changed objects: %d (not saved)', changed)] if changed else [])
                + ([_('Map changes: %d (not saved)', on_map)] if on_map else [])))
        elif self.saved_edited:
            self.state_var.set(_('This level has saved edits'))
        else:
            self.state_var.set(_('Original level'))

    # ------------------------------------------------------------------ editor
    def _set_text(self, text, editable):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._highlight()
        if not editable:
            self.text.configure(state='disabled')

    def _on_modified(self, event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._hl_job:
            self.after_cancel(self._hl_job)
        self._hl_job = self.after(250, self._after_edit)

    def _after_edit(self):
        self._hl_job = None
        self._highlight()
        self._store_current()

    def _highlight(self):
        t = self.text
        for tag in ('kw', 'obj', 'str', 'com', 'num', 'sect'):
            t.tag_remove(tag, '1.0', 'end')
        src = t.get('1.0', 'end-1c')
        m = re.search(r'(?m)^#ssl\s*$', src)
        code_from = m.start() if m else len(src)

        def mark(tag, a, b):
            t.tag_add(tag, '1.0+%dc' % a, '1.0+%dc' % b)
        if m:
            mark('sect', m.start(), m.end())
        for mm in re.finditer(r'-?\b\d+(\.\d+)?\b', src):
            mark('num', mm.start(), mm.end())
        for mm in re.finditer(KEYWORDS, src[code_from:]):
            mark('kw', code_from + mm.start(), code_from + mm.end())
        for mm in re.finditer(r'\$[\w|]+', src):
            mark('obj', mm.start(), mm.end())
        for mm in re.finditer(r'"[^"\n]*"', src):
            mark('str', mm.start(), mm.end())
        for mm in re.finditer(r'//[^\n]*', src):
            mark('com', mm.start(), mm.end())

    # ------------------------------------------------------------------ actions
    def _revert_object(self):
        if self.current is None and self.current_bare is not None:      # drop the text written for it
            self.new_texts.pop(self.current_bare, None)
            self._set_text('', editable=True)
            self._store_current()
            return
        if self.current is None:
            return
        self.edits.pop(self.current, None)
        o = self.level.objects[self.current]
        self._set_text(o.text.replace('\r\n', '\n'), editable=True)
        self._mark_row(self.current)
        self._update_state()

    def save(self, apply=False):
        """Save the text edits and the objects moved on the map together (called by the map window too)."""
        self._save(apply)

    def _save(self, apply=False):
        if not self.level:
            return
        self._store_current()
        moves, adds, deletes, flags, resources = (self.map_win.changes() if self._map_open()
                                                  else ({}, [], set(), {}, []))
        names = {mpos: name for mpos, name, _ref, _pos in self.bare}
        texts = {names[m]: t for m, t in self.new_texts.items() if m in names}
        if not self.edits and not texts and not (moves or adds or deletes or flags) and not apply:
            return
        problems = []
        checked = [(self.level.objects[idx].name, text) for idx, text in self.edits.items()] + list(texts.items())
        for name, text in checked:
            for line, kind in check_script(text):
                msg = {'end': _('extra "end"'), 'unclosed': _('a block is not closed with "end"'),
                       'quotes': _('unbalanced quotes')}[kind]
                problems.append('%s: %s%s' % (name, msg, _(' (line %d)', line) if line else ''))
        if problems and not messagebox.askyesno(_('Check'), _('Possible problems:') + '\n\n' + '\n'.join(problems[:15])
                                                + '\n\n' + _('Save anyway?')):
            return
        try:
            for idx, text in self.edits.items():
                self.level.encode_text(self.level.objects[idx], text)
            for text in texts.values():
                text.encode('latin1')
        except UnicodeEncodeError:
            messagebox.showerror(_('Error'), _('The text contains characters the game cannot store (use Latin letters only).'))
            return
        cls, game, level, edits = self.cls, self.app.game, self.level, dict(self.edits)

        def fn():
            data = None
            if moves:                                # same size: the matrices are written in place first
                data = apply_moves(level.data, moves)
            if edits:                                # the object indices do not change with the moves
                data = (LevelFile(data) if data else level).replace_texts(edits)
            if adds or deletes or flags or texts:    # by instance name: last, it changes the structure
                data = edit_instances(LevelFile(data) if data else level, adds, deletes, flags, texts)
            if data is not None:
                LevelFile(data)                      # the result must parse again before it is stored
                game.save_level_override(cls, data, self.app._say)
                if edits:
                    self.app._say(_('%s: %d objects changed', cls, len(edits)))
                if moves:
                    self.app._say(_('%s: %d objects moved', cls, len(moves)))
                if adds or deletes or flags:
                    self.app._say(_('%s: %d objects added, %d deleted, flags of %d changed', cls, len(adds),
                                    len(deletes), len(flags)))
                if texts:
                    self.app._say(_('%s: property texts written for %d objects', cls, len(texts)))
            # objects from the catalog: templates, textures, sounds; with them every object added by an earlier
            # save (its list may lack what a newer version of the tool finds; "Save and apply" alone fixes it)
            extra = [t for t in placed_templates(game, cls, data or level.data) if t not in resources]
            if resources or extra:
                add_resources(game, cls, list(resources) + extra, self.app._say)
            if apply:
                game.apply(self.app._say)
        def done(err):
            if not err:                              # reload the saved level; keep the edits on failure
                self.edits = {}
                if self._map_open():
                    self.map_win.forget_changes()
                self.cls = None
                self.load_map(cls)
        self.app._run(_('Saving scripts'), fn, done=done)

    def _restore_level(self):
        if not self.cls:
            return
        if not messagebox.askyesno(_('Scripts'), _('Drop all edits of %s and use the original level?', self.cls)):
            return
        cls, game = self.cls, self.app.game

        def fn():
            if not game.drop_level_override(cls, self.app._say):
                self.app._say(_('%s has no saved edits', cls))
        def done(err):
            if not err:
                self.edits = {}
                if self._map_open():
                    self.map_win.forget_changes()
                self.cls = None
                self.load_map(cls)
        self.app._run(_('Restoring the level'), fn, done=done)
