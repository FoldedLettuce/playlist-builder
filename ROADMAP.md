# Roadmap

## Done
- Playlist & schedule builder (exact time codes, SV week splits, theatre swaps, trailer picking, timing verification)
- Permanent history of every saved plan (can't be edited or deleted)
- Hidden admin page with its own password

## Up next (small, soon)
- Put the trailer script and the Google Apps Script into this repo under `scripts/` so they are versioned. Secrets and per-machine config (`config.json`, service account keys, tokens) stay out of git.
- Trailer keep rule in the script: never put a trailer on the delete list while a running or scheduled playlist uses it (superseded versions like Ice Age TLR-1 stay until their last playing day). Needs the app to say which trailers are in use. Saved plans already record that, so expose it over a token-protected endpoint.
- Self-updating Python script. The Task Scheduler job (every 15 min) runs a tiny launcher that fetches the latest script from the repo, checks it, and then runs it, so edits never need a trip on site. Falls back to the last good copy if GitHub can't be reached.

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
