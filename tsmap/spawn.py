"""Bots and vehicles on multiplayer maps.

Multiplayer levels have no spawners, so spare objects of the level are converted:
  - a sound zone (&dom_snd) becomes an enemy spawner (&dom_ai) or a vehicle spawner (&dom_spawn): the flags
    string of its scene node (chunk 0x115) and its property text change; a 4-vertex zone can also be moved
    (vertices 0xf1, bounding box 0x11d);
  - a pickup (an item_* instance record in section 0x1b8) becomes a placed vehicle: the record
    'SNIA' name\\0 template\\0 class\\0 \\0 + 4x4 matrix gets a new template, class and position.
The game creates only objects whose resources are in the map's preload list, so the templates, textures,
sounds and sound banks of the new objects are added there (see resources()).

Checked in game (single-player load from the developer menu): soldiers with SHOOT behaviour appear at random
places of their zone when a player enters it; the bike placed on the map and spawned by a zone, respawning
after it is destroyed. Moving bots (MULXNS / WILLROCK) need navigation that multiplayer maps lack.
"""
import collections
import os
import re
import struct

from . import xbox
from .lg import LevelFile, _cstr

# (template, class, checked in game)
VEHICLES = [('bike', 'vhc_bike', True), ('vhc_bronik', '', False), ('vhc_bronik_covered', '', False),
            ('vhc_tank', '', False), ('vhc_truck', 'vhc_truck', False), ('vhc_lorry_van', '', False),
            ('vhc_helicopter', '', False)]
WEAPONS = ['arifle', 'shotgun', 'pmg', 'crb', 'uzi', 'sniper']
DEFAULT_SOLDIERS = ['soldier_2_9', 'soldier_2_10', 'soldier_2_6']
DONOR_FLAGS = ('&dom_snd',)           # zones that can be given away: sound / reverb zones
VEHICLE_PREFIX = 'veh_'               # converted pickups: veh_<template>__<original name>

BOTS_TEXT = '''IACTIVE {
	minLocks = 0
	maxLocks = 1
	nmbLocks = 0
}

AI_SPAWN {
	oppClass = [%(classes)s]
	oppAffixes = [%(weapons)s]
	nSpawn = 100000
	nSpawnFirst = %(first)d
	nMaxSimultSameDom = %(alive)d
	timeOppSpawn = %(interval)s
	timeAfterKill = %(after_kill)s
}

OPP_PS {
	AI {
		isAggressive = 1
		BEHAVIORS {
			fight {
				type = SHOOT
			}
		}
	}

	#ssl_begin
		override OnInit
			SetSenses("max")
			SetPar("radSeeStraight", %(sight)d)
			SetPar("radSeeSide", %(sight)d)
			SetPar("angleSeeSide", 180)
			SetPar("radHearShot", %(sight)d)
			SetPar("radHearRun", %(hear)d)
			SetPar("radHearWalk", %(hear)d)
			SetBHV("fight")
		end
	#ssl_end
}
'''

SPAWNER_TEXT = '''IACTIVE {
	minLocks = 0
	maxLocks = 1
	nmbLocks = 0
}

DOM_SPAWN {
	class = "%(cls)s"
	tpl = "%(tpl)s"
	nMaxSimultSameClassTpl = 1
	nSpawnMax = 100000
}

SPAWN_PS {
	#ssl_begin
		override OnDestroyed
			$%(name)s.Lock()
		end
	#ssl_end
}

DOMAIN {
	height = 4
}

#ssl
override OnInitLevel
	Spawn(1)	:2
end
override OnLocked
	Unlock()	:%(respawn)s
end
override OnUnlocked
	Spawn(1)
end
'''


class Zone:
    __slots__ = ('name', 'flags', 'node', 'flags_chunk', 'text_chunk', 'text', 'verts', 'vert_chunk', 'box_chunk')

    @property
    def center(self):
        n = len(self.verts) or 1
        return tuple(sum(v[i] for v in self.verts) / n for i in range(3))

    @property
    def size(self):
        if not self.verts:
            return 0.0, 0.0
        return (max(v[0] for v in self.verts) - min(v[0] for v in self.verts),
                max(v[2] for v in self.verts) - min(v[2] for v in self.verts))

    @property
    def movable(self):
        return len(self.verts) == 4 and self.vert_chunk is not None and self.box_chunk is not None

    @property
    def kind(self):
        return {'&dom_ai': 'bots', '&dom_spawn': 'spawner'}.get(self.flags, 'zone')


