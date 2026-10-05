# TSMapTool — TimeShift (PC) multiplayer map manager

**English** | [Українська](README.md)

TSMapTool adds, browses, exports and removes multiplayer maps in the PC version of TimeShift.
Maps can come from Xbox 360 DLC packages (the Xbox Live content files with long hex names such as
`2F61D2EC…`) or from `.tsmap` files created by the tool itself. This is how the two Xbox-only map
packs, *Futures-Past* and *Urban ReDuel*, can be played on PC.

Requires Python 3 (tested with 3.14). No third-party packages are needed.

## Running

- **GUI:** `TSMapTool.bat`, or double-click `maptool_gui.pyw`.
- **Command line:** `python maptool.py <command>`.

The game is found automatically if the tool folder is inside the game folder. Otherwise, pick it
with **Change…** or pass `--game "D:\Games\TimeShift"`. The choice is saved in `config.json`.

**Interface language:** English or Ukrainian. The Windows display language is used by default.
Switch it with the **Language** box in the window or with `--lang en|uk`; the choice is saved in `config.json`.

Close the game before making changes.

## Features

| Action | GUI | Command line |
|---|---|---|
| List maps | main window | `maptool.py list` |
| Details, description, preview | select a map | `maptool.py info <level> [--preview file.png]` |
| Import an Xbox DLC package or `.tsmap` | **Import file…** | `maptool.py import <file> [...] [--maps a,b]` |
| Import an extracted Xbox folder | **Import folder…** | `maptool.py import <folder>` |
| Export a map to `.tsmap` | **Export…** | `maptool.py export <level> <file.tsmap>` |
| Remove an added map | **Remove / hide** | `maptool.py remove <level>` |
| Hide an original map from the menu | **Remove / hide** | `maptool.py remove <level>` |
| Show a hidden map again | **Unhide** | `maptool.py unhide <level>` |
| Rebuild the game archives | **Apply** | `maptool.py apply` |
| Put the original archives back | **Restore original game** | `maptool.py restore` |
| Check the game archives | — | `maptool.py verify` |
| Edit object scripts and properties | **Scripts** tab | — |
| Add bots and vehicles | **Bots and vehicles** tab | — |

`<level>` is the internal map name, e.g. `dlc1_multiplayer13` or `multiplayer2` (the **Level** column).
Import, remove and unhide are applied to the game right away (use `--no-apply` on the command line to batch them).

## How it works

- **Backup.** The original archives are kept in `preload\paks_backup_original\`. If that folder does not
  exist, the tool creates it, but only while the current archives are still unmodified.
- **Library.** Every added map is a separate file, `library\<level>.tsmap`. The map list, map ids and
  hidden maps are stored in `library\library.json`.
- **Apply.** `patch.s3darc`, `patch_nv.s3darc` and `patch_ati.s3darc` are always rebuilt from the backed-up
  originals plus every map in the library. A removed map leaves nothing behind, and **Restore original game**
  is a plain copy from the backup (the library is kept). `out*.s3darc` are never modified.
- **Safe writes.** New archives are first written as `*.tmp` and checked (CRC of every entry) before they
  replace the live ones.

Importing an Xbox map converts it to the PC format:

- **What is taken from the package.** Only the resources the PC game lacks: level geometry `.lg`,
  collision `.cdt`/`.sm`, grass `.grs`, rain `.rain`, traces `.trc`, lightmaps, new textures and loading
  screens. Everything else comes from the PC game.
- **Textures.** In the `TCIP` header the absolute Xbox offsets are replaced with `0xFFFFFFFF`, as in the PC
  archives. Pixel data is unchanged; the formats are identical on both platforms.
- **Normal maps and masks (3Dc/BC5, BC4).** ATI archives take them as they are. For NVIDIA they are re-encoded
  to DXT5/DXT1 the same way as in the original PC archives.
- **Preload list.** Converted to the PC layout. References to Xbox-only resources (achievement icons, gamepad
  buttons) are dropped, and the shader pairs of the original PC maps are used instead of the Xbox shaders.
- **Menu entry.** Added to `gs_map_list` with the map name and description in the game's 9 languages.
  Languages missing from the DLC get the English text.
- **Map ids.** Taken from the free range 15–29 (where the original code had placeholder DLC entries).

## Scripts and object properties

The **Scripts** tab edits map logic: traps, buttons, doors, jump pads, time-slow zones and so on. Every
object of a level (`.lg`) has a property text, and after the `#ssl` line its script in the game's own
scripting language:

```
DOMAIN {
    height = 5
}
#ssl
override OnUnlocked
    $lazor_effect.Show()          // $name is another object of the level
end
```

- **Usage.** Pick a map, then an object in the list (by default only objects with scripts are shown;
  search works on name, template and text). Edit the text and press **Save changes** or **Save and apply
  to the game**. Changed objects that are not saved yet are marked `●`.
