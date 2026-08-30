# rutina-export

**Get your Health Connect, Samsung Health, FitDays and Hevy data into Notion and
a web dashboard — automatically, every day, without paying a subscription.**

> 🇪🇸 [Léelo en español](README.es.md) · code comments and inline docs are in Spanish.

Apps that export Health Connect on a schedule charge for it. This one doesn't —
and not out of generosity. The automation they charge for is the
`READ_HEALTH_DATA_IN_BACKGROUND` permission, which **is free**. What gates it is
Google Play's review for health apps, and since this app is installed over ADB
and never published, there is no review to pass.

```
                  ┌─ Samsung Health ─┐
   watch / phone ─┤                  ├─► Health Connect ─┐
                  └─ Hevy ───────────┘                   │
                                                         ├─► the android/ app
   FitDays (scale) ─► Health Connect ────────────────────┘         │
                   └─ its own export (15 metrics) ────────────────┤
                                                                   ▼
   tape measure ─────► typed into the app ─────────► data/inbox/ in your repo
                                                                   │
   Hevy (API) ───────────────────────────────────────► workflow ───┤
                                                                   ▼
                                             Notion + dashboard + GitHub Pages
```

## What it collects, and from where

| source | what it gets | how |
|---|---|---|
| **Health Connect** | steps, distance, calories, sleep stages, heart rate, HRV, SpO2 | own app, in the background |
| **Samsung Health** | it's what feeds the above from your watch | via Health Connect |
| **FitDays** | all **15** scale metrics: visceral and subcutaneous fat, skeletal muscle, protein, body age… | its export, by driving its UI |
| **Hevy** | workouts, sets, reps, weights, RPE, PRs, estimated 1RM | official API |
| **tape measure** | chest, abdomen, waist, hips, arms, thighs, calves | typed by hand, in the app |
| **Notion** | five related databases: Days, Workouts, Sets, Exercises, Measurements | official API |

## Why a dedicated app is needed

**Samsung Health has no cloud API.** Nothing outside the phone can read your
steps or your sleep. And **Health Connect is a local database**: only an app
installed on the device can read it. There is no server-side shortcut.

**FitDays only writes 4 of its 15 metrics into Health Connect.** The rest —
visceral, subcutaneous, skeletal muscle, protein, body age — never leave its
app, so they have to come from its own export.

**Hevy does have an API**, which is why workouts are fetched by the workflow
without touching the phone at all.

## Requirements

- An Android 10+ phone with Health Connect.
- A computer with `adb`, to install the app **once**. After that the phone runs
  on its own, on mobile data, with the screen locked.
- Hevy (API key, needs Hevy Pro) and Notion accounts. Both optional: without
  Hevy you get health only, without Notion you get the dashboard only.
- A GitHub repository, which acts as both storage and server.

## Setup

### 1. The phone app

```bash
android/build.sh                                  # builds the APK
python scripts/health_pull.py --instalar          # installs it over ADB
python scripts/health_pull.py --permisos          # grants the permissions
```

You need a JDK **with `javac`** (many distributions ship only the runtime).
`build.sh` finds one by itself.

### 2. The token

On GitHub: Settings → Developer settings → **Fine-grained tokens**.

- Repository access: **Only select repositories** → yours
- Permissions → Repository permissions → **Contents: Read and write**. Nothing else.

Create it **from the phone's browser** and paste it into the app, so it never
has to travel between devices — which is where tokens leak.

In the app, also set your repository (`user/repo`) and hit *Save*. The screen is
a checklist: whatever is missing shows up at the top, in red, with a button.

### 3. FitDays (optional)

If you use their scale and want all 15 metrics, enable the accessibility
service from the app and grant it access to the `Documents` folder. It drives
FitDays' interface on its own.

> The service is restricted to FitDays in `res/xml/accesibilidad.xml`. It sees
> no other app.

### 4. Everything else

```bash
cp config.example.toml config.toml     # your Hevy and Notion keys
pip install -r requirements.txt
python scripts/configurar.py           # it does the rest
```

`configurar.py` creates the five Notion databases, uploads the eight secrets to
your repository, sets the variables and turns on Pages. It is idempotent, and
`--dry-run` tells you what it would do without touching anything.

## How it runs once set up

| when | what happens |
|---|---|
| 20:45 | the app reads Health Connect and uploads to `data/inbox/` |
| on unlock | if due, it exports FitDays and uploads it |
| that push | fires the workflow immediately |
| 21:00 | the cron, as a safety net |

The workflow imports whatever is in the inbox, fetches Hevy workouts, writes to
Notion and republishes the dashboard on GitHub Pages.

**Your computer is not needed.** The `scripts/` bridge is still there if you
prefer it, but the phone is enough.

## Things that were hard to find out

Documented where they happen, but these deserve a warning:

- **Health Connect exports don't accumulate.** The window is a few days, so the
  history lives in `data/raw/*.jsonl` and whatever arrives overwrites that date.
  A day measured halfway fixes itself on the next run.
- **Driving another app's UI requires an unlocked screen.** There is no way
  around it. It doesn't matter much: FitDays' export always contains the full
  history, so skipping days delays but never loses.
- **Exercises are grouped by id, never by name**: Hevy freezes the translated
  name into each workout, so changing the app's language splits your history.
- **Hevy's calories are already in Health Connect**, because Hevy writes there.
  Adding them separately would double-count them.
- **`uiautomator dump` freezes the animations of whatever app you're looking
  at**, and it doesn't recover on its own. Worth knowing if you automate UIs.

## Privacy

Your data goes from your phone to **your** repository. No middleman server, no
one else's account. If you keep it private, GitHub Pages over a private repo
requires Pro (free with the Student Pack).

The token lives in the phone's private `SharedPreferences`; on an unrooted
device no other app can read them.

## Tests

```bash
python tests/test_history.py        # history survives a short export window
python tests/test_sync_offline.py   # syncing never duplicates in Notion
python tests/test_movil.py          # the phone-deletion guard holds
npm install jsdom && node tests/test_dashboard.mjs
```

They run on every push, along with the APK build.

## License

MIT — see [LICENSE](LICENSE).

Exercise animations shown in the dashboard come from Hevy and from
[ExerciseGymGifsDB](https://github.com/JahelCuadrado/ExerciseGymGifsDB); neither
is redistributed here.
