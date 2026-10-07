# Скрипти рівнів TimeShift: короткий довідник

Довідник до вкладки «Скрипти» TSMapTool. Сигнатури методів і їхній розподіл за типами об'єктів
взято з exe гри. Опис кожного методу виведено з того, як його використовують оригінальні рівні
(кампанія, 14 мультиплеєрних карт і DLC, разом понад 6000 скриптів). Офіційної документації немає,
тому значення, позначені «?», — здогадки.

## Де живе скрипт

Скрипт — частина тексту властивостей об'єкта, усе після рядка `#ssl`:

```
DOMAIN {
    height = 2.5
}
#ssl
override OnEnter
    $door_actor.Start("TOUCH", 0, 0)
end
```

Інші місця, де трапляється код:

- `#ssl_begin` … `#ssl_end` усередині блоку `OPP_PS { }` спавнера (`AI_SPAWN`) — скрипт, який отримує
  кожен створений спавнером NPC.
- `sml_program { sounds = <<< … >>> }` у звукових зонах — окрема мова звукових станів
  (`if State(ENTER) PlaySound(amb_loop) end`). Стан перемикає `SetState("...")`.

## Синтаксис

| Конструкція | Приклад | Пояснення |
|---|---|---|
| Обробник події | `override OnEnter` … `end` | Виконується, коли трапляється подія. Подія з аргументом: `override OnBhvEnd("RUN")` — лише для цього значення. |
| Своя функція | `func sHide` … `end` | Виклик: `sHide()`. Може повертати `return true` / `return false`. |
| Метод цього об'єкта | `Hide()` | Без `$` — метод об'єкта, якому належить скрипт. |
| Метод іншого об'єкта | `$lazor_effect.Show()` | `$ім'я` — будь-який іменований об'єкт рівня або глобальний об'єкт. |
| Частина екземпляра шаблону | `$s3d_refLocator736\|door_actor.Start("TOUCH",0,0)` | `екземпляр\|об'єкт` — об'єкт усередині вставленого шаблону. |
| Гравець | `$dom_revive.IsBelong($player)` | `$player` — гравець. |
| Затримка | `$dom.Unlock() :20` | Рядок виконається через 20 с **від початку обробника** (не від попереднього рядка). Можна писати вираз: `:$npc.SoundTime("vo_x")+5`. |
| Умова | `if not $gun.IsAlive() or $gun.IsLockedNow()` … `else` … `end` | Є `not`, `and`, `or`, порівняння `>`, `<`, `==`. |
| Перебір | `foreach OPP : @ai_brain ($dom.IsBelong(OPP))` … `end` | Усі об'єкти класу (`@ai_brain` — NPC, `@player` — гравці, `@vhc_bike` — мотоцикли), що задовольняють умову. |
| Коментар | `// текст` | До кінця рядка. |

Рядки — у подвійних лапках, логічні значення `true`/`false` або `1`/`0`. Текст зберігається у
8-бітному кодуванні, тож лише латиниця. Гра не перевіряє синтаксис: помилка просто вимикає частину
скрипту без повідомлень (у `multiplayer8` досі живе одруківка `ovrride OnInitLevel`).

## Події

