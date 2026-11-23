# CLAUDE.md

Guide for Claude (and humans) working on this repo. Read it before changing anything.

## What this is

A playlist and schedule generator for Bensenville Centre Theatre (two screens, GDC SR-1000 servers). The owner and developer is Liam, the in-house tech. Staff use the builder page. Liam uses the hidden admin page.

You pick features, dates, and showtimes. The app tells you exactly what to build in each GDC playlist (clips, CPL names, cues, Screenvision ads, trailers) and the exact time code to schedule each one at. Every result is re-verified so the feature starts exactly 10:00 after the posted showtime.

Stack: Node 22 (built-in `node:sqlite`, so Node must be 22.13 or newer), Express, vanilla JS, Bootstrap 4.3. Only two runtime dependencies (`express`, `google-auth-library`). Keep it that way unless there is a strong reason.

## Commands

    npm install
    npm start        # http://localhost:3000 on SAMPLE data (placeholder run lengths, saving is blocked)
    npm test         # engine + server tests, must be green before any commit

Real data needs `DATA_SOURCE=sheet`, `SHEET_ID`, and a `service-account.json`. See README.

## Layout

| Path | Job |
|---|---|
| `public/engine.js` | ALL scheduling logic. Pure functions, no DOM, no network, no clock. Runs in the browser (`window.PB`) and in Node (`require`) |
| `public/app.js`, `public/index.html` | Builder UI. Re-renders the whole page on every change. State is saved in `localStorage` key `pb-state-v1` |
| `server.js` | Express server. CONFIG block at the top, sheet reader, `/api/rows`, `/api/plans`, `/healthz`, staff basic auth, admin routes |
| `admin/` | Hidden admin page (served by `server.js` at `adminPath`, never from `public/`) |
| `data/sample-rows.json` | Sample dataset in the `Playlist Data` tab shape. Real names and dates, PLACEHOLDER run lengths |
| `tools/make_sample.py` | Regenerates the sample from the real `Current_Trailers.xlsx` |
| `test/` | `engine.test.js` (rules, with a small hand-checkable synthetic dataset) and `server.test.js` (spawns the real server) |
| `SHEET_UPDATE_PROMPT.md` | Spec and prompt for the session that owns `trailer_automation.py` and the sheet |
| `ROADMAP.md` | Roadmap. The admin page shows this file |

## Data contract

The app reads ONE tab, `Playlist Data`, one row per CPL. Columns:
`role, format, title, version, rating, release_date, start_date, end_date, cpl_name, runtime_sec, ffec_sec, only_for_title, notes`.
Roles: `FEATURE`, `TRAILER`, `SV_EARLY`, `SV_LATE`, `COKE`, `CHEVY`, `MARQUEE`, `PRESHOW`, `RATING_CARD`. Full meaning is in `SHEET_UPDATE_PROMPT.md`. `loadData()` in the engine parses it and is forgiving about date formats (`YYYY-MM-DD` or `M/D/YYYY`) and durations (seconds or `H:MM:SS`).

## Business rules (encoded in engine.js, locked in by tests)

- The feature starts exactly 10:00 after the posted showtime. Playlist start = showtime + 10:00 - preshow length. Start times are whole seconds because GDC only takes whole seconds, so up to 0.5s of error is accepted and shown.
- Every Projector Flat / Scope / F-200 cue pauses the playlist 10 seconds. The pause is counted in the preshow length. A Flat film has one, an F-200 film has two.
- Playlist order is always: Black 1min, SV early, SV late, Coke, Chevy, trailers, marquee, DeSimone PreShow, rating card, feature, Black 5sec. Cues: Show Start - Scope, Coke, Trailers, Feature, Credits (at the FFEC marker), Show End. Always Show Start - Scope, never the Flat one.
- Trailers: same rating first, lower-rated only if there are not enough, never above, never unrated, never the film's own. Pick the soonest-releasing N (release on or after the playlist's first day) then play them furthest-out first. N is auto (4 to 5) so the preshow lands in 25 to 30 minutes. If there are not enough upcoming ones, fall back to already-released and warn.
- Playlist names are `SVW## Title`. SV weeks run Monday to Sunday. Week 1 is the first Monday of the calendar year, so Mon 2026-11-23 is SVW47. A playlist splits at every SV week boundary and whenever an ad changes mid-week (second period is `.5`, then `.6`). A film that opens after a mid-week switch is still the `.5` playlist.
- Ads are picked by role, date window, rating, and format. A row with `only_for_title` beats the regular row (this is how a feature-specific SV late ad like the Godzilla one works). Two equally good matches raise an "ambiguous" warning.
- Theatre assignment: default screens are #1 for 3:30 and 6:30, #2 for 4:00 and 7:00. If the turnaround before a preshow is under 15 minutes, try the swapped layout. If neither reaches 15, take the better one when it is at least 5 minutes and warn. Under 5 is an error that needs a manual call.
- A film can run at most 7 days (new bookings are not known further out). A week can still have one film through Tuesday and a new one opening Wednesday.
- Missing runtimes or missing SV ads are loud errors. Never silently produce times from partial data.

