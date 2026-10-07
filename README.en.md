# TSMapTool — TimeShift (PC) multiplayer map manager

**English** | [Українська](README.md)

TSMapTool adds, browses, exports and removes multiplayer maps in the PC version of TimeShift.
Maps can come from Xbox 360 DLC packages (the Xbox Live content files with long hex names such as
`2F61D2EC…`) or from `.tsmap` files created by the tool itself. This is how the two Xbox-only map
packs, *Futures-Past* and *Urban ReDuel*, can be played on PC.

Requires Python 3 (tested with 3.14). No third-party packages are needed. The Map window draws with
`tsmap\native\raster.dll` (in the repository, for a 64-bit Python on Windows); without it the same Python code
draws the map, a frame taking a second or two instead of hundredths and the 3D view drawn more simply (see
**3D**).

## Running

- **Ready-made `TSMapTool.exe`:** on the [Releases](https://github.com/Tarik2142/TSMapTool/releases) page
  (Windows, 64-bit, no Python needed). Put it into a folder of its own, for example `TimeShift\TSMapTool`:
  `config.json` and the map library (`library\`) are kept next to it. Every push to `main` that changes code
  builds a new release `build-N` (`.github\workflows\release.yml`, PyInstaller); it has the GUI only.
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
| Level map (2D / 3D), move, copy and delete objects | **Scripts** tab, **Map** button | — |

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

### Level map

The **Map** button shows the map selected on the **Scripts** tab from above.

- **Objects with models.** Boxes, barrels, vehicles, plants, turrets and so on are drawn with their models
  from the game's templates (archive entries of type 12, the same format as the prototypes of a level; the
  parts of a model are placed relative to their nodes, collision hulls and transparent effects are left out).
  A moved or added object shows the outline of its model until it is saved.
- **Geometry.** The level's triangles are coloured by height, from blue (low) to light (high). The sky,
  player clip walls, AI helper meshes and transparent surfaces are left out.
- **Height cut.** Hides everything that lies entirely above the given height, which shows rooms under roofs
  and single floors. It starts 3 m above the highest start point or pickup.
- **Objects.** Start points, pickups (weapons included), vehicles, objects and zones with their outlines.
  Layers can be switched on and off; effects, sounds and lights are hidden by default.
- **Transparent and effects.** Transparent and non-colliding geometry (`&tra_N`, `&trp_N`, `&nc`: time fields,
  glass, light cones, decals) is not drawn by default, it would cover the map. The **Transparent and effects**
  switch draws it, solid, with the rest (not the sky `&vis`, zones or invisible helper meshes). An object made of
  such geometry only (`act_time_fieldBig`) always has an outline from it: the outline moves and scales with the
  object, the geometry itself is updated after saving.
- **Link to the list.** Clicking an object on the map selects its text in the **Scripts** list (the list
  filters are cleared when they hide it). Selecting an object in the list marks it on the map. Objects
  without a property text (most pickups, start points) are listed in grey when **Only objects with scripts**
  is off: there is nothing to edit in them, but they are listed and can be moved on the map.
- **Controls.** The wheel zooms, dragging or WASD / arrows (Shift: faster) move the map, **Fit** goes back
  to the whole map. The bottom line
  shows X, Y, Z under the cursor (Y is the height of the surface). Right click copies them to the clipboard,
  ready to paste as a position on the **Bots and vehicles** tab.
- **3D.** The **3D** switch shows the level in perspective. Dragging rotates the camera around the point
  where the drag started (marked with a cross; over empty space, a point at the height of the view centre).
  Shift+drag or the middle button moves the camera, the wheel zooms, a double click centres the view on
  the point under the cursor. WASD / arrows move the camera forward, back and sideways relative to where it looks, Q / E down
  and up, Shift three times faster. The keys work with any keyboard layout. The height cut, layers, object selection and coordinates work the same way. Markers hidden
  behind walls are drawn as outlines. The renderer is a software one (`tsmap\native\raster.c`): a frame
  takes hundredths of a second, so the full model is shown while the camera moves too (about 15 frames per
  second). Without `raster.dll` the renderer runs in Python: while the camera moves a simplified model is
  shown (the 12 000 largest triangles), and the full frame appears a second or two after it stops.
  The C renderer keeps a depth buffer, so every pixel shows the nearest surface. Python only draws the
  triangles from far to near, so there a big plane (the ground or water under a map) can cover nearer objects.
- **Building `raster.dll`.** Only needed after changing `raster.c`: `tsmap\native\build.bat` (needs the
  Visual Studio Build Tools with the C++ compiler). The C renderer follows the Python code step by step, so
  both give the same pixels (in 3D C adds a depth buffer); change them together.
- **Zones in 3D.** In the level file a zone is a flat outline at floor height; its height is set in the
  property text: `DOMAIN { height = N }`. In 3D such zones are drawn as prisms N metres high, zones without
  that block (fog, grass, some sound zones) as a dotted outline: the game uses its own default for them.
  The zone height is shown in the hover text.
- **Editing objects.** The **Edit objects** switch opens a bar for start points, pickups, vehicles and other
  instance objects (in 2D and 3D):
  - **Move and turn.** Drag a marker, R / Shift+R turns it by 15°, or type X, Y, Z and the angle. A dragged
    object lands on the highest floor at the new place that is at most 0.5 m above its old position, and
    keeps its height above the floor. **On the floor** drops the selected object, **Original position** puts
    it where the original game level has it. The arrow shows the local Z axis; for start points it is the
    direction the player faces after spawning (checked in game).
  - **Scale.** **Scale X / Y / Z** sets the size of an object along its own axes (Y: the height) as a factor
    of its model; turning and dragging keep it. The engine reads the scale from the record's matrix: the
    original maps have thousands of scaled instances (lamp flares 0.71, boxes 1.29 along one axis, the time field
    effect `act_time_fieldBig` on `dlc1_multiplayer14` 2.32 x 0.45 x 0.84). How strongly stretched objects with
    physics or collision (boxes, vehicles) behave is not checked.
  - **Copy / Delete.** A copy appears next to the object with the same template, flags and property text and
    a new name (`start_pos17`, `item_armor3_2` ...); drag it into place. When scripts use the object being
    deleted (`$name`), the tool warns. Until saved, a deleted object is shown as a red cross, an added one
    with a dashed circle.
  - **Flags.** Check boxes set for which teams the object is a spawn point (no teams, team 1, team 2) and in
    which game modes it is left out (`notIN_DM`, `TDM`, `CTF`, `KOT`, `STM`, `1V1`). Other flags are shown in
    grey and kept. Start points and CTF flag positions are recognised by these flags (`swMP_SP_*`,
    `swMP_RED/BLUE_FLAG_POS`), so they show up on maps where they are not named `start_pos`.
  - **Undo changes** returns the selected object to its saved state (removes a new one).
  - **Zones** (spawners, sound zones, fog, kill zones ...) are moved and turned the same way: by their marker,
    with X, Y, Z and the angle, R / Shift+R; **Original position** puts them back where the original level has
    them. They turn around their centre. **Width** and **Length** stretch a zone along its own axes (the sides
    of the smallest rectangle around it; the width is the one nearer to X) around its centre; the height is set
    by the property text (`DOMAIN { height = N }`). Zones cannot be copied or deleted and have no flags (the
    **Bots and vehicles** tab changes their type). Saving rewrites the zone's vertices (`0xf1`), normals
    (`0x110`) and box (`0x11d`) in place; in the level's spatial grid (`0x21f`) the zone stays where it was. A
    moved spawner triggered at its new place in game; check the other kinds of zones in game.
  - **Catalog.** **Catalog...** lists everything found on the game's multiplayer maps (weapons and items,
    boxes, barrels, turrets, plants ...) and the vehicles of the **Bots and vehicles** tab. The chosen object
    appears on the floor in the middle of the view; drag it into place. It is a record of another map
    without its script part (`#ssl`) and without team / mode flags. The templates, textures and sounds it
    needs are added to the map's preload list when the level is saved; every map already loads all player
    weapons, a weapon pickup only adds its own template (`item_mp_sniper`). The model of the chosen object is
    shown on the right; items such as armour have none (in game they are only an effect).
  - **Templates of the game.** After the objects of the multiplayer maps the catalog lists the other
    templates of the game (about 220: metal containers, wagons, turrets, vehicles, oaks, boxes, and in a
    category of their own, **Debris**, stones, planks, vehicle parts). They are marked "not tested": no
    multiplayer map places them, so whether such an object works in multiplayer is seen only in game. Their
    record gets the template's name and class (the class is written in the template's head). Templates without
    a class (bodies and their parts, weapons in hand, crosshairs), characters, effects and projectiles are
    left out.
  - **Resources of new objects.** Not only the template goes to the preload list but everything the object's
    class names: its crosshair, shot dispatchers and projectiles, effects, lights (`tur_charging` without
    `crs_tur_charging` and `sfx_tur_alt_expl` crashes the game while the map loads). Every save checks again all
    objects that the original level does not have, so a list an older version saved incomplete is completed
    even by **Save and apply** without other changes.

  The changes are saved together with the script edits (**Save** in the map window or on the **Scripts**
  tab). A move changes only the matrix of the record. Copy and delete add or remove a record in the object
  section and update the record count at its start; the rest of the file is written again from the chunk
  tree. Walls and other static geometry cannot be edited: they are part of the level's spatial grid and of
  its collision files. The geometry of doors and gates is updated in the picture after saving.

X points right and Z up. The orientation matches the game: pickups and start points moved on the map end
up where they were put.

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
  maptool_mapview.py   Map window (2D and 3D)
  tsmap\s3darc.py      .s3darc archive reader / writer
  tsmap\xbox.py        Xbox 360 STFS, .s3dpak/.s3dlst, textures
  tsmap\core.py        map library, import / export, archive rebuild
  tsmap\lg.py          .lg levels: object property texts and scripts
  tsmap\spawn.py       bots and vehicles: object conversion, resources for the preload list
  tsmap\mapview.py     top and 3D view of a level: geometry, object positions
  tsmap\native\        map renderer in C (raster.c, raster.dll, build.bat) and its ctypes binding
  tsmap\catalog.py     catalog of objects from all multiplayer maps and the other game templates
  tsmap\i18n.py        interface localization (English strings + Ukrainian table)
  docs\scripts.md      level scripting reference (Ukrainian)
  library\             added maps (*.tsmap, library.json, overrides\) — not in the repository
```

## License

MIT — see [LICENSE](LICENSE).