| Подія | Коли | Де трапляється |
|---|---|---|
| `OnInitLevel` | рівень завантажено | будь-який об'єкт |
| `OnInit` | об'єкт створено | NPC, NPC зі спавнера |
| `OnEnter` / `OnLeave` | хтось увійшов у зону / вийшов | зони (`DOMAIN`) |
| `OnLocked` / `OnUnlocked` | об'єкт заблоковано / розблоковано | будь-який об'єкт з `IACTIVE` |
| `OnHit` | у об'єкт влучили | руйнівні / «вогнепроникні» об'єкти |
| `OnDestroyed` | об'єкт зруйновано / NPC загинув | руйнівні об'єкти, NPC |
| `OnHealthChanged(health)` | змінилось здоров'я | руйнівні об'єкти |
| `OnOpen` / `OnClose` | двері відчинились / зачинились | двері |
| `OnPushed` / `OnPoped` | кнопку натиснуто / відпущено | кнопки |
| `OnPicked(user)` | предмет підібрано | предмети |
| `OnActionFrame(anim, frame)` | анімація дійшла до кадру | анімовані об'єкти |
| `OnAnimBegin(anim)` / `OnAnimEnd(anim)` | анімація почалась / скінчилась | анімовані об'єкти |
| `OnStartTSlow` / `OnStopTSlow`, `OnStartTStop` / `OnStopTStop`, `OnStartTReverse` / `OnStopTReverse` | хтось сповільнив / зупинив / повернув час | будь-який об'єкт |
| `OnSlowed` / `OnUnslowed` | об'єкт потрапив у сповільнення часу / вийшов | анімовані об'єкти |
| `OnComplete` | спавнер вичерпав ворогів (`nKillComplete`) | спавнери |
| `OnBhvEnd(bhv)` | NPC завершив поведінку | NPC |
| `OnAlert`, `OnWounded`, `OnReach(wp)`, `OnWeaponPick`, `OnWeaponLose` | NPC помітив ціль / поранений / дійшов до точки / підібрав / втратив зброю | NPC |
| `OnEye(on)` | гравець подивився на об'єкт / відвів погляд | `look_trigger` |
| `OnExploded`, `OnUsed` | бомба з таймером вибухнула / її використали | бомба з таймером |
| `OnOccupied(user)` / `OnReleased(user)` | гравець сів за турель / вийшов | турелі, що керуються гравцем |
| `OnPathFrame(path, frame)` | техніка дійшла до кадру маршруту | техніка |
| `OnTimer(name)` | спрацював таймер | таймери |

## Методи за типами об'єктів

### Будь-який об'єкт

| Метод | Що робить |
|---|---|
| `Lock()` / `Unlock()` | Додає / знімає блокування. Об'єкт з `IACTIVE { nmbLocks = N }` активний, коли блокувань 0. Так вмикаються зони, спавнери, звукові зони. |
| `IsLockedNow() : bool` | Чи заблоковано зараз. |
| `IsName(name) : bool` | Чи має об'єкт таке ім'я (зручно у `foreach`). |
| `SetState(name)` | Перемикає стан `sml_program` (звукові актори, ефекти). |
| `PlaySound(snd, obj = "")` / `MuteSound(snd, obj = "")` | Грає / глушить звук зі списку `sounds_list` об'єкта. |
| `SoundTime(snd) : float` | Тривалість звуку, зручно для затримок. |
| `ShowMsg(strId, time = 0, usage = "")` | Показує репліку / субтитр за id рядка. |
| `Terminate(time = 0)` | Прибирає об'єкт (ефект, пару тощо) через `time` с. |

### Видимі та анімовані об'єкти

| Метод | Що робить |
|---|---|
| `Show()` / `Hide()` | Показати / сховати (ефекти, моделі, лазери). |
| `Start(anim = "", time = 0, isCycle = false)` | Запустити анімацію об'єкта `DYNAMIC` (двері, ліфти, механізми). |
| `Stop(anim = "")`, `Resume(anim = "", isCycle = false)`, `Toggle()` | Зупинити / продовжити / перемкнути анімацію. |
| `ShowPhys()` / `HidePhys()` | Увімкнути / вимкнути колізію ?. |
| `SetFollow(nameTrk)` / `EndFollow()` | Прив'язати об'єкт до руху іншого / відв'язати. |
| `MoveTo(name)` | Перемістити до іншого об'єкта. |
| `Aimable(type = 1)` | Чи може об'єкт бути ціллю ?. |
| `IsSpawnedFrom(dom) : bool` | Чи створив об'єкт цей спавнер. |

### Фізичні об'єкти (`rigid`)

