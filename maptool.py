"""TimeShift map manager, command line.

  python maptool.py list                          карти в грі
  python maptool.py info <карта>                  подробиці про карту
  python maptool.py import <файл/папка> [...]     Xbox 360 DLC-пакет, розпакована папка або .tsmap
  python maptool.py export <карта> <файл.tsmap>   зберегти карту в окремий файл
  python maptool.py remove <карта> [...]          видалити додану / приховати оригінальну
  python maptool.py unhide <карта> [...]          повернути приховану оригінальну карту
  python maptool.py apply                         перезібрати архіви гри з бібліотеки
  python maptool.py restore                       повернути оригінальні архіви гри
  python maptool.py verify                        перевірити архіви гри
import / remove / unhide одразу застосовують зміни, якщо не вказано --no-apply.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tsmap import MapToolError, open_game  # noqa: E402

KIND = {'original': 'оригінал', 'custom': 'додана', 'hidden': 'прихована', 'broken': 'ПОШКОДЖЕНА'}


def log(msg):
    print(msg, flush=True)


def cmd_list(g, a):
    rows = g.maps()
    print('%-4s %-22s %-34s %-24s %-7s %s' % ('id', 'клас', 'назва', 'режими', 'гравці', 'стан'))
    for r in rows:
        print('%-4d %-22s %-34s %-24s %-7s %s' % (r['id'], r['class'], r['title'][:34], ' '.join(r['modes']),
                                                   r['players'], KIND[r['kind']]))
    if g.pending():
        print('\nЄ незастосовані зміни: виконайте "maptool.py apply"')


def cmd_info(g, a):
    rows = [r for r in g.maps() if r['class'].lower() == a.map.lower()]
    if not rows:
        raise MapToolError('невідома карта %s' % a.map)
    r = rows[0]
    desc, png = g.map_details(r['class'])
    print('%s (id %d, %s)' % (r['class'], r['id'], KIND[r['kind']]))
    print('Назва:   %s' % r['title'])
    if r.get('source'):
        print('Джерело: %s' % r['source'])
    print('Режими:  %s' % ' '.join(r['modes']))
    print('Гравці:  %s' % r['players'])
    print('Опис:    %s' % desc)
    if a.preview and png:
        with open(a.preview, 'wb') as f:
            f.write(png)
        print('Превʼю збережено: %s' % a.preview)


def cmd_import(g, a):
    added = []
    for p in a.paths:
        added += g.import_path(p, only=a.maps.split(',') if a.maps else None, log=log)
    if added and not a.no_apply:
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
    p = argparse.ArgumentParser(description='Менеджер мультиплеєрних карт TimeShift (PC)')
    p.add_argument('--game', help='папка гри (запам\'ятовується)')
    sp = p.add_subparsers(dest='cmd', required=True)
    sp.add_parser('list', help='список карт')
    s = sp.add_parser('info', help='подробиці про карту')
    s.add_argument('map')
    s.add_argument('--preview', help='зберегти превʼю завантажувального екрана в PNG')
    s = sp.add_parser('import', help='імпорт карт')
    s.add_argument('paths', nargs='+')
    s.add_argument('--maps', help='лише ці карти (через кому)')
    s.add_argument('--no-apply', action='store_true')
    s = sp.add_parser('export', help='експорт карти в .tsmap')
    s.add_argument('map')
    s.add_argument('out')
    s = sp.add_parser('remove', help='видалити додану або приховати оригінальну карту')
    s.add_argument('maps', nargs='+')
    s.add_argument('--no-apply', action='store_true')
    s = sp.add_parser('unhide', help='повернути приховану оригінальну карту')
    s.add_argument('maps', nargs='+')
    s.add_argument('--no-apply', action='store_true')
    sp.add_parser('apply', help='перезібрати архіви гри')
    sp.add_parser('restore', help='повернути оригінальні архіви')
    sp.add_parser('verify', help='перевірити архіви гри')
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
        print('Помилка: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
