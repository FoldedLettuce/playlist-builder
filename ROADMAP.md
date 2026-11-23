# Roadmap

## Done
- Special ads now come from the Cinema Productions information email and its Dropbox links (Cielo HBR emails ignored, weekly SV ads still use Cielo). A hand-saved `.eml` in `email_drop` is read too
- Trailer script and Apps Script are in the repo under `scripts/`, with a self-updating launcher
- Trailer keep rule (script asks the app which trailers are in a running or scheduled playlist, those are never put on the delete list)
- Playlist & schedule builder (exact time codes, SV week splits, theatre swaps, trailer picking, timing verification)
- Permanent history of every saved plan (can't be edited or deleted)
- Hidden admin page with its own password

## Up next (small, soon)
- Script writes the `Playlist Data` tab (features, trailers, SV ads, Coke, Chevy, marquee, preshow, rating cards with run lengths and FFEC). Run lengths are already read from the CPLs, the role mapping needs `--dump-installed` output.

## Next (the big one): trailer management moves into this app
Goal: this app becomes the source of truth for trailers, ads, features and run lengths. The spreadsheet stops being the database.
1. Move the trailer data (available, currently downloaded, download/delete lists, slug rules, special ads) into the app's database, with a UI in the admin page.
2. `trailer_automation.py` talks to this app over an authenticated API (a token for the script) instead of writing to the sheet directly. It keeps doing the projector and email work. The app does the thinking and the bookkeeping.
3. The app writes ONLY the Printout section of the spreadsheet, for the manager. Everything else stays private to Liam inside the app.
4. The `Playlist Data` tab (interim bridge) goes away once step 2 is live. The builder reads straight from the database.
5. Keep the same rules as today: never touch the Projector 1/2 "currently downloaded" columns by script, English 5.1 CCAP only, rating-based placement, the delete rules.

## Later ideas (not promised)
- KDM validity check per feature and screen, shown next to the schedule
- GPG vs GEN Coke ad picked automatically by animated title
- Download a week's plan as a one-page printable sheet for the booth
- Compare a saved plan with what's actually loaded in GDC (after the Next work)