`ApplyImpulse(nameObj, abs)`, `ApplyImpulseXYZ(x, y, z)`, `ApplyImpulseGlobalDir(namePos, nameDir, abs)` —
штовхнути. `FreezeBody()` / `UnFreezeBody()` — заморозити / відпустити. `Transport(nameObj)` — перенести
в точку іншого об'єкта. `SetFadeTime(timeStart, timeEnd)` — зникнення уламків.

### Зони (`DOMAIN`)

| Метод | Що робить |
|---|---|
| `IsBelong(ent) : bool` | Чи перебуває об'єкт (`$player`, NPC) у зоні. |
| `IsAnyoneInside(ent)` | Чи є хтось усередині ?. |
| `Activate()` / `Deactivate()` | Увімкнути / вимкнути зону видимості (`dom_vis`). |
| `Spawn(num = 1)` | Створити об'єкти зі спавнера (`DOM_SPAWN`). |

Блоки властивостей зон: `DOMAIN { height = … }`, `isActOnce = 1` (спрацьовує один раз),
`trampoline { height = … target = "…" }` (джамп-пад), `AI_SPAWN { … }` (спавнер ворогів, див. нижче).

### Двері

`Open(stayOpen = false)`, `OpenBck()` (відчинити в інший бік), `Close()`, `SetOpenForPlayer(is)`,
`SetOpenForNpc(is)`, `ShowLockedMsg(is)`, `SetOneWay(way)`. Події `OnOpen` / `OnClose`.

### Кнопки

`Push(notify = true) : bool`, `Pop(notify = true) : bool`. Події `OnPushed` / `OnPoped`.

### Руйнівні об'єкти, вибухівка, пастки

| Метод | Що робить |
|---|---|
| `Health() : int`, `IsAlive() : bool` | Здоров'я / чи ціле. |
| `SendDamage(damage = 0)` | Завдати пошкодження. |
| `ToggleFireable(isFireable = false)` | Чи реагує на влучання. |
| `Explode()` | Підірвати (бочки, `dyn_destroy`, техніка). |
| `Trigger()` | Активувати міну. |
| `Activate()`, `Enable()`, `Disable()` | Бомба з таймером. |
| `Enable(enable)` | Увімкнути / вимкнути лазери, лампи-сигналізації. |

### Предмети (зброя, енергія)

`Enable()`, `Disable(regenTime = -1)` — прибрати предмет (і повернути через `regenTime` с ?). Подія `OnPicked(user)`.

### Вітер, вентилятори, силові поля (`FORCE_FIELD`)

`SetSpeed(x, y, z)` — сила й напрям потоку.

### Турелі та техніка

| Метод | Що робить |
|---|---|
| `SetTarget(nameActor = "")` / `EndTarget()` | Навести на ціль / скинути. |
| `ShootBegin(idxAttack = 0)`, `ShootEnd(idxAttack = 0)`, `ShootOnce(idxAttack = 0)` | Стрільба. У техніки перший аргумент — номер турелі. |
| `SetPrediction(pred)`, `TurretKeepShooting(on)`, `SetLaser(on)`, `ShowLaser(isShow)` | Упередження, безперервний вогонь, лазерний приціл. |
| `StartRoute(nameActor = "", isCycle = false, isResumePath = false)` / `StopRoute()` | Рух техніки маршрутом. |
| `LayMines(num)`, `ForceRockets(time)`, `EnableRotors(enable, instant)`, `EnableFloating(enable)`, `SetHealth(health)` | Окремі види техніки (мінний літак, дирижабль, гелікоптер). |

### NPC (боти)

