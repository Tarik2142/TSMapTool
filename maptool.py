"""TimeShift map manager, command line.

  python maptool.py list                          maps in the game
  python maptool.py info <level>                  map details
  python maptool.py import <file/folder> [...]    Xbox 360 DLC package, extracted folder or .tsmap
  python maptool.py export <level> <file.tsmap>   save a map to a separate file
  python maptool.py remove <level> [...]          remove an added map / hide an original one
  python maptool.py unhide <level> [...]          show a hidden original map again
  python maptool.py apply                         rebuild the game archives from the library
  python maptool.py restore                       put the original game archives back
  python maptool.py verify                        check the game archives
import / remove / unhide apply the changes right away unless --no-apply is given.
--lang en|uk selects the interface language (default: config.json, then the Windows language).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tsmap import LANGUAGES, MapToolError, _, init_language, open_game, save_config  # noqa: E402

KIND = {'original': 'original', 'custom': 'added', 'hidden': 'hidden', 'broken': 'broken'}


def log(msg):
    print(msg, flush=True)


def cmd_list(g, a):
    rows = g.maps()
    print('%-4s %-22s %-34s %-24s %-7s %s' % ('id', _('level'), _('title'), _('modes'), _('players'), _('state')))
    for r in rows:
        print('%-4d %-22s %-34s %-24s %-7s %s' % (r['id'], r['class'], r['title'][:34], ' '.join(r['modes']),
                                                   r['players'], _(KIND[r['kind']])))
    if g.pending():
        print('\n' + _('There are unapplied changes: run "maptool.py apply"'))


def cmd_info(g, a):
    rows = [r for r in g.maps() if r['class'].lower() == a.map.lower()]
    if not rows:
        raise MapToolError(_('unknown map %s', a.map))
    r = rows[0]
    desc, png = g.map_details(r['class'])
    print('%s (id %d, %s)' % (r['class'], r['id'], _(KIND[r['kind']])))
    print(_('Title:   %s', r['title']))
    if r.get('source'):
        print(_('Source:  %s', r['source']))
    print(_('Modes:   %s', ' '.join(r['modes'])))
    print(_('Players: %s', r['players']))
    print(_('Description: %s', desc))
    if a.preview and png:
        with open(a.preview, 'wb') as f:
            f.write(png)
        print(_('Preview saved: %s', a.preview))


def cmd_import(g, a):
    added = []
    for p in a.paths:
        added += g.import_path(p, only=a.maps.split(',') if a.maps else None, log=log)
    if not added:
        print(_('No new maps were added'))
    elif not a.no_apply:
        g.apply(log)


def cmd_export(g, a):
    g.export_map(a.map, a.out, log)


def cmd_remove(g, a):
    for m in a.maps:
        g.remove_map(m, log)
    if not a.no_apply:
        g.apply(log)


def cmd_unhide(g, a):
    for m in a.maps:
        g.unhide_map(m, log)
    if not a.no_apply:
        g.apply(log)


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--lang', choices=sorted(LANGUAGES))
    lang = pre.parse_known_args()[0].lang
    init_language(lang)
    if lang:
        save_config(lang=lang)

    p = argparse.ArgumentParser(description=_('TimeShift (PC) multiplayer map manager'))
    p.add_argument('--game', help=_('game folder (remembered)'))
    p.add_argument('--lang', choices=sorted(LANGUAGES), help=_('interface language'))
    sp = p.add_subparsers(dest='cmd', required=True)
    sp.add_parser('list', help=_('list maps'))
    s = sp.add_parser('info', help=_('map details'))
    s.add_argument('map')
    s.add_argument('--preview', help=_('save the loading screen preview as PNG'))
    s = sp.add_parser('import', help=_('import maps (Xbox 360 DLC package, extracted folder or .tsmap)'))
    s.add_argument('paths', nargs='+')
    s.add_argument('--maps', help=_('only these maps (comma separated)'))
    s.add_argument('--no-apply', action='store_true', help=_('do not rebuild the game archives'))
    s = sp.add_parser('export', help=_('export a map to .tsmap'))
    s.add_argument('map')
    s.add_argument('out')
    s = sp.add_parser('remove', help=_('remove an added map / hide an original one'))
    s.add_argument('maps', nargs='+')
    s.add_argument('--no-apply', action='store_true', help=_('do not rebuild the game archives'))
    s = sp.add_parser('unhide', help=_('show a hidden original map again'))
    s.add_argument('maps', nargs='+')
    s.add_argument('--no-apply', action='store_true', help=_('do not rebuild the game archives'))
    sp.add_parser('apply', help=_('rebuild the game archives from the library'))
    sp.add_parser('restore', help=_('put the original game archives back'))
    sp.add_parser('verify', help=_('check the game archives'))
    a = p.parse_args()
    try:
        g = open_game(a.game)
        try:
            {'list': cmd_list, 'info': cmd_info, 'import': cmd_import, 'export': cmd_export,
             'remove': cmd_remove, 'unhide': cmd_unhide,
             'apply': lambda g, a: g.apply(log), 'restore': lambda g, a: g.restore(log),
             'verify': lambda g, a: g.verify(log)}[a.cmd](g, a)
        finally:
            g.close()
    except MapToolError as e:
        print(_('Error: %s', e), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
