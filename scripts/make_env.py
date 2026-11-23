#!/usr/bin/env python3
"""
make_env.py - run ONCE on the theatre PC, in the folder that has your CURRENT trailer_automation.py.

It copies your private settings out of that script into a file called .env next to it, so the self-updating launcher
can replace the script without losing them. It only reads your script. It never sends anything anywhere.

    python make_env.py                 (reads trailer_automation.py in this folder)
    python make_env.py --force         (overwrite an existing .env)
    python make_env.py C:/path/to/old_script.py
"""
import os
import re
import sys

KEYS = ["IMAP_USER", "IMAP_PASSWORD", "FTP_USER", "FTP_PASSWORD", "SHEET_URL", "SHEET_TOKEN", "TMDB_TOKEN",
        "DISCORD_WEBHOOK", "LOG_WEBHOOK", "KUMA_PUSH_URL", "ADS_TARGET_ROOT", "ADS_ARCHIVE_ROOT",
        "PLAYLIST_APP_URL", "PLAYLIST_APP_TOKEN"]

here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
src = args[0] if args else os.path.join(here, "trailer_automation.py")
out = os.path.join(os.path.dirname(os.path.abspath(src)), ".env")
if os.path.exists(out) and "--force" not in sys.argv:
    sys.exit("%s already exists. Use --force to overwrite it." % out)

text = open(src, "r", encoding="utf-8").read()
lines, found, blank = [], [], []
for k in KEYS:
    m = re.search(r'^%s\s*=\s*(r?)("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')' % k, text, re.M)
    if not m:
        blank.append(k)
        continue
    raw = m.group(2)[1:-1]
    if not m.group(1):
        raw = raw.replace("\\\\", "\\")
    if raw:
        lines.append("%s=%s" % (k, raw))
        found.append(k)
    else:
        blank.append(k)
with open(out, "w", encoding="utf-8") as f:
    f.write("# Private settings for trailer_automation.py. Never commit or share this file.\n")
    f.write("\n".join(lines) + "\n")
print("Wrote %s with %d settings: %s" % (out, len(found), ", ".join(found) or "none"))
if blank:
    print("Blank or not found in the script (fill in by hand if you use them): " + ", ".join(blank))