| Метод | Що робить |
|---|---|
| `SetBHV(name)`, `PushBHV(name)`, `PopBHV()` | Увімкнути поведінку з блоку `AI { BEHAVIORS { … } }`. Типи поведінки: `SHOOT` (стояти й стріляти), `RUN` / `WALK` (маршрутом `path = [...]`), `LOOK`, `TURRET`, `WILLROCK` / `MULXNS` / `XNS_HO` (бій із пересуванням, потрібна навігація), `CINE` (анімація). |
| `SetPar(key, val)` | Параметр NPC: `radSeeStraight`, `radSeeSide`, `angleSeeStraight`, `angleSeeSide`, `radHearShot`, `radHearRun`, `radHearWalk`, `StrongRun`, `OffGrenAttach` … |
| `SetSenses(id)` | Набір відчуттів (`"max"` у кампанії). |
| `SetAim(nameTrk)` / `EndAim()`, `SetInterest(nameTrk, mode = "")` / `EndInterest()` | Цілитись / дивитись на об'єкт. |
| `SetApproach(name)` / `EndApproach()`, `SetFollow(nameTrk)`, `SetNoCombat(name)` | Підійти / йти слідом / не битися. |
| `ShootNow(type)`, `SetShootRandAngles(...)`, `SetShootRandDist(...)`, `SetContourShooting(on)` | Стрільба й розкид. |
| `SetHealth(percent = 1)`, `SetHealthGod()`, `IgnoreDamage()` / `AcceptDamage()` | Здоров'я, безсмертя. |
| `Die(nameSeq = "")` | Загинути. `Die("REMOVE")` — просто зникнути. |
| `SetWpn(type)`, `HideWeapon()`, `ShowWeapon()`, `DropWeapon()`, `EnableStrike(type)` / `DisableStrike(type)` | Зброя та удари. |
| `DoAlert()`, `ResetAlerts()`, `Exclaim(name)` | Тривога, вигук. |
| `ExcludeFromNav(type)` / `IncludeToNav(type)`, `NavLink(wpA, wpB)` / `NavUnLink(wpA, wpB)` | Навігація. |
| `IsAlive()`, `IsIdle()`, `IsLive()` | Стан. |

Спавнер ворогів — зона з прапорцем `&dom_ai` і блоками `IACTIVE`, `AI_SPAWN { oppClass, oppAffixes,
nSpawn, nSpawnFirst, nMaxSimultSame, nMaxSimultSameDom, timeOppSpawn, timeAfterKill, nKillComplete, setBHV }`
та `OPP_PS { AI { … } #ssl_begin … #ssl_end }`. Прапорець зони вкладка «Скрипти» не змінює. Як це
працює на мультиплеєрних картах — див. розділ «Боти й техніка».

### Гравець (`$player`)

| Метод | Що робить |
|---|---|
| `DisableInput(disable = true)`, `DisableMove(disable = true)`, `DisableWpnChange(disable = true)` | Заблокувати керування / рух / зміну зброї. |
| `DisableTC(mode, disable = true)`, `StartTControl(mode, effect = false)`, `StopTControl(mode)` | Керування часом. |
| `SetTCEnergy(percent)`, `SetTCRate(rate)`, `EnableTCRecharge(is)`, `SetTStopLength(percent)` | Енергія керування часом. |
| `ShakeCamera(tGrow, max, tReduce, scale = 1)`, `DoRotateView(tgt, time)` | Трясіння / поворот камери. |
| `Transport(nameActor = "")` | Телепорт у точку об'єкта. |
| `SelectWpn(slot, playanim = true)`, `SetupWeapon(first, second, third)`, `SelectHolster(...)`, `ClearGrenades(...)` | Зброя. |
| `Kill(useEff = true)`, `SetGOD(enable)`, `SetFLY(enable)`, `DisableHealthRegen(disable)`, `SetDeathFallHeight(height)` | Смерть, безсмертя, політ, регенерація. |
| `InBike() : bool` | Чи на мотоциклі. |
| `ShowSAMMsg(msg)`, `ShowSAMHint(hint)`, `FailMission(...)` | Повідомлення костюма, провал місії (кампанія). |

## Глобальні об'єкти

