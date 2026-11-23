#!/usr/bin/env python3
"""
launcher.py - point Task Scheduler at THIS file (every 15 minutes) instead of trailer_automation.py.

Each run it:
  1. downloads the newest scripts/trailer_automation.py from the GitHub repo (public, no login needed)
  2. checks it (not empty, valid Python, your .env has everything it needs)
  3. swaps it in, keeping the old copy as trailer_automation.py.prev
  4. runs it. If a freshly updated script crashes on its first run it is rolled back and the old one runs instead.
If GitHub cannot be reached it just runs the copy already on disk.

Settings (optional, put them in the same .env the script uses):
  LAUNCHER_REF=main        which branch, tag or commit to follow. Set a commit id to PIN a known-good version.
  LAUNCHER_UPDATE=0        turn updating off completely (still runs the script)
Keep this launcher in the same folder as the .env file and trailer_automation.py. It never updates itself.
"""
import hashlib
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

REPO = "FoldedLettuce/playlist-builder"
REMOTE_PATH = "scripts/trailer_automation.py"
TARGET = "trailer_automation.py"
# the new script is only swapped in when .env has these (the repo copy has no secrets in it)
REQUIRED_ENV = ["IMAP_USER", "IMAP_PASSWORD", "FTP_USER", "FTP_PASSWORD", "SHEET_URL", "SHEET_TOKEN", "TMDB_TOKEN"]

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, TARGET)
PREV = SCRIPT + ".prev"
BAD = os.path.join(HERE, "launcher_bad.txt")
LOG = os.path.join(HERE, "launcher.log")
ENV = os.environ.get("TRAILER_ENV_FILE") or os.path.join(HERE, ".env")


def log(msg):
    line = "%s launcher: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 500_000:
            os.replace(LOG, LOG + ".old")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_env():
    out = {}
    try:
        with open(ENV, "r", encoding="utf-8-sig") as f:
            for raw in f.read().splitlines():
                ln = raw.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError:
        pass
    return out


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "trailer-launcher/1.0"})
    ctxs = [None]
    try:
        import ssl
        import certifi
        ctxs.insert(0, ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    last = None
    for ctx in ctxs:
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last


def try_update(env):
    """Returns True when a new version was installed."""
    ref = env.get("LAUNCHER_REF") or "main"
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, ref, REMOTE_PATH)
    try:
        data = fetch(url)
    except Exception as e:
        log("could not reach GitHub (%s). Running the copy on disk." % e)
        return False
    if len(data) < 20_000 or b"def main()" not in data:
        log("downloaded file looks wrong (%d bytes). Keeping the current script." % len(data))
        return False
    try:
        compile(data, TARGET, "exec")
    except SyntaxError as e:
        log("downloaded file is not valid Python (%s). Keeping the current script." % e)
        return False
    new = sha(data)
    try:
        with open(SCRIPT, "rb") as f:
            cur = sha(f.read())
    except OSError:
        cur = ""
    if new == cur:
        return False
    try:
        with open(BAD, "r", encoding="utf-8") as f:
            if new in f.read().split():
                log("version %s was rolled back before. Staying on the current script." % new[:10])
                return False
    except OSError:
        pass
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        log("update %s is waiting. Your .env is missing: %s. Run make_env.py (see scripts/README.md)." % (new[:10], ", ".join(missing)))
        return False
    tmp = SCRIPT + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    if os.path.exists(SCRIPT):
        try:
            with open(SCRIPT, "rb") as a, open(PREV, "wb") as b:
                b.write(a.read())
        except OSError:
            pass
    os.replace(tmp, SCRIPT)
    log("updated %s -> %s (ref %s)" % (cur[:10] or "none", new[:10], ref))
    return True


def run(args):
    return subprocess.call([sys.executable, SCRIPT] + args, cwd=HERE)


def main():
    args = sys.argv[1:]
    env = read_env()
    updated = False
    if env.get("LAUNCHER_UPDATE", "1") != "0":
        try:
            updated = try_update(env)
        except Exception as e:
            log("update step failed (%s). Running the copy on disk." % e)
    if not os.path.exists(SCRIPT):
        log("no %s here and nothing could be downloaded." % TARGET)
        return 2
    rc = run(args)
    if updated and rc != 0 and not args and os.path.exists(PREV):
        # a brand new version crashed on its first run: go back to the old one and remember the bad one
        try:
            with open(SCRIPT, "rb") as f:
                bad = sha(f.read())
            with open(BAD, "a", encoding="utf-8") as f:
                f.write(bad + "\n")
            os.replace(PREV, SCRIPT)
            log("new version crashed (exit %s). Rolled back to the previous script and running it." % rc)
            rc = run(args)
        except OSError as e:
            log("rollback failed: %s" % e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
