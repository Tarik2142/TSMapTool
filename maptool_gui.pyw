"""TimeShift map manager, graphical interface (tkinter)."""
import base64
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tsmap import MapToolError, find_game_dir, open_game, save_game_dir  # noqa: E402

KIND = {'original': 'оригінал', 'custom': 'додана', 'hidden': 'прихована', 'broken': 'пошкоджена'}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('TimeShift — менеджер карт')
        self.geometry('1300x740')
        self.minsize(900, 560)
        self.game = None
        self.busy = False
        self.msgs = queue.Queue()
        self.details_cache = {}
        self.photo = None
        self.rows = {}
        self.lock = threading.Lock()   # archive handles are shared between threads
        self._build()
        self.after(100, self._poll)
        self.after(50, self._open_game)

    # ------------------------------------------------------------- layout
    def _build(self):
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill='x')
        ttk.Label(top, text='Папка гри:').pack(side='left')
        self.game_var = tk.StringVar(value='—')
        ttk.Label(top, textvariable=self.game_var).pack(side='left', padx=6)
        ttk.Button(top, text='Змінити…', command=self._choose_game).pack(side='left')
        self.pending_var = tk.StringVar()
        ttk.Label(top, textvariable=self.pending_var, foreground='#b05000').pack(side='right')

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill='x')
        self.buttons = [
            ttk.Button(bar, text='Імпорт файлу…', command=self._import_files),
            ttk.Button(bar, text='Імпорт папки…', command=self._import_folder),
            ttk.Button(bar, text='Експорт…', command=self._export),
            ttk.Button(bar, text='Видалити / приховати', command=self._remove),
            ttk.Button(bar, text='Повернути приховану', command=self._unhide),
            ttk.Button(bar, text='Застосувати', command=self._apply),
            ttk.Button(bar, text='Відновити оригінал гри', command=self._restore),
        ]
        for b in self.buttons:
            b.pack(side='left', padx=(0, 6))

        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=8)
        left = ttk.Frame(body)
        body.add(left, weight=3)
        cols = ('id', 'title', 'class', 'modes', 'players', 'kind')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', selectmode='extended')
        for c, t, w, a in (('id', 'ID', 36, 'e'), ('title', 'Назва', 210, 'w'), ('class', 'Рівень', 130, 'w'),
                           ('modes', 'Режими', 160, 'w'), ('players', 'Гравці', 56, 'center'), ('kind', 'Стан', 80, 'w')):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=c == 'title')
        self.tree.tag_configure('custom', foreground='#2e9e2e')
        self.tree.tag_configure('hidden', foreground='#909090')
        self.tree.tag_configure('broken', foreground='#c00000')
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=0)
        self.img = ttk.Label(right, text='', anchor='center', width=72)
        self.img.pack(fill='x')
        self.name_var = tk.StringVar()
        ttk.Label(right, textvariable=self.name_var, font=('Segoe UI', 11, 'bold'), wraplength=420).pack(anchor='w', pady=(8, 2))
        self.meta_var = tk.StringVar()
        ttk.Label(right, textvariable=self.meta_var, wraplength=420, justify='left').pack(anchor='w')
        self.desc = tk.Text(right, height=7, wrap='word', relief='flat', font=('Segoe UI', 10),
                            background=self.cget('background'), foreground=ttk.Style().lookup('TLabel', 'foreground') or 'black')
        self.desc.pack(fill='both', expand=True, pady=(6, 0))
        self.desc.configure(state='disabled')

        self.log = tk.Text(self, height=8, wrap='word', state='disabled')
        self.log.pack(fill='x', padx=8, pady=8)

    # ------------------------------------------------------------- helpers
    def _say(self, msg):
        self.msgs.put(('log', msg))

    def _poll(self):
        try:
            while True:
                kind, payload = self.msgs.get_nowait()
                if kind == 'log':
                    self.log.configure(state='normal')
                    self.log.insert('end', payload + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
                elif kind == 'call':
                    payload()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, title, fn, refresh=True):
        """Run fn() in a worker thread, report errors, then refresh the list."""
        if self.busy or not self.game:
            return
        self.busy = True
        for b in self.buttons:
            b.state(['disabled'])
        self._say('— %s' % title)

        def work():
            err = None
            try:
                with self.lock:
                    fn()
            except MapToolError as e:
                err = str(e)
            except Exception as e:  # unexpected: show it instead of dying silently
                err = '%s: %s' % (type(e).__name__, e)
            self.msgs.put(('call', lambda: self._done(err, refresh)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, err, refresh):
        self.busy = False
        for b in self.buttons:
            b.state(['!disabled'])
        if err:
            self._say('Помилка: %s' % err)
            messagebox.showerror('Помилка', err)
        if refresh:
            self.details_cache.clear()
            self._refresh()

    def _selected(self):
        return [self.tree.item(i, 'values')[2] for i in self.tree.selection()]

    # ------------------------------------------------------------- game
    def _open_game(self, path=None):
        try:
            if self.game:
                self.game.close()
            d = path or find_game_dir()
            if not d:
                d = filedialog.askdirectory(title='Вкажіть папку TimeShift')
                if not d:
                    self.destroy()
                    return
            self.game = open_game(d)
            save_game_dir(self.game.dir)
            self.game_var.set(self.game.dir)
            self.game.ensure_backup(self._say)
        except MapToolError as e:
            messagebox.showerror('Помилка', str(e))
            self.game = None
            return
        self._refresh()

    def _choose_game(self):
        d = filedialog.askdirectory(title='Вкажіть папку TimeShift')
        if d:
            self._open_game(d)

    def _refresh(self):
        if not self.game:
            return
        sel = set(self._selected())
        try:
            with self.lock:
                rows = self.game.maps()
                pending = self.game.pending()
        except MapToolError as e:
            messagebox.showerror('Помилка', str(e))
            return
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            iid = self.tree.insert('', 'end', values=(r['id'], r['title'], r['class'], ' '.join(r['modes']),
                                                     r['players'], KIND.get(r['kind'], r['kind'])), tags=(r['kind'],))
            if r['class'] in sel:
                self.tree.selection_add(iid)
        self.rows = {r['class']: r for r in rows}
        self.pending_var.set('Є незастосовані зміни — натисніть «Застосувати»' if pending else '')

    # ------------------------------------------------------------- details
    def _on_select(self, _=None):
        sel = self._selected()
        if len(sel) != 1:
            return
        cls = sel[0]
        r = self.rows.get(cls, {})
        self.name_var.set(r.get('title', cls))
        meta = 'Рівень: %s   ID: %s   Стан: %s\nРежими: %s   Гравці: %s' % (
            cls, r.get('id'), KIND.get(r.get('kind'), ''), ' '.join(r.get('modes', [])), r.get('players', ''))
        if r.get('source'):
            meta += '\nДжерело: %s' % r['source']
        if r.get('error'):
            meta += '\nПомилка: %s' % r['error']
        self.meta_var.set(meta)
        if cls in self.details_cache:
            self._show_details(cls, *self.details_cache[cls])
            return
        self._set_desc('…')
        self.img.configure(image='', text='')

        def work():
            try:
                with self.lock:
                    desc, png = self.game.map_details(cls)
            except Exception as e:
                desc, png = 'Не вдалося прочитати: %s' % e, None
            self.details_cache[cls] = (desc, png)
            self.msgs.put(('call', lambda: self._show_details(cls, desc, png)))
        threading.Thread(target=work, daemon=True).start()

    def _show_details(self, cls, desc, png):
        if self._selected() != [cls]:
            return
        self._set_desc(desc or '')
        if png:
            self.photo = tk.PhotoImage(data=base64.b64encode(png))
            self.img.configure(image=self.photo, text='')
        else:
            self.photo = None
            self.img.configure(image='', text='(немає превʼю)')

    def _set_desc(self, text):
        self.desc.configure(state='normal')
        self.desc.delete('1.0', 'end')
        self.desc.insert('1.0', text)
        self.desc.configure(state='disabled')

    # ------------------------------------------------------------- actions
    def _import_files(self):
        paths = filedialog.askopenfilenames(
            title='Імпорт карт: Xbox 360 DLC-пакет або .tsmap',
            filetypes=[('Усі файли (Xbox DLC-пакети без розширення)', '*'), ('Карти TimeShift', '*.tsmap')])
        if paths:
            self._do_import(list(paths))

    def _import_folder(self):
        d = filedialog.askdirectory(title='Розпакована папка Xbox DLC (з .s3dpak / .s3dlst)')
        if d:
            self._do_import([d])

    def _do_import(self, paths):
        def fn():
            added = []
            for p in paths:
                added += self.game.import_path(p, log=self._say)
            if added:
                self.game.apply(self._say)
            else:
                self._say('Нових карт не додано')
        self._run('Імпорт', fn)

    def _export(self):
        sel = self._selected()
        if not sel:
            messagebox.showinfo('Експорт', 'Виберіть карту в списку')
            return
        if len(sel) == 1:
            out = filedialog.asksaveasfilename(title='Експорт карти', defaultextension='.tsmap',
                                               initialfile=sel[0] + '.tsmap', filetypes=[('Карти TimeShift', '*.tsmap')])
            targets = [(sel[0], out)] if out else []
        else:
            d = filedialog.askdirectory(title='Папка для експорту')
            targets = [(c, os.path.join(d, c + '.tsmap')) for c in sel] if d else []
        if targets:
            self._run('Експорт', lambda: [self.game.export_map(c, o, self._say) for c, o in targets], refresh=False)

    def _remove(self):
        sel = self._selected()
        if not sel:
            messagebox.showinfo('Видалення', 'Виберіть карту в списку')
            return
        custom = [c for c in sel if self.rows[c]['kind'] in ('custom', 'broken')]
        orig = [c for c in sel if self.rows[c]['kind'] == 'original']
        if not custom and not orig:
            return
        text = []
        if custom:
            text.append('Видалити додані карти:\n  ' + '\n  '.join(custom))
        if orig:
            text.append('Приховати з меню оригінальні карти (їх можна повернути):\n  ' + '\n  '.join(orig))
        if not messagebox.askyesno('Підтвердження', '\n\n'.join(text)):
            return

        def fn():
            for c in custom + orig:
                self.game.remove_map(c, self._say)
            self.game.apply(self._say)
        self._run('Видалення', fn)

    def _unhide(self):
        sel = [c for c in self._selected() if self.rows[c]['kind'] == 'hidden']
        if not sel:
            messagebox.showinfo('Повернення', 'Виберіть приховану оригінальну карту')
            return

        def fn():
            for c in sel:
                self.game.unhide_map(c, self._say)
            self.game.apply(self._say)
        self._run('Повернення карт', fn)

    def _apply(self):
        self._run('Застосування', lambda: self.game.apply(self._say))

    def _restore(self):
        if messagebox.askyesno('Відновлення', 'Повернути оригінальні архіви гри?\n\n'
                               'Бібліотека карт залишиться — карти можна повернути кнопкою «Застосувати».'):
            self._run('Відновлення оригіналу', lambda: self.game.restore(self._say))


if __name__ == '__main__':
    App().mainloop()