| Об'єкт | Методи | Призначення |
|---|---|---|
| `$Light_zones` | `EnableZone(zone, enable, exclusive = true)`, `EnableLight(light, enable, exclusive = true)` | Зони освітлення (на мультиплеєрних картах — сонце надворі / світло всередині). |
| `$mp_rules_sys` | `IsTimeControl() : bool` | Чи дозволено керування часом у поточному режимі. |
| `$farm` | `Create(name)` | Створити об'єкт, позначений `ACTOR { isFarm = 1 }` (NPC, техніку), який не з'являється сам на старті. |
| `$music` | `SetMusic(music, fade = true)` | Музика. |
| `$sob_flare_mng` | `ShowFlare(name)`, `HideFlare(name)` | Відблиски світла. |
| `$effect` | `FadeIn(len)`, `FadeOut(len)`, `StartMotionBlur(...)`, `StopMotionBlur()`, `SetupDOF(...)`, `StopDOF()` | Екранні ефекти. |
| `$AI` | `NavLink(sys, a, b)`, `NavUnLink(sys, a, b)`, `TermBrain(name)` | Навігація NPC. |
| `$tc` | `SetMode(mode)` | Підказки керування часом ?. |
| `$sw_objective_sys`, `$ui`, `$saves_mng`, `$dlg`, `$fmv`, `$mirrorSys` | `ObjectiveAdd/Complete/Failed/Remove`, `ShowTimeHint`, `AutoSave`, `StartDlg`, `Play`, `SetMirrorState` | Завдання, підказки, збереження, діалоги, ролики, дзеркала — переважно кампанія. |

Решта функцій зі списку у вкладці «Скрипти» (`FillServerList`, `ShowFrame`, `SetVideoQuality` …) належить меню гри і на рівнях не працює.

## Приклади з мультиплеєрних карт

Зона, що вмикає сонячне освітлення надворі:

```
#ssl
override OnEnter
    $Light_zones.EnableZone("zone_outer", true)
end
```

Дверцята щитка відчиняються від пострілу (Sky Hook, `multiplayer1`):

```
destroy {
    isFireable = 1
    health = 10
    isIndestructible = 1
}
#ssl
override OnHit
    Start("HIT", 0, 0)
end
```

Звук вулиці вимикається, поки гравець у вентиляції (Supply Dump, `multiplayer8`):

```
#ssl
override OnEnter
    $sndDom_amb_Street.Lock()
end
override OnLeave
    $sndDom_amb_Street.Unlock()
end
```

Sanctorum (`multiplayer11`) вимикає поле сповільнення часу в режимах без керування часом:

```
#ssl
override OnInitLevel
    if not $mp_rules_sys.IsTimeControl()
        $dom_time.Lock()
        $sndActor_Time_Field.SetState("OFF")
        $time_effect.Hide()
        $sndDom_amb_Time_Field.Lock()
    end
end
```

## Боти й техніка на мультиплеєрних картах

Перевірено на Supply Dump (`multiplayer8`) у меню розробника (одиночне завантаження рівня). Як це
поводиться в мережевій грі, не перевірялося. Усе описане нижче робить вкладка «Боти й техніка»
(`tsmap/spawn.py`), тут — як це влаштовано.

**Що треба змінювати, крім тексту:**

- **Тип зони** — рядок прапорців вузла сцени (чанк `0x115`, наприклад `&dom_snd` → `&dom_ai`). На
  мультиплеєрних картах готових спавнерів немає, тож беруть непотрібну зону (відлуння `eaxDom_*`,
  звук) і перетворюють. Зону з 4 вершинами можна перенести в будь-яке місце (чанк `0xf1` з вершинами
  і `0x11d` з габаритами).