class Instance:
    __slots__ = ('name', 'tpl', 'cls', 'pos', 'chunk')

    @property
    def kind(self):
        return 'vehicle' if self.name.startswith(VEHICLE_PREFIX) else 'pickup'

    @property
    def original_name(self):
        return self.name.split('__', 1)[1] if self.kind == 'vehicle' and '__' in self.name else self.name


def parse_record(payload):
    """'SNIA' name tpl cls '' + 16 floats -> (name, tpl, cls, matrix) or None."""
    if not payload.startswith(b'SNIA'):
        return None
    parts = payload[4:].split(b'\0', 3)
    if len(parts) < 4 or len(parts[3]) != 1 + 64 or parts[3][0] != 0:
        return None
    m = struct.unpack('<16f', parts[3][1:])
    return parts[0].decode('latin1'), parts[1].decode('latin1'), parts[2].decode('latin1'), m


def make_record(name, tpl, cls, pos):
    m = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, pos[0], pos[1], pos[2], 1]
    return b'SNIA' + b'\0'.join(s.encode('latin1') for s in (name, tpl, cls)) + b'\0\0' + struct.pack('<16f', *m)


class MapLevel:
    """A level with its original (unedited) copy, for finding and reverting conversions."""

    def __init__(self, data, original):
        self.lf = LevelFile(data)
        self.orig = self.lf if original == data else LevelFile(original)

    # ---- objects
    @staticmethod
    def _zones(lf):
        d = lf.data
        objs = {o.chunk: o for o in lf.objects}
        out = []
        for c in lf.chunks:
            if c.tag != 0xf0 or not c.children or c.children[0].tag != 0xf4:
                continue
            kids = {k.tag: k for k in c.children}
            fc = kids.get(0x115)
            if fc is None:
                continue
            flags = _cstr(d[fc.start:fc.end])
            if not flags.startswith('&dom_'):
                continue
            z = Zone()
            z.name, z.flags, z.node, z.flags_chunk = _cstr(d[c.children[0].start:c.children[0].end]), flags, c, fc
            tc = kids.get(0xfd)
            z.text_chunk = tc.children[0] if tc is not None and tc.children and tc.children[0].tag == 0x1ba else None
            z.text = objs[z.text_chunk].text if z.text_chunk in objs else ''
            z.vert_chunk, z.box_chunk, z.verts = kids.get(0xf1), kids.get(0x11d), []
            if z.vert_chunk is not None:
                n = struct.unpack_from('<I', d, z.vert_chunk.start)[0]
                if 4 + 12 * n == z.vert_chunk.end - z.vert_chunk.start:
                    z.verts = [struct.unpack_from('<3f', d, z.vert_chunk.start + 4 + 12 * i) for i in range(n)]
            out.append(z)
        return out

    @staticmethod
    def _instances(lf):
        out = []
        for c in lf.chunks:
            if c.tag != 0x1b9 or c.children:
                continue
            r = parse_record(lf.data[c.start:c.end])
            if r is None:
                continue
            i = Instance()
            i.name, i.tpl, i.cls, m = r
            i.pos, i.chunk = m[12:15], c
            out.append(i)
        return out

    def zones(self):
        """Zones that can be converted (sound zones with a text) and the ones converted already."""
        names = collections.Counter(z.name for z in self._zones(self.lf))
        return [z for z in self._zones(self.lf)
                if names[z.name] == 1 and z.text_chunk is not None and (z.flags in DONOR_FLAGS or z.kind != 'zone')]

    def pickups(self):
        """Pickups (item_*) that can become vehicles, and the vehicles made already."""
        return [i for i in self._instances(self.lf) if i.name.startswith('item_') or i.kind == 'vehicle']

    def start_positions(self):
        return sorted(((i.name, i.pos) for i in self._instances(self.lf) if i.name.startswith('start_pos')),
                      key=lambda x: (len(x[0]), x[0]))

    def additions(self):
        return [z for z in self.zones() if z.kind != 'zone'] + [i for i in self.pickups() if i.kind == 'vehicle']

    # ---- conversions; each returns the new level data
    def _zone(self, name):
        for z in self.zones():
            if z.name == name:
                return z
        raise ValueError('zone %s not found' % name)

    def _zone_payloads(self, z, flags, place):
        payloads = {z.flags_chunk: flags.encode('latin1') + b'\0'}
        if place:
            if not z.movable:
                raise ValueError('zone %s cannot be moved' % z.name)
            x, y, zz, size = place
            half = size / 2.0
            corners = [(x - half, y, zz - half), (x + half, y, zz - half), (x - half, y, zz + half), (x + half, y, zz + half)]
            # keep the vertex order of the faces: the corner of the old quad goes to the same corner of the new one
            order = sorted(range(4), key=lambda i: (z.verts[i][0], z.verts[i][2]))
            new = [None] * 4
            for i, v in zip(order, sorted(corners, key=lambda v: (v[0], v[2]))):
                new[i] = v
            payloads[z.vert_chunk] = struct.pack('<I', 4) + b''.join(struct.pack('<3f', *v) for v in new)
            payloads[z.box_chunk] = struct.pack('<I6f', 1, x - half, y - 0.05, zz - half, x + half, y + 0.05, zz + half)
        return payloads

    def _apply(self, texts, payloads):
        out = self.lf.replace_texts(texts, payloads)
        if len(LevelFile(out).objects) != len(self.lf.objects):
            raise ValueError('level check failed')
        return out

    def _text_index(self, z):
        return [o.index for o in self.lf.objects if o.chunk is z.text_chunk][0]

    def make_bots(self, zone, classes, weapons, alive=3, after_kill=5, interval=3, sight=150, place=None):
        z = self._zone(zone)
        text = BOTS_TEXT % dict(classes=','.join('"%s"' % c for c in classes),
                                weapons=','.join('"&w_%s"' % w for w in weapons),
                                first=min(2, alive), alive=alive, after_kill=after_kill, interval=interval,
                                sight=sight, hear=max(10, sight // 2))
        return self._apply({self._text_index(z): text}, self._zone_payloads(z, '&dom_ai', place))

    def make_spawner(self, zone, tpl, cls, respawn=10, place=None):
        z = self._zone(zone)
        text = SPAWNER_TEXT % dict(cls=cls or tpl, tpl=tpl, name=z.name, respawn=respawn)
        return self._apply({self._text_index(z): text}, self._zone_payloads(z, '&dom_spawn', place))

    def make_vehicle(self, pickup, tpl, cls, pos=None):
        p = [i for i in self.pickups() if i.name == pickup]
        if not p:
            raise ValueError('pickup %s not found' % pickup)
        p = p[0]
        pos = pos or (p.pos[0], p.pos[1] + 0.5, p.pos[2])
        name = '%s%s__%s' % (VEHICLE_PREFIX, tpl, p.original_name)
        return self._apply({}, {p.chunk: make_record(name, tpl, cls, pos)})

    def revert(self, name):
        """Undo a conversion: the zone or the record gets its original data back."""
        for z in self.zones():
            if z.name == name and z.kind != 'zone':
                oz = [o for o in self._zones(self.orig) if o.name == name][0]
                od = self.orig.data
                payloads = {z.flags_chunk: od[oz.flags_chunk.start:oz.flags_chunk.end]}
                for cur, old in ((z.vert_chunk, oz.vert_chunk), (z.box_chunk, oz.box_chunk)):
                    if cur is not None and old is not None:
                        payloads[cur] = od[old.start:old.end]
                return self._apply({self._text_index(z): oz.text}, payloads)
        for i in self.pickups():
            if i.name == name and i.kind == 'vehicle':
                old = [o for o in self._instances(self.orig) if o.name == i.original_name][0]
                return self._apply({}, {i.chunk: self.orig.data[old.chunk.start:old.chunk.end]})
        raise ValueError('%s is not a converted object' % name)


# ------------------------------------------------------------------ resources
def _class_blocks(ps):
    """{class name: [block text]} from all ps files."""
    out = collections.defaultdict(list)
    for i in range(len(ps.streams)):
        t = ps.text(i)
        depth, start, name = 0, None, None
        for m in re.finditer(r'(?m)^([A-Za-z_][\w|&.]*)\s*\{|\{|\}', t):
            if m.group(1) is not None and depth == 0:
                name, start, depth = m.group(1).lower(), m.start(), 1
            elif m.group(0).endswith('{'):
                depth += 1
            elif depth:
                depth -= 1
                if depth == 0 and name:
                    out[name].append(t[start:m.end()])
                    name = None
    return out


class ResourceIndex:
    """What a template needs: its templates, textures, sounds and sound banks (built once per game)."""

    def __init__(self, game, ps):
        self.game = game
        self.arc = game.orig['main']
        self.names = collections.defaultdict(set)
        for e in self.arc.entries:
            self.names[e.name.lower()].add(e.type)
        self.blocks = _class_blocks(ps)
        self.uses_tpl = collections.defaultdict(set)      # template -> classes with nameTpl = template
        for c, bl in self.blocks.items():
            for b in bl:
                for t in re.findall(r'\bnameTpl\s*=\s*"?(\w+)', b):
                    self.uses_tpl[t.lower()].add(c)
        self.waves = {e.name.lower(): self.game.read_orig('main', e) for e in self.arc.entries if e.type == 10}
        self._sounds = {}

    def _is_sound(self, name):
        """A sound of a bank: the name itself is in a sound bank (steps and deaths named in a model)."""
        if name not in self._sounds:
            key = name.encode('latin1')
            self._sounds[name] = any(key in d for d in self.waves.values())
        return self._sounds[name]

    def _bank(self, sound):
        toks = re.sub(r'(_rnd_set|_set|_loop)$', '', sound.lower()).split('_')
        for n in range(len(toks), 1, -1):
            stem = '_'.join(toks[:n]).encode()
            hit = [b for b, d in self.waves.items() if stem in d]
            if hit:
                return hit if len(hit) <= 2 else []
        return []

    def resources(self, tpl, cls=''):
        """{preload section: [names]} for a template (and its class)."""
        out = collections.defaultdict(set)
        sounds, seen_t, seen_c = set(), set(), {}
        # classes: (name, mode). DEEP: the object's own class and every class it names (dispatchers, projectiles,
        # effects, its crosshair - tur_charging crashes the game without crs_tur_charging and sfx_tur_alt_expl):
        # all templates and textures they name. TIED: a class tied to a template found on the way (nameTpl): its
        # templates only. BASE: base classes (parentDesc): their sounds only. Following more than that pulls in
        # half of the game (the tank: soldiers and police through the classes that use its rockets)
        DEEP, TIED, BASE = 2, 1, 0
        todo_t, todo_c = [tpl.lower()], [(c.lower(), DEEP) for c in (cls, tpl) if c]
        if cls and cls.lower() not in self.blocks and 12 in self.names.get(cls.lower(), ()):
            todo_t.append(cls.lower())         # item_mp_sniper: no ps block, a template of the same name
        sections = {12: 'Templates', 6: 'Textures', 7: 'Cubemaps', 15: 'Ragdolls'}
        while todo_t or todo_c:
            if todo_t:
                t = todo_t.pop()
                if t in seen_t or 12 not in self.names.get(t, ()):
                    continue
                seen_t.add(t)
                out['Templates'].add(t)
                todo_c += [(c, TIED) for c in self.uses_tpl.get(t, ())]
                data = self.game.read_orig('main', self.arc.get(t, 12))
                for s in set(re.findall(rb'[A-Za-z_][A-Za-z0-9_]{2,}', data)):
                    w = s.decode('latin1').lower()
                    for ty in self.names.get(w, ()):
                        if ty == 12:
                            todo_t.append(w)
                        elif ty in sections:
                            out[sections[ty]].add(w)
                    # sounds named by the model itself: the steps, landing and death of the mech
                    if w not in self.names and SOUND_NAME.match(w) and self._is_sound(w):
                        sounds.add(w)
            else:
                c, mode = todo_c.pop()
                if seen_c.get(c, -1) >= mode:
                    continue
                seen_c[c] = mode
                for b in self.blocks.get(c, []):
                    todo_c += [(p.lower(), BASE) for p in re.findall(r'\bparentDesc\s*=\s*"?(\w+)', b)]
                    if mode >= TIED:
                        todo_t += [t.lower() for t in re.findall(r'\b(?:nameTpl|tpl|nameClass)\s*=\s*"?(\w+)', b)]
                    if mode == DEEP:
                        body = re.sub(r'\bparentDesc\s*=\s*"?\w+', '', b)
                        for w in re.findall(r'=\s*"?([A-Za-z_]\w*)', body):
                            w = w.lower()
                            if w in self.blocks and w != c:
                                todo_c.append((w, DEEP))
                            for ty in self.names.get(w, ()):
                                if ty == 12:
                                    todo_t.append(w)
                                elif ty in sections:
                                    out[sections[ty]].add(w)
                    for sl in _blocks_named(b, 'sounds_list'):
                        # name = sound, or name { file = sound  nameObj = node the sound comes from }
                        sounds.update(v for k, v in re.findall(r'(\w+)\s*=\s*"?([A-Za-z_]\w+)', sl)
                                      if v not in ('Yes', 'No') and k.lower() != 'nameobj')
        out['Sounds'] = sounds
        out['WaveBanks_mem'] = {b for s in sounds for b in self._bank(s)}
        # a memory bank X_mem comes with the streamed bank X (sounds\pc\X.fsb), as the game's lists have them
        out['WaveBanks_strm_file'] = {b[:-4] for b in out['WaveBanks_mem'] if b.endswith('_mem') and os.path.isfile(
            os.path.join(self.game.dir, 'sounds', 'pc', b[:-4] + '.fsb'))}
        return {k: sorted(v) for k, v in out.items() if v}


SOUND_NAME = re.compile(r'(obj|wpn|amb|mus|vo|ex|whc|mpt|sfx)_\w+$')


def _blocks_named(text, name):
    """Bodies of the `name { ... }` blocks of a class text, nested blocks included."""
    out = []
    for m in re.finditer(r'\b%s\s*\{' % re.escape(name), text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
        out.append(text[m.end():i - 1])
    return out


def resource_index(game):
    idx = getattr(game, '_resource_index', None)
    if idx is None:
        from .core import PsContainer
        idx = game._resource_index = ResourceIndex(game, PsContainer(game.orig_ps))
    return idx


def add_to_list(list_data, resources):
    """Preload list data + {section: [names]} -> (new data, number of names added). Unknown sections are skipped."""
    n = struct.unpack_from('<I', list_data)[0]
    secs = xbox.parse_list_text(list_data[4:4 + n].decode('latin1'))
    added = 0
    new = []
    for s, items in secs:
        have = {i.lower() for i in items}
        extra = [x for x in resources.get(s, []) if x.lower() not in have]
        added += len(extra)
        new.append((s, items + extra))
    return xbox.format_list(new), added


def save_edit(game, cls, data, templates=(), log=lambda m: None):
    """Store the edited level and add the resources of [(template, class)] to the map's preload lists."""
    from .i18n import _
    LevelFile(data)                       # must parse again before it is stored
    game.save_level_override(cls, data, log)
    add_resources(game, cls, templates, log)


def add_resources(game, cls, templates, log=lambda m: None):
    """Add what [(template, class)] need to the map's preload lists (all archives)."""
    from .i18n import _
    res = needed_resources(game, cls, templates) if templates else {}
    if not res:
        return
    lists, added = {}, 0
    for arc in ('main', 'nv', 'ati'):
        d = game.map_list_data(cls, arc)
        if d is not None:
            lists[arc], n = add_to_list(d, res)
            added = max(added, n)
    game.save_map_lists(cls, lists)
    log(_('%s: %d resources added to the preload list', cls, added))


def needed_resources(game, cls, templates):
    """{section: [names]} for [(template, class)], skipping what the map's main list already has. A template of
    the map's own list (as shipped) is skipped as a whole: the map loads everything it needs. One added by an
    earlier save is walked again, so a list saved incomplete gets the rest."""
    have = _list_names(game.map_list_data(cls, 'main'))
    shipped = _list_names(game.map_list_data(cls, 'main', original=True))
    todo = [(tpl, tcls) for tpl, tcls in templates                    # already loaded by the map (soldiers are
            if ('Templates', (tpl or tcls).lower()) not in shipped]   # player models, every map has all weapons)
    if not todo:
        return {}
    idx = resource_index(game)
    out = collections.defaultdict(set)
    for tpl, tcls in todo:
        for s, items in idx.resources(tpl, tcls).items():
            out[s].update(n for n in items if (s, n) not in have)
    return {k: sorted(v) for k, v in out.items() if v}


def _list_names(data):
    """{(section, lower-case name)} of a preload list (empty for None)."""
    if data is None:
        return set()
    n = struct.unpack_from('<I', data)[0]
    return {(s, i.lower()) for s, items in xbox.parse_list_text(data[4:4 + n].decode('latin1')) for i in items}


def placed_templates(game, cls, data):
    """[(template, class)] of the instances of level data whose template is not in the map's own preload list
    (as shipped): objects added by this tool, now or by an earlier save, which may have missed some of what they
    need (tur_charging before its crosshair and projectiles were followed). A save walks them again."""
    from .mapview import instance_records
    shipped = _list_names(game.map_list_data(cls, 'main', original=True))
    own = {r.name for r in instance_records(LevelFile(game.level_data(cls, original=True)))}   # as the map has them
    arc = game.orig['main']
    out = []
    for r in instance_records(LevelFile(data)):
        key, model = (r.tpl, r.cls), r.tpl or r.cls        # a class-only one (the mech) takes its class's model
        if (model and r.name not in own and ('Templates', model.lower()) not in shipped and key not in out
                and arc.get(model.lower(), 12) is not None):
            out.append(key)
    return out
