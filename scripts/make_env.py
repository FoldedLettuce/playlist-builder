#!/usr/bin/env python3
"""
make_env.py - run ONCE on the theatre PC, in the folder that has your CURRENT trailer_automation.py.

It copies private settings that are still written inside that script into the .env file next to it, so the
self-updating launcher can replace the script without losing them. It only ever ADDS settings:
a value that is already filled in your .env is never changed, and nothing is sent anywhere.

    python make_env.py                       reads trailer_automation.py in this folder
    python make_env.py C:/path/to/old.py     reads a different script
    python make_env.py --show                just lists which settings are filled in .env (names only, no values)
"""
import os
import re
import sys

KEYS = ["IMAP_USER", "IMAP_PASSWORD", "FTP_USER", "FTP_PASSWORD", "SHEET_URL", "SHEET_TOKEN", "TMDB_TOKEN",
        "DISCORD_WEBHOOK", "LOG_WEBHOOK", "KUMA_PUSH_URL", "ADS_TARGET_ROOT", "ADS_ARCHIVE_ROOT",
        "PLAYLIST_APP_URL", "PLAYLIST_APP_TOKEN"]


def read_env(path):
    vals, text = {}, ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        for raw in text.splitlines():
            ln = raw.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                vals[k.strip()] = v
    return vals, text


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = args[0] if args else os.path.join(here, "trailer_automation.py")
    out = os.path.join(os.path.dirname(os.path.abspath(src)), ".env")
    have, text = read_env(out)
    if "--show" in sys.argv:
        for k in KEYS:
            print("%-20s %s" % (k, "filled" if have.get(k) else "EMPTY"))
        return 0
    code = open(src, "r", encoding="utf-8").read()
    add, kept = [], []
    for k in KEYS:
        if have.get(k):
            kept.append(k)
            continue
        m = re.search(r'^%s\s*=\s*(r?)("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')' % k, code, re.M)
        if not m:
            continue
        raw = m.group(2)[1:-1]
        if not m.group(1):
            raw = raw.replace("\\\\", "\\")
        if raw:
            add.append("%s=%s" % (k, raw))
    if add:
        with open(out, "a", encoding="utf-8", newline="\n") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            if not text:
                f.write("# Private settings for trailer_automation.py. Never commit or share this file.\n")
            f.write("\n".join(add) + "\n")
    print(".env: %s" % out)
    print("Already filled (left alone): %s" % (", ".join(kept) or "none"))
    print("Added from the script:       %s" % (", ".join(l.split("=")[0] for l in add) or "none"))
    have2, _ = read_env(out)
    empty = [k for k in KEYS[:7] if not have2.get(k)]
    if empty:
        print("STILL EMPTY and needed: %s" % ", ".join(empty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
