"""Catalog of objects that can be added to a multiplayer map: every instance type found on the multiplayer maps
(weapons and items, objects, plants ...) plus the vehicles of the Bots and vehicles tab.

An entry keeps one instance record of a map that has it; a new object is that record with a new name and matrix
(see mapview.edit_instances). Its script part (#ssl) is dropped, since it may call objects of the other map, and
so are the flags that pick teams and game modes. The resources it needs are added to the preload list when the
level is saved (spawn.add_resources): every map already loads all weapons, an item pickup only needs its own
template.
"""
import collections
import re
import struct

from .lg import TEXT
from .mapview import IDENT, Instance, instance_kind, scan_instances
from .spawn import VEHICLES

SKIP_KINDS = ('effect', 'sound', 'start', 'flag')
SKIP_CLASSES = ('', 'dynamic', 'light_zones')     # markers whose role comes from a map's scripts


class Entry:
    __slots__ = ('kind', 'tpl', 'cls', 'record', 'maps', 'count', 'tested')

    @property
    def label(self):
        return self.tpl or self.cls

    @property
    def category(self):
        if self.kind in ('pickup', 'vehicle'):
            return self.kind
        return 'plant' if self.cls == 'tree' else 'object'


def _clean(rec):
    """The record as it is copied to another map: no script part, no team / mode flags."""
    children = []
    for tag, payload in rec.children:
        if tag == TEXT:
            text = payload.split(b'\0', 1)[0]
            m = re.search(rb'(?m)^#ssl\s*$', text)
            if m:
                text = text[:m.start()].rstrip() + b'\r\n'
            if not text.strip():
                continue
            payload = text + b'\0'
        children.append((tag, payload))
    flags = tuple(f for f in rec.flags if not f.startswith(('notIN_', 'swMP_')))
    return rec._replace(children=children, flags=flags)


def _score(rec):
    """Prefer a plain record: no script, few flags."""
    script = any(tag == TEXT and b'#ssl' in payload for tag, payload in rec.children)
    return (script, len(rec.flags), len(rec.children))


def build_catalog(game, lock=None):
    """[Entry] sorted by category and name; built from every multiplayer map (about two seconds). `lock` guards
    the archive reads when other threads read them too."""
    maps = [r['class'] for r in game.maps() if r.get('kind') in ('original', 'custom', 'hidden')]
    # (kind, name) -> {(template, class): [best record, maps, count]}: one object can be written either way
    # (item_mp_arifle as a class on most maps, as a template on some DLC maps); the most used way is taken
    variants = collections.defaultdict(dict)
    for cls in maps:
        try:
            if lock is not None:
                with lock:
                    data = game.level_data(cls)
            else:
                data = game.level_data(cls)
            recs, protos = scan_instances(data)
        except Exception:                 # noqa: BLE001 - a broken map does not stop the catalog
            continue
        for r in recs:
            kind = instance_kind(r.name, r.tpl, r.cls, r.flags)
            if kind in SKIP_KINDS or r.tpl in protos or (not r.tpl and r.cls in SKIP_CLASSES):
                continue
            v = variants[(kind, (r.tpl or r.cls).lower())].setdefault((r.tpl, r.cls), [r, set(), 0])
            v[1].add(cls)
            v[2] += 1
            if _score(r) < _score(v[0]):
                v[0] = r
    found = {}
    for key, vs in variants.items():
        (tpl, cls), (rec, on, n) = max(vs.items(), key=lambda kv: (len(kv[1][1]), kv[1][2]))
        e = found[key] = Entry()
        e.kind, e.tpl, e.cls, e.record, e.maps, e.count, e.tested = key[0], tpl, cls, rec, on, n, True
    for tpl, cls, checked in VEHICLES:          # vehicles may be on no map at all
        on_maps = [e for k, e in found.items() if k[1] == tpl.lower()]
        for e in on_maps:
            e.tested = checked
        if on_maps:
            continue
        e = found[('vehicle', tpl.lower())] = Entry()
        e.kind, e.tpl, e.cls, e.maps, e.count, e.tested = 'vehicle', tpl, cls, set(), 0, checked
        e.record = Instance(None, tpl, tpl, cls, IDENT, None, None, (), b'\0' + struct.pack('<16f', *IDENT), [])
    out = []
    for e in found.values():
        e.record = _clean(e.record)
        e.maps = sorted(e.maps)
        out.append(e)
    order = {'pickup': 0, 'vehicle': 1, 'object': 2, 'plant': 3}
    return sorted(out, key=lambda e: (order[e.category], e.label.lower(), e.cls.lower()))


def catalog(game, lock=None):
    """The catalog of this game, built once."""
    cat = getattr(game, '_catalog', None)
    if cat is None:
        cat = game._catalog = build_catalog(game, lock)
    return cat