- **Reference.** The right pane lists all built-in script functions (383) with argument types, read from
  the game executable. Double click inserts the call. Syntax, events and methods per object type are
  described in [docs/scripts.md](docs/scripts.md) (in Ukrainian).
- **Check.** Before saving the tool looks for unclosed blocks (`override`/`func`/`if` without `end`),
  extra `end`s and unbalanced quotes. The game has no real syntax check, errors only show up in game.
  For example, the check finds the typo `ovrride OnInitLevel` in the original map `multiplayer8`.
- **Where edits are stored.** The edited level is kept in `library\overrides\<level>.lg` and replaces the
  original one when the archives are rebuilt (for original and added maps alike). **Restore original
  level** drops the edits. Exporting a map to `.tsmap` includes the edited level. Such maps are marked
  `✎` on the **Maps** tab.
- **How it works.** A `.lg` file is a tree of chunks with absolute end offsets. When a text changes
  length, the tool rewrites the headers of every following chunk and of the parent chunks. On all 24
  multiplayer maps saving without changes gives an identical file, and an edited level loads in game.
- **Limits.** Latin characters only (the text is stored as 8-bit). Existing objects can be changed, new
  ones cannot be added. For online play every player needs the same edited levels.

## Bots and vehicles

The **Bots and vehicles** tab adds enemies and vehicles to a multiplayer map. Such maps have no spawners,
so the tool converts spare objects of the map:

- **Bots.** A sound zone becomes a soldier spawner. Soldiers appear at random places of the zone when a
  player enters it, and stand and shoot. Soldiers, weapons, how many at once and the delay after a kill
  can be chosen. They cannot run around: that needs navigation the multiplayer maps do not have.
- **Vehicle spawner.** A sound zone creates a vehicle 2 s after the start and again a set time after it
  is destroyed.
- **Vehicle.** A pickup (energy, grenades ...) becomes a vehicle at the same place or at a given point.

A zone with 4 corners can be moved anywhere; the coordinates can be taken from the player start points
(`start_pos*`) or from pickups. The tool adds the resources needed (templates, textures, sounds) to the
map's preload list, because the game creates only what is listed there. The bike (`bike`) is checked,
the other vehicles are marked as not tested. Added objects are shown in orange, **Undo** restores the
selected one. Internals are described in [docs/scripts.md](docs/scripts.md) (in Ukrainian).

Checked only with the level loaded in single player from the developer menu.

## Online play

All players need the same maps with the same ids. The simplest way is to share the three files
`preload\paks\patch.s3darc`, `patch_nv.s3darc` and `patch_ati.s3darc`. Sharing `.tsmap` files also works,
but everyone must import them in the same order.

## .tsmap format

A ZIP archive:

- `manifest.json` with these fields:
  - `class` — level name;
  - `title` — display name;
  - `source` — where the map comes from;
  - `entry` — map properties for `gs_map_list` without `id`: modes, player counts, `LoadingTexture`, etc.;
  - `strings` — localization strings per game language;
  - `loading` — loading screen texture;
  - `files` — list of files (name, archive entry type, target archive `main`/`nv`/`ati`/`all`).
- `files/NNN.bin` — entry data exactly as it goes into the game archive (not zlib-compressed).

Exporting an original map produces a `.tsmap` with its level, lightmaps, preload list and loading screen.
Shared game assets are not included.

## Limitations

- Only STFS packages with non-fragmented files are supported. The official TimeShift DLCs are such packages.
- A map whose level name already exists in the game cannot be imported.
- Ids above 29 are used only after the 15–29 range is exhausted; such ids have not been tested in game.
- Only multiplayer maps can be managed.
- Map content from the official DLCs belongs to its publishers. The `library\` folder is user data and is
  excluded from the repository by `.gitignore`.

## Layout

```
TSMapTool\
  TSMapTool.bat        GUI launcher
  maptool_gui.pyw      GUI
  maptool.py           command line
  maptool_scripts.py   Scripts tab
  maptool_spawn.py     Bots and vehicles tab
  tsmap\s3darc.py      .s3darc archive reader / writer
  tsmap\xbox.py        Xbox 360 STFS, .s3dpak/.s3dlst, textures
  tsmap\core.py        map library, import / export, archive rebuild
  tsmap\lg.py          .lg levels: object property texts and scripts
  tsmap\spawn.py       bots and vehicles: object conversion, resources for the preload list
  tsmap\i18n.py        interface localization (English strings + Ukrainian table)
  docs\scripts.md      level scripting reference (Ukrainian)
  library\             added maps (*.tsmap, library.json, overrides\) — not in the repository
```

## License

MIT — see [LICENSE](LICENSE).
