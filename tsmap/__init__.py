"""TimeShift map manager: import / export / remove multiplayer maps of the PC version."""
import json
import os

from .core import Game, MapToolError  # noqa: F401

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(TOOL_DIR, 'config.json')


def _is_game(d):
    return bool(d) and os.path.isfile(os.path.join(d, 'preload', 'paks', 'out.s3darc'))


def find_game_dir(explicit=None):
    """--game argument, then config.json, then the folder above the tool."""
    if explicit:
        return explicit
    try:
        with open(CONFIG, encoding='utf-8') as f:
            d = json.load(f).get('game_dir')
        if _is_game(d):
            return d
    except (OSError, ValueError):
        pass
    d = os.path.dirname(TOOL_DIR)
    return d if _is_game(d) else None


def save_game_dir(d):
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump({'game_dir': os.path.abspath(d)}, f, ensure_ascii=False, indent=1)


def open_game(game_dir=None):
    d = find_game_dir(game_dir)
    if not d:
        raise MapToolError('Не знайдено папку TimeShift. Вкажіть її: --game "D:\\Games\\TimeShift"')
    g = Game(d, TOOL_DIR)
    if game_dir:
        save_game_dir(d)
    return g
