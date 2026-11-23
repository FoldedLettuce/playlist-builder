# Trailer scripts

| File | What it is |
|---|---|
| `trailer_automation.py` | The trailer automation. Runs on the theatre PC every 15 minutes |
| `launcher.py` | What Task Scheduler runs. Pulls the newest `trailer_automation.py` from this repo, checks it, runs it |
| `make_env.py` | One time helper, copies your private settings out of the old script into `.env` |
| `.env.example` | The settings that live in `.env` on the PC (never committed) |
| `TrailerManager.gs` | The Google Apps Script for the sheet. Paste it into the sheet's Apps Script editor by hand, then Deploy, Manage deployments, New version. Set `WEB_SECRET` there only, never here |
| `test_trailer_automation.py` | Tests for CPL timing and the keep rule |

## Switch the theatre PC to the launcher (once)

All of this happens in the folder that has your current `trailer_automation.py`.

1. Download `launcher.py` and `make_env.py` from this folder into that folder.
2. `python make_env.py` makes `.env` from your current script. Open it and check the values are there.
3. Add the keep rule settings to `.env`: `PLAYLIST_APP_URL=https://your-app-address` and `PLAYLIST_APP_TOKEN=` the same text as `API_TOKEN` in the app's `.env` on vm1.
4. `python launcher.py --check` should say everything is ready.
5. `python launcher.py --dry-run` shows what it would do.
6. In Task Scheduler change the action from `trailer_automation.py` to `launcher.py`. Same folder, same 15 minute trigger.

## How updates work

- Every run the launcher downloads `scripts/trailer_automation.py` from `main`, checks it is valid Python, and swaps it in. The old one is kept as `trailer_automation.py.prev`.
- If a new version crashes on its first run it is rolled back at once, the bad version is remembered in `launcher_bad.txt` and skipped.
- If GitHub is down it runs the copy on disk.
- To pin a known good version put `LAUNCHER_REF=<commit id>` in `.env`. To stop updating put `LAUNCHER_UPDATE=0`.
- `launcher.py` never updates itself. Change it by hand on the PC.
- Anything merged to `main` that touches `trailer_automation.py` reaches the PC within 15 minutes. Tests run first in CI, but still prefer a pull request.

## Handy modes

- `--check` tests every connection
- `--dry-run` shows the plan and sends nothing
- `--dump-installed` reads both projectors fresh into `installed_dump.csv` (names, run lengths, FFEC)
