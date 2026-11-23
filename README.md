# Playlist & Schedule Builder

Pick the features, dates and showtimes. It tells you exactly what goes in each GDC playlist
(clips, CPL names, cues) and the exact time code to schedule each one at.

Working on the code? Read `CLAUDE.md` first.

## What's in here
- `/` the builder (staff login is basic auth in CONFIG)
- **Save this plan** stores a snapshot forever in `db/history.db` (SQLite, built into Node 22, nothing extra to install). Saved plans can't be edited or deleted, not even by SQL. Back that file up.
- The hidden admin page (`ADMIN_PATH`, default `/admin`, OFF until you set `ADMIN_PASSWORD`) lists every saved plan, shows the full playlists/schedule as they were, can reopen one in the builder, and exports everything as JSON. It also shows `ROADMAP.md`. Pick an obscure path and a long password.
- Sample data can't be saved (the history is permanent, so no junk goes in).
- A film can run at most 7 days.

## Run it locally
    npm install
    npm start            # http://localhost:3000 using SAMPLE data (placeholder run lengths)
    npm test             # engine + server tests

## Hook it to the real sheet
1. Add the `Playlist Data` tab (see `SHEET_UPDATE_PROMPT.md`, hand that to the session that owns the sheet scripts).
2. Google Cloud: create a service account, download its JSON key, save as `service-account.json` next to server.js.
3. Share the Google Sheet with the service account's email address (Viewer is enough).
4. Set `DATA_SOURCE=sheet`, `SHEET_ID`, `APP_USER`/`APP_PASS` and (optionally) `ADMIN_PASSWORD`/`ADMIN_PATH`. Either edit the CONFIG block at the top of `server.js` or use env vars.

The browser never sees Google credentials. It only calls `/api/rows`.

## Deploy on vm1 with Docker (step by step)

The image is built by GitHub Actions on every push to `main` and published to GitHub Container Registry.
Watchtower then pulls it.

**One time, on GitHub**
1. The repo is https://github.com/FoldedLettuce/playlist-builder (public).
2. Watch the first run under the Actions tab. When it's green, the image exists at `ghcr.io/foldedlettuce/playlist-builder:latest`.
3. A new package is private by default, even in a public repo. Go to your GitHub profile, Packages, `playlist-builder`, Package settings, Change visibility, Public. Then vm1 and Watchtower need no login at all.

**One time, on vm1-public-apps**
1. `mkdir -p ~/stacks/playlist-builder && cd ~/stacks/playlist-builder`
2. Copy `docker-compose.yml` and `.env.example` (as `.env`) here, put the Google key next to them as `service-account.json`, and fill in `.env`.
3. The compose file already points at `ghcr.io/foldedlettuce/playlist-builder:latest`.
4. `docker compose up -d` then check `docker compose ps` shows it `healthy`.
5. In Pangolin add a resource pointing at `<vm1 ip>:3040`. The app has its own staff login, so decide if you also want Pangolin auth in front.
6. Optional: add an Uptime Kuma HTTP monitor on `/healthz` (it needs no login).

**Auto-update with Watchtower**
- The compose file already carries the `com.centurylinklabs.watchtower.enable=true` label. If your Watchtower isn't in label-only mode it's harmless.
- With a public package Watchtower needs no registry login. Nothing secret is in the image, but it does contain the sample data and the theatre's IPs.
- If you ever keep the package private, make a classic token with only `read:packages`, run `docker login ghcr.io` on vm1, and mount `~/.docker/config.json` into Watchtower as `/config.json`. It has to be the plain file with base64 `auths`, a credential helper won't work.
- To deploy immediately instead of waiting for the daily run: `docker compose pull && docker compose up -d`.

**Backups (do this, history is forever)**

    docker run --rm -v playlist-builder_pb-db:/db -v "$PWD":/out alpine tar czf /out/pb-db-$(date +%F).tar.gz -C /db .

The admin page's "Export everything" button gives a JSON copy too. Never run `docker compose down -v`, it deletes the volume.

## How the rules are applied (engine.js)
- Playlist start = posted showtime + 10:00 - preshow length. Seconds are exact, e.g. 15:12:36.
- Reframe cues (Projector Flat / Scope / F-200) pause the playlist 10s and are counted in the math.
- Every schedule row is re-verified by re-adding the clips from the start time: feature must start 10:00 after
  showtime and the show must end at feature end + 5s black (within 0.5s, since GDC only takes whole seconds).
- Trailers: same rating first (lower-rated only if there aren't enough), never above, never the film itself,
  soonest N releasing on/after the playlist's first day, played furthest-out first. N is auto (4-5) so the preshow lands in 25-30 min.
- Playlists split on SV week (Monday) and whenever an ad changes mid-week (.5, .6, ...).
- Turnaround: default screens first, swap if under 15 min, accept 5-15 min with a warning, error under 5.
