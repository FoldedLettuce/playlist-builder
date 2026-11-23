# Prompt for the session that owns the trailer/sheet automation

Paste everything below the line into that session.

---

I'm building a "Playlist & Schedule Builder" web app for Bensenville Centre Theatre (GDC SR-1000 servers). It reads one new tab in my existing Google Sheet. I need you to make `trailer_automation.py` (and the sheet) produce that tab. The app needs the **run length of every CPL** it puts in a playlist, which my sheet doesn't have today.

Keep my usual rules: all config and credentials in one block at the top of the script, run once per invocation (Task Scheduler every 15 min), health line to the log webhook, main Discord channel only for needs-attention items. Never write to Currently Downloaded's Projector 1 / 2 columns. If anything below is ambiguous or the data contradicts it, stop and ask me instead of guessing.

## 1. New tab: `Playlist Data`

One header row, then one row per CPL. Fully rewrite the tab each run (idempotent). Header names exactly, in this order:

`role, format, title, version, rating, release_date, start_date, end_date, cpl_name, runtime_sec, ffec_sec, only_for_title, notes`

| column | meaning |
|---|---|
| role | `FEATURE`, `TRAILER`, `SV_EARLY`, `SV_LATE`, `COKE`, `CHEVY`, `MARQUEE`, `PRESHOW`, `RATING_CARD` |
| format | `SCOPE`, `FLAT`, or `F-200` (features only, a flat DCP framed F-200 like CatInTheHat...F-200) |
| title | clean film name for FEATURE and TRAILER (no `TLR-x`). Blank for ads |
| version | trailers only, e.g. `TLR-6`, `TLR-1R` |
| rating | `G`, `PG`, `PG-13`, `R`. For ads a comma list (`G,PG,PG-13`) or `ALL`. Rating cards: the one rating |
| release_date | `YYYY-MM-DD`, features and trailers |
| start_date / end_date | `YYYY-MM-DD` inclusive window the ad is valid. Blank = always |
| cpl_name | exactly the name I see in the GDC playlist editor lists (verify against the "C" prefix quirk when copying from the GDC UI) |
| runtime_sec | decimal seconds = CPL Duration / EditRate, 3 decimals (frame accurate). This is the critical new data |
| ffec_sec | features only. Offset of the `FFEC` marker into the feature in seconds, from the CPL's marker list. Blank if the CPL has none |
| only_for_title | optional. Makes an ad apply to one film only (feature-specific SV ads, targeted Coke ads) |
| notes | free text, e.g. why a row is special |

Write the whole sheet cells as plain text/number, not auto-typed. (Excel already turned the Film Name of "November 1963" into the date 11/1/1963 in Currently Downloaded, so format the title columns as plain text and fix that cell.)

## 2. Where each role comes from

Runtimes and FFEC come from the CPL XMLs. Projector 1 (10.0.0.187) has the FTP `content` account (UUID folders, titles only inside CPL XMLs). Read from Projector 1, cross-check Projector 2, and alert me if the two differ.

- **TRAILER**: the trailers currently on the Printout (above the EXCLUDE line). Two rows each (SCOPE `_S_` and FLAT `_F_`). Title/version/rating/release_date from Currently Downloaded (derive title by stripping ` TLR-x` from the Formatted Name, don't trust the Film Name column). Blank rating stays blank.
- **FEATURE**: every `*_FTR*` package on the servers (today: `SunriseReaping_FTR_S...`, `Hexed-wshr_FTR-2D_F...`, `CatInTheHat-wShort_FTR-2-2D_F-200...`). Framing from the `_S_` / `_F_` / `F-200` token. Rating from the name token (`US-PG13`, `US-PG`) else TMDB certification, else leave blank and alert me. Title and release date from TMDB.
- **SV_EARLY / SV_LATE**: packages named like `Fb-SV early-PG13 20261123` and `Fb-SV late-R 20261123`. Rating from the token (`PG13` -> `PG-13`). The number is the Monday the week starts, so start_date = that Monday and end_date = that Sunday. Format `SCOPE`. Special ones like `Fb-SV Godzilla Zero late-R 20261123` are tied to ONE feature: when I'm playing that film its special ad is used as the SV late (or early) instead of the regular one, and every other film still gets the regular one. So emit it as its own row with `only_for_title` set to the feature's title exactly as it appears in the FEATURE rows (e.g. `Godzilla Zero`). The app prefers an `only_for_title` match over the regular row automatically. If you can't tell which feature a special ad belongs to, ask me.
- **COKE**: `KO60-FLT11-GEN_ADV_S/F...`. Dates come from the Printout line `2026 Coke Flight 11 | 10/30 - 12/3`. GEN vs GPG: GPG is for G and PG *animated* titles only, GEN for everything else. Emit GEN rows with rating `ALL`. When a GPG flight is on the server, emit it with `only_for_title` set to each animated title it should apply to (ask me which titles).
- **CHEVY**: `TRL.GMC.xxxx.nnn_*_flat/scope`. Dates and the flat/scope pairs come from the Printout special-ads block (`Chevy ( .146 & .147 ) | 11/20 - 11/25`, then `( .477 & .478 ) | 11/26 - 12/31`). That block is retyped by hand today. Best fix is to add Chevy rows to the Special Ads tab (Placement `Trailer`) and read both from there. This mid-week Chevy switch (ends Wed 11/25, new one Thu 11/26) is exactly what splits a week into a SVW47 and a SVW47.5 playlist in the app, so the dates must be exact.
- **MARQUEE**: the Late Marquee special ads on the Special Ads tab (Google, Samsung...), with the ratings from the Printout block (`G,PG,PG-13`). Assets column gives the campaign codes, map them to the real package names and to scope/flat versions. Dates from Start/End columns.
- **PRESHOW**: `DeSimone_PreShow_2024_scope_dcp` and `..._flat_dcp`, rating `ALL`.
- **RATING_CARD**: `G_RTG_S/F`, `PG_RTG_S/F`, `PG-13_RTG_S/F`, `R_RTG_S/F`, rating = the card's rating.

The app has built-in values for the two black clips (Black MOS 1min = 60s, Black MOS 5sec = 5s), so those don't need rows.

## 3. Alerts (main Discord, needs-attention only)

- A row that should exist but has no `runtime_sec` (CPL unreadable, projector 1 vs 2 mismatch).
- A feature with no rating, or no FFEC marker.
- Two ads that would both be active for the same role, rating, format and date. (A row with `only_for_title` doesn't count as a conflict with the regular row.)
- An SV ad pair (early + late) missing for a rating in the next SV week once the Monday is within 4 days.

## 4. Done means

- The tab matches the header above, and I can open it and see runtimes for everything on the Printout plus this week's features and ads.
- Sample rows I expect (runtimes will be real): a FEATURE row for Hexed (`FLAT`, `PG`), an SV_EARLY row `Fb-SV early-PG13 20261123` with start 2026-11-23 / end 2026-11-29, two CHEVY rows for `.146/.147` ending 2026-11-25 and two for `.477/.478` starting 2026-11-26.
- Re-running with no changes leaves the tab unchanged.
- The Google Sheet stays shared with the app's service account (I'll give you its email if you need to confirm access).

## Special ads: where they come from (changed 2026-11-23)
Do not search for the Ceilio emails and do not download from them anymore. Always take special ads from the information emails and the Dropbox links in them. Liam may need to upload an email himself, so support a manual drop for that. Remove the Ceilio code path when this script is next updated.