- **Розставлений об'єкт** — запис `SNIA` в секції `0x1b8`: `ім'я\0 шаблон\0 клас\0 \0` + матриця 4×4
  (позиція в останньому рядку). Підбирання (`item_*`) можна перетворити, наприклад, на мотоцикл:
  шаблон `bike`, клас `vhc_bike`.
- **Список передзавантаження** (запис типу 0 з ім'ям карти, однаковий у `patch`, `patch_nv`,
  `patch_ati`). Гра вантажить лише ресурси зі списку, тож шаблон, якого там немає, тихо не створюється.
  Солдати (`soldier_*`, `police_*`) у списках мультиплеєрних карт уже є (це моделі гравців), техніки —
  немає. Для мотоцикла потрібні шаблони `bike`, `deb_atv_01…08`, текстури `atv_1`, `atv_2`,
  `plw_crb_expl_flame`, `time_glow`, банк звуків `obj_moto` і звуки `whc_moto_*`.

**Боти** (зона `&dom_ai`):

- Спрацьовує, коли гравець **заходить у зону**. Солдати з'являються у **випадкових місцях зони**.
- Поведінка `SHOOT` (стояти й стріляти) працює. `MULXNS` / `WILLROCK` (бій із пересуванням) — ні:
  на мультиплеєрних картах немає навігації (точок `&wp`), і боти стоять.
- Далекий зір і слух: `SetPar("radSeeStraight", 150)`, `SetPar("radHearShot", 150)` тощо в `OnInit`
  скрипту `OPP_PS`.

**Техніка** (зона `&dom_spawn`):

- `DOM_SPAWN { class = "vhc_bike"  tpl = "bike" }` нічого не створює сам: потрібен виклик `Spawn(1)`.
  `timeAfterSpawn` техніку не відроджує.
- Скрипт у `SPAWN_PS { #ssl_begin … #ssl_end }` отримує кожен створений об'єкт.
- Відродження через 10 с після знищення:

```
SPAWN_PS {
	#ssl_begin
		override OnDestroyed
			$my_spawner.Lock()          // техніку знищено: блокуємо спавнер
		end
	#ssl_end
}
#ssl
override OnInitLevel
	Spawn(1)	:2                      // перша поява після старту
end
override OnLocked
	Unlock()	:10                     // відкладений виклик живе в спавнері
end
override OnUnlocked
	Spawn(1)
end
```

**Мех** (клас `mech` з каталогу, розставлений на карті):

- Як у кампанії, мех — це запис без шаблону з класом `mech`: клас бере модель `mech` з анімаціями ходьби,
  поворотів, стрільби й загибелі (перевірено в грі: мех ходить маршрутом з анімацією кроків). Шаблон
  `mech_city1` — «зламаний» мех зі сцени `ts2_city_part01` з анімацією `BREAKING`; він стріляє, але
  маршрутом рухається без анімації кроків.
- Маршрут `WALK` / `RUN` з `path = [ … ]`: мех іде від точки до точки (точки в кампанії — вузли `&wp`).
- Об'єкт з каталогу приходить без тексту властивостей. Текст і скрипт пишуть на вкладці «Скрипти» в його
  сірому рядку після першого збереження.
- Щоб мех стріляв, поведінка `SHOOT` має бути без пересування: `distWillRock = 0` (так у всіх мехів
  кампанії, що стріляють самі) і `place = HERE`, плюс `SetSenses("max")`. Без цього мех цілиться в гравця,
  але не стріляє: схоже, чекає на маневр, для якого на мультиплеєрній карті немає навігації. Перевірено в
  грі на Supply Dump (`multiplayer8`).
- Затримку `:N` ставлять у кінці команди, а не в рядку `override`.
- Зброя — клас `wpn_mech_guns`: удари `queue` (черга з кулемета, 30 пострілів), `miniQueue` (10 пострілів)
  і `single` (ракета). Непотрібні вимикає `DisableStrike("single")`. Постріл на вимогу — `ShootNow("queue")`.

```
AI { isDropGrenade = 0
  BEHAVIORS {
	FIGHT {
		type = SHOOT
		place = HERE
		distWillRock = 0
		}
	}
}

#ssl

override OnInit
	SetSenses("max")
	SetPar("isEagleEye", 1)         // далекий зір
	SetBHV("FIGHT")		:6
end
```

## Мережева гра

Скрипти виконуються в кожного гравця на його копії рівня. Тому змінений рівень мусить бути в усіх
учасників (див. README). Як саме гра синхронізує стани об'єктів між гравцями, не досліджено. На це
можуть впливати властивості `isSyncMP`, `isSyncTR`, `isSyncSL` у тексті об'єкта ?.