Do not change these numbers or rules without Liam saying so. If a rule changes, change the engine AND the test AND this section in the same commit.

## History and admin (important)

- "Save this plan" writes a permanent snapshot to SQLite at `db/history.db`. Plans can never be edited or deleted. SQLite triggers enforce it, and there is no delete or update endpoint. Never add one. Schema changes must be additive (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN`).
- Sample data must never be saved (`canSave` is false unless `ALLOW_SAMPLE_SAVE=1`, which is for tests only).
- The admin page has its own password, a signed httpOnly cookie, a rate limit, and a configurable path. No `adminPassword` means no admin routes at all. Do not weaken any of that.
- Google credentials stay on the server. `/api/rows` is the only thing the browser can ask for.

## Conventions

- Plain JS, no build step, no semicolons, two-space indent. Keep `engine.js` pure so it stays testable in Node.
- All server config lives in the single CONFIG block at the top of `server.js` (env vars override). Do not scatter config around.
- Bootstrap 4.3 links must be exactly: `cdn.jsdelivr.net/npm/bootstrap@4.3.1/`, `cdn.jsdelivr.net/npm/popper.js@1.14.7/`, `code.jquery.com/jquery-3.3.1.slim.min.js`.
- Write docs and comments without semicolons.
- Every rule change gets a test. Use the `synth()` dataset in `engine.test.js` when you want numbers you can verify by hand.
- Dates are ISO strings (`2026-11-23`) and times are seconds. No `Date.now()` in the engine.

## Common edits

| I want to... | Change |
|---|---|
| Default showtimes | `DEFAULT_SLOTS` in `engine.js` |
| Screen names or IPs | `DEFAULT_CFG.screens` in `engine.js` |
| Turnaround, lead time, preshow target, pause length, 7-day cap | `DEFAULT_CFG` in `engine.js` |
| Trailer picking | `pickTrailers()` and `buildSegmentPlaylist()` |
| Add or move a cue or clip | `assemble()` |
| Ad choice or tie-breaks | `findAd()` |
| Which films default to which showtimes | `defaultFilmSlots()` |
| Theatre swap logic | `assignScreens()` |
| Something on the page | `public/app.js` (the `*Card()` functions) |
| Something on the admin page | `admin/admin.js`, routes in `server.js` |

## Checking UI changes

There is no browser test suite. After UI edits, run `npm test`, then load the page. A quick headless check that worked well: serve the app, open it in jsdom with a seeded `localStorage['pb-state-v1']`, click the Save button, and confirm no page errors. For visuals, headless Chrome screenshots work (swap the CDN CSS for a local copy if the sandbox has no internet).

## Deploying

    edit on a branch -> pull request (CI runs the tests) -> merge to main
    -> CI builds the image and pushes ghcr.io/<owner>/playlist-builder:latest and :sha-xxxxxxx
    -> Watchtower on vm1-public-apps pulls it on its daily run (4am) and restarts the container
    -> to deploy right now:  cd ~/stacks/playlist-builder && docker compose pull && docker compose up -d

Rollback: set `image:` in the compose file to an older `:sha-xxxxxxx` tag and `docker compose up -d`. Remove the Watchtower label while pinned, or it will jump back to `latest`.

The compose file, `.env.example`, and README have the setup steps. History lives in the `pb-db` Docker volume, so back it up (README has the command) and never run `docker compose down -v`.

## Never

- Never commit `.env`, `service-account.json`, or anything in `db/`.
- Never push straight to main with failing tests. A merge to main ships to the theatre within a day.
- Never make the history mutable, never save sample data, never expose Google credentials to the browser.
