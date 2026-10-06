"""TimeShift map manager: import / export / remove multiplayer maps of the PC version."""
import json
import os
import sys

from .core import Game, MapToolError  # noqa: F401
from .i18n import _, detect_language, get_language, set_language, LANGUAGES  # noqa: F401

# the folder of config.json and library\: the tool's folder; for the one-file TSMapTool.exe (PyInstaller) the
# folder of the exe, the code itself runs from a temporary folder that is removed on exit
if getattr(sys, 'frozen', False):
    TOOL_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(TOOL_DIR, 'config.json')


def load_config():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**values):
    cfg = load_config()
    cfg.update(values)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def init_language(explicit=None):
    """--lang argument, then config.json, then the Windows UI language."""
    set_language(explicit or load_config().get('lang') or detect_language())
    return get_language()


def _is_game(d):
    return bool(d) and os.path.isfile(os.path.join(d, 'preload', 'paks', 'out.s3darc'))


def find_game_dir(explicit=None):
    """--game argument, then config.json, then the folder above the tool."""
    if explicit:
        return explicit
    d = load_config().get('game_dir')
    if _is_game(d):
        return d
    d = os.path.dirname(TOOL_DIR)
    return d if _is_game(d) else None


def save_game_dir(d):
    save_config(game_dir=os.path.abspath(d))


def open_game(game_dir=None):
    d = find_game_dir(game_dir)
    if not d:
        raise MapToolError(_('TimeShift folder not found. Specify it: --game "D:\\Games\\TimeShift"'))
    g = Game(d, TOOL_DIR)
    if game_dir:
        save_game_dir(d)
    return g
