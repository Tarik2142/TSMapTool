"""Tiny gettext-like localization: messages are written in English, translations are keyed by them.

    from .i18n import _
    log(_('Added %s (id %d)', cls, mid))
"""
import locale

LANGUAGES = {'en': 'English', 'uk': 'Українська'}
_lang = 'en'

UK = {
    # core
    'unexpected ps container layout': 'неочікувана структура контейнера ps',
    'class %r found %d times in ps': 'клас %r знайдено в ps %d раз(и)',
    'only the last class of a ps file can be modified': 'змінювати можна лише останній клас у файлі ps',
    'gs_map_list: maps section not found': 'gs_map_list: не знайдено секцію maps',
    'gs_map_list: section %s not found': 'gs_map_list: не знайдено секцію %s',
    '%s: unsupported .tsmap format': '%s: непідтримуваний формат .tsmap',
    'TimeShift not found in %s (no preload\\paks\\out.s3darc)': 'TimeShift не знайдено в %s (немає preload\\paks\\out.s3darc)',
    'There is no backup of the original archives and the current ones are already modified. '
    'Put the original patch*.s3darc files into %s':
        'Немає бекапу оригінальних архівів, а поточні вже змінені. Покладіть оригінальні patch*.s3darc у %s',
    'Backing up the original archives to %s': 'Бекап оригінальних архівів у %s',
    'no free map ids left': 'закінчилися вільні id карт',
    'map %s already exists': 'карта %s вже є',
    'map %s clashes with an original level name': 'карта %s збігається з назвою оригінального рівня',
    'Warning: map id %d is above the singleplayer range (untested)':
        'Увага: id карти %d більший за діапазон одиночних рівнів (не перевірено)',
    'Added %s (id %d)': 'Додано %s (id %d)',
    'unknown map %s': 'невідома карта %s',
    'no maps (.s3dpak) found in %s': 'у %s не знайдено карт (.s3dpak)',
    'Skipped %s: already installed': 'Пропущено %s: вже встановлена',
    'Converting %s ...': 'Конвертація %s ...',
    '  %s: dropped %d references to Xbox-only resources (%s ...)': '  %s: прибрано %d посилань на Xbox-ресурси (%s ...)',
    'Exported %s -> %s': 'Експортовано %s -> %s',
    '%s has no preload list': '%s не має списку передзавантаження',
    'Exported %s -> %s (%d files)': 'Експортовано %s -> %s (%d файлів)',
    'Removed %s': 'Видалено %s',
    'Hidden original map %s': 'Приховано оригінальну карту %s',
    '%s is not hidden': '%s не прихована',
    'Restored original map %s': 'Повернуто оригінальну карту %s',
    'Close TimeShift first': 'Спершу закрийте TimeShift',
    'Building the map list ...': 'Збирання списку карт ...',
    'map list check failed for %s': 'перевірка списку карт не пройшла для %s',
    'Adding strings ...': 'Додавання рядків локалізації ...',
    'Packing %s ...': 'Пакування %s ...',
    'Writing %s ...': 'Запис %s ...',
    'Verifying ...': 'Перевірка ...',
    'Done: %d maps added, %d hidden': 'Готово: додано карт %d, приховано %d',
    'Original archives restored': 'Оригінальні архіви відновлено',
    '%s: OK (%d entries checked)': '%s: OK (перевірено записів: %d)',
    'ps file %d is inconsistent': 'файл ps %d пошкоджено',
    'Map list: %d multiplayer maps installed': 'Список карт: мультиплеєрних карт у грі: %d',
    'TimeShift folder not found. Specify it: --game "D:\\Games\\TimeShift"':
        'Не знайдено папку TimeShift. Вкажіть її: --game "D:\\Games\\TimeShift"',
    # map states
    'original': 'оригінал',
    'added': 'додана',
    'hidden': 'прихована',
    'broken': 'пошкоджена',
    # command line
    'TimeShift (PC) multiplayer map manager': 'Менеджер мультиплеєрних карт TimeShift (PC)',
    'game folder (remembered)': 'папка гри (запам\'ятовується)',
    'interface language': 'мова інтерфейсу',
    'list maps': 'список карт',
    'map details': 'подробиці про карту',
    'save the loading screen preview as PNG': 'зберегти превʼю завантажувального екрана в PNG',
    'import maps (Xbox 360 DLC package, extracted folder or .tsmap)':
        'імпорт карт (Xbox 360 DLC-пакет, розпакована папка або .tsmap)',
    'only these maps (comma separated)': 'лише ці карти (через кому)',
    'do not rebuild the game archives': 'не перезбирати архіви гри',
    'export a map to .tsmap': 'експорт карти в .tsmap',
    'remove an added map / hide an original one': 'видалити додану або приховати оригінальну карту',
    'show a hidden original map again': 'повернути приховану оригінальну карту',
    'rebuild the game archives from the library': 'перезібрати архіви гри з бібліотеки',
    'put the original game archives back': 'повернути оригінальні архіви гри',
    'check the game archives': 'перевірити архіви гри',
    'level': 'рівень',
    'title': 'назва',
    'modes': 'режими',
    'players': 'гравці',
    'state': 'стан',
    'There are unapplied changes: run "maptool.py apply"': 'Є незастосовані зміни: виконайте "maptool.py apply"',
    'Title:   %s': 'Назва:   %s',
    'Source:  %s': 'Джерело: %s',
    'Modes:   %s': 'Режими:  %s',
    'Players: %s': 'Гравці:  %s',
    'Description: %s': 'Опис:    %s',
    'Preview saved: %s': 'Превʼю збережено: %s',
    'No new maps were added': 'Нових карт не додано',
    'Error: %s': 'Помилка: %s',
    # GUI
    'TimeShift map manager': 'TimeShift — менеджер карт',
    'Game folder:': 'Папка гри:',
    'Change…': 'Змінити…',
    'Language:': 'Мова:',
    'Import file…': 'Імпорт файлу…',
    'Import folder…': 'Імпорт папки…',
    'Export…': 'Експорт…',
    'Remove / hide': 'Видалити / приховати',
    'Unhide': 'Повернути приховану',
    'Apply': 'Застосувати',
    'Restore original game': 'Відновити оригінал гри',
    'Title': 'Назва',
    'Level': 'Рівень',
    'Modes': 'Режими',
    'Players': 'Гравці',
    'State': 'Стан',
    'Error': 'Помилка',
    'There are unapplied changes — press «Apply»': 'Є незастосовані зміни — натисніть «Застосувати»',
    'Select the TimeShift folder': 'Вкажіть папку TimeShift',
    'Level: %s   ID: %s   State: %s\nModes: %s   Players: %s': 'Рівень: %s   ID: %s   Стан: %s\nРежими: %s   Гравці: %s',
    'Source: %s': 'Джерело: %s',
    'Cannot read: %s': 'Не вдалося прочитати: %s',
    '(no preview)': '(немає превʼю)',
    'Import maps: Xbox 360 DLC package or .tsmap': 'Імпорт карт: Xbox 360 DLC-пакет або .tsmap',
    'All files (Xbox DLC packages have no extension)': 'Усі файли (Xbox DLC-пакети без розширення)',
    'TimeShift maps': 'Карти TimeShift',
    'Extracted Xbox DLC folder (with .s3dpak / .s3dlst)': 'Розпакована папка Xbox DLC (з .s3dpak / .s3dlst)',
    'Import': 'Імпорт',
    'Export': 'Експорт',
    'Select a map in the list': 'Виберіть карту в списку',
    'Export map': 'Експорт карти',
    'Export folder': 'Папка для експорту',
    'Removal': 'Видалення',
    'Remove added maps:': 'Видалити додані карти:',
    'Hide original maps from the menu (they can be restored):': 'Приховати з меню оригінальні карти (їх можна повернути):',
    'Confirmation': 'Підтвердження',
    'Unhide maps': 'Повернення карт',
    'Select a hidden original map': 'Виберіть приховану оригінальну карту',
    'Applying': 'Застосування',
    'Restore': 'Відновлення',
    'Put the original game archives back?\n\nThe map library is kept — press «Apply» to bring the maps back.':
        'Повернути оригінальні архіви гри?\n\nБібліотека карт залишиться — карти можна повернути кнопкою «Застосувати».',
    'Restoring the original game': 'Відновлення оригіналу',
    # scripts (level object properties)
    'Maps': 'Карти',
    'Scripts': 'Скрипти',
    'Map:': 'Карта:',
    'Search:': 'Пошук:',
    'Only objects with scripts': 'Лише об\'єкти зі скриптами',
    'Object': 'Об\'єкт',
    'Template': 'Шаблон',
    'Section': 'Секція',
    'scene': 'сцена',
    'templates': 'шаблони',
    'effects': 'ефекти',
    'other': 'інше',
    'Built-in functions': 'Вбудовані функції',
    'Double click inserts the call': 'Подвійний клік вставляє виклик',
    'Save changes': 'Зберегти зміни',
    'Save and apply to the game': 'Зберегти й застосувати в гру',
    'Undo object changes': 'Скасувати зміни об\'єкта',
    'Restore original level': 'Повернути оригінал рівня',
    'Loading %s ...': 'Завантаження %s ...',
    '%s: %d objects with properties, %d with scripts': '%s: об\'єктів із властивостями %d, зі скриптами %d',
    'Changed objects: %d (not saved)': 'Змінено об\'єктів: %d (не збережено)',
    'This level has saved edits': 'Рівень має збережені зміни',
    'Original level': 'Оригінальний рівень',
    'Discard unsaved changes in %s?': 'Відкинути незбережені зміни в %s?',
    'Drop all edits of %s and use the original level?': 'Видалити всі зміни %s і повернути оригінальний рівень?',
    'Check': 'Перевірка',
    'Possible problems:': 'Можливі проблеми:',
    'Save anyway?': 'Все одно зберегти?',
    'extra "end"': 'зайвий "end"',
    'a block is not closed with "end"': 'блок не закрито "end"',
    'unbalanced quotes': 'непарні лапки',
    ' (line %d)': ' (рядок %d)',
    'The text contains characters the game cannot store (use Latin letters only).':
        'Текст містить символи, які гра не може зберегти (використовуйте лише латиницю).',
    'Saving scripts': 'Збереження скриптів',
    'Restoring the level': 'Відновлення рівня',
    '%s: %d objects changed': '%s: змінено об\'єктів: %d',
    '%s has no level file': '%s не має файлу рівня',
    '%s has no saved edits': '%s не має збережених змін',
    'Saved the edited level %s': 'Збережено змінений рівень %s',
    'Restored the original level %s': 'Повернуто оригінальний рівень %s',
    'Warning: edited file %s is not used by any map': 'Увага: змінений файл %s не використовується жодною картою',
    'Edited levels: %s': 'Змінені рівні: %s',
    # bots and vehicles
    'Bots and vehicles': 'Боти й техніка',
    'A spare sound zone becomes a spawner, a pickup becomes a vehicle. Soldiers appear at random places of '
    'their zone when a player enters it and stand and shoot (moving needs navigation the multiplayer maps '
    'do not have). A vehicle spawner creates the vehicle 2 s after the start and again after it is '
    'destroyed. The resources are added to the map\'s preload list. Checked in single player (developer '
    'menu) only.':
        'Непотрібна звукова зона стає спавнером, предмет для підбирання — технікою. Солдати з\'являються у '
        'випадкових місцях своєї зони, коли гравець заходить у неї, і стоять та стріляють (щоб бігати, потрібна '
        'навігація, якої на мультиплеєрних картах немає). Спавнер техніки створює її через 2 с після старту і '
        'знову після знищення. Потрібні ресурси дописуються в список передзавантаження карти. Перевірено лише в '
        'одиночній грі (меню розробника).',
    '%s: %d spare zones, %d pickups': '%s: вільних зон %d, предметів %d',
    'What': 'Що це',
    'Position': 'Позиція',
    'Size': 'Розмір',
    'size': 'розмір',
    'Zones': 'Зони',
    'Pickups': 'Предмети',
    'sound zone': 'звукова зона',
    'bots': 'боти',
    'vehicle spawner': 'спавнер техніки',
    'pickup': 'предмет',
    'vehicle': 'техніка',
    'Select a zone or a pickup': 'Виберіть зону або предмет',
    'Make:': 'Зробити:',
    'Bots': 'Боти',
    'Vehicle spawner': 'Спавнер техніки',
    'Vehicle': 'Техніка',
    'Vehicle:': 'Техніка:',
    'not tested': 'не перевірено',
    'Soldiers:': 'Солдати:',
    'Weapons:': 'Зброя:',
    'At once:': 'Одночасно:',
    'Next one after a kill, s:': 'Новий після вбивства, с:',
    'Respawn after destruction, s:': 'Поява після знищення, с:',
    'Move to': 'Перенести в',
    'Take from:': 'Взяти з:',
    'This zone has more than 4 corners and stays where it is.': 'Ця зона має понад 4 кути й лишається на місці.',
    'The zone becomes a square of this size on the floor at Y. Bots appear inside it when a player enters it.':
        'Зона стане квадратом такого розміру на підлозі на висоті Y. Боти з\'являються в ній, коли туди заходить гравець.',
    'By default the vehicle stands where the pickup was.': 'Без перенесення техніка стоїть там, де був предмет.',
    'Position: numbers expected': 'Позиція: потрібні числа',
    'Choose at least one soldier and one weapon': 'Виберіть хоча б одного солдата й одну зброю',
    'Add / change': 'Додати / змінити',
    'Undo': 'Скасувати',
    'Added: %d': 'Додано: %d',
    '%s: %s → %s': '%s: %s → %s',
    'Select an added object (marked orange)': 'Виберіть доданий об\'єкт (позначено помаранчевим)',
    '%s: %s restored': '%s: %s повернуто',
    '%s: %d resources added to the preload list': '%s: до списку передзавантаження додано ресурсів: %d',
    # map window
    'Map': 'Карта',
    'Select a map first': 'Спершу виберіть карту',
    'Height cut:': 'Зріз за висотою:',
    'none': 'немає',
    'Start points and pickups': 'Точки появи й предмети',
    'Objects': 'Об\'єкти',
    'Lights': 'Світло',
    'Effects and sounds': 'Ефекти й звуки',
    'effect': 'ефект',
    'Names': 'Імена',
    'Fit': 'Уся карта',
    'start point': 'точка появи',
    'object': 'об\'єкт',
    'scene object': 'об\'єкт сцени',
    'zone': 'зона',
    'sound': 'звук',
    'light': 'світло',
    'Drag: rotate, Shift+drag or middle button: move, wheel: zoom, double click: rotate around that point, '
    'right click: copy coordinates':
        'Перетягування: обертання, Shift+перетягування чи середня кнопка: зсув, колесо: масштаб, подвійний клік: '
        'обертати навколо цієї точки, правий клік: копіювати координати',
    'WASD / arrows: move, Q / E: down / up, Shift: faster':
        'WASD / стрілки: рух, Q / E: вниз / вгору, Shift: швидше',
    'Wheel: zoom, drag: move, right click: copy coordinates':
        'Колесо: масштаб, перетягування: зсув, правий клік: копіювати координати',
    'WASD / arrows: move, Shift: faster': 'WASD / стрілки: рух, Shift: швидше',
    '%s: %d triangles, %d objects': '%s: трикутників %d, об\'єктів %d',
    'click: show in the list': 'клік: показати в списку',
    'height %g m': 'висота %g м',
    'height not set': 'висота не задана',
    'This object has no position on the map': 'Цей об\'єкт не має позиції на карті',
    'Copied: %s': 'Скопійовано: %s',
}

TABLES = {'uk': UK}


def detect_language():
    """'uk' when Windows (or the locale) is Ukrainian, otherwise 'en'."""
    try:
        import ctypes
        if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 0x22:   # LANG_UKRAINIAN
            return 'uk'
    except (AttributeError, OSError):
        pass
    try:
        if (locale.getlocale()[0] or '').lower().startswith(('uk', 'ukrainian')):
            return 'uk'
    except ValueError:
        pass
    return 'en'


def set_language(code):
    global _lang
    _lang = code if code in LANGUAGES else 'en'


def get_language():
    return _lang


def _(msg, *args):
    t = TABLES.get(_lang, {}).get(msg, msg)
    return t % args if args else t
