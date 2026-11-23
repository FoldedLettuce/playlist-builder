"""Tests for the Playlist Data tab. The fixture is a trimmed copy of a real --dump-installed file (no emails, no secrets).
Run:  python -m unittest discover -s scripts -p "test_*.py" """
import csv
import importlib.util
import os
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ta", os.path.join(HERE, "trailer_automation.py"))
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)

MONDAY = date(2026, 11, 23)
PRINTOUT = [
    ["2026 Coke Flight 11", "10/30 - 12/3", "", ""],
    ["GPG = G & PG ANIMATED TITLES ONLY", "", "", ""],
    ["GEN = ALL OTHER PG-R TITLES", "", "", ""],
    ["", "", "", ""],
    ["Special Advertisements", "Dates", "Position", "Ratings"],
    ["Chevy ( .146 & .147 )", "11/20 - 11/25", "Trailer", "All"],
    ["Chevy ( .477 & .478 )", "11/26 - 12/31", "Trailer", "All"],
    ["Google ( .339 & .347)", "11/17 - 11/22", "Late Marquee", "G,PG,PG-13"],
]
FILMS = {"HEXED": {"ok": True, "title": "Hexed", "release": "2026-11-25", "rating": "PG"},
         "SUNRISEREAPING": {"ok": True, "title": "Sunrise Reaping", "release": "2026-10-30", "rating": "PG-13"},
         "CATINTHEHAT": {"ok": True, "title": "The Cat in the Hat", "release": "2026-10-23", "rating": ""}}


def lookup(slug, hint):
    return FILMS.get(slug.upper(), {"ok": False, "title": "", "release": None, "rating": ""})


def load_inst(mutate=None):
    """The dump as plan['inst'], the same shape build_plan makes (names unique, one entry per projector)."""
    inst = {}
    with open(os.path.join(HERE, "fixtures", "installed_dump_sample.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = inst.setdefault(r["name"], {"hosts": set(), "kind": r["kind"], "cpls": set(), "dur": {}, "ffec": {}})
            d["hosts"].add(r["projector"])
            d["cpls"].add(r["cpl_id"])
            if r["dur_sec"]:
                d["dur"][r["projector"]] = float(r["dur_sec"])
            if r["ffec_sec"]:
                d["ffec"][r["projector"]] = float(r["ffec_sec"])
    if mutate:
        mutate(inst)
    return inst


def trailer_plan(inst, deletes=None, kept=None):
    managed, films = {}, {"Hexed": FILMS["HEXED"], "Drag": {"ok": True, "title": "Drag", "release": "2027-01-29", "rating": "R"},
                          "DunePart3": {"ok": True, "title": "Dune: Part Three", "release": "2026-12-18", "rating": "PG-13"}}
    for name in inst:
        p = ta.parse_dcp(name)
        if p["kind"] == "TLR" and p["slug"] in films:
            managed[name] = p
    return {"inst": inst, "managed": managed, "deletes": deletes or {}, "kept": kept or {},
            "film_of": lambda p: films[p["slug"]]}


def build(mutate=None, printout=PRINTOUT, state=None, when=MONDAY, deletes=None, kept=None):
    S = state or ta.new_state()
    ta.TODAY_OVERRIDE = when
    try:
        plan = trailer_plan(load_inst(mutate), deletes, kept)
        rows, alerts = ta.playlist_data(S, plan, ta.parse_printout(printout, when), lookup, t=when)
    finally:
        ta.TODAY_OVERRIDE = None
    return [dict(zip(ta.PD_HEADER, r)) for r in rows], alerts


def only(rows, **kw):
    return [r for r in rows if all(r[k] == v for k, v in kw.items())]


class Header(unittest.TestCase):
    def test_header_order_and_text_cells(self):
        self.assertEqual(ta.PD_HEADER[:13], ["role", "format", "title", "version", "rating", "release_date", "start_date",
                                             "end_date", "cpl_name", "runtime_sec", "ffec_sec", "only_for_title", "notes"])
        self.assertEqual(ta.PD_HEADER[13], "except_titles")
        rows, _ = build()
        self.assertTrue(all(isinstance(v, str) for r in rows for v in r.values()))

    def test_running_twice_gives_the_same_tab(self):
        a, _ = build()
        b, _ = build()
        self.assertEqual(a, b)

    def test_payload_tab_is_plain_text_replace(self):
        S = ta.new_state()
        plan = {"inst": {}, "managed": {}, "downloads": [], "waiting": [], "deletes": {}, "review": {}, "auto_ignored": {},
                "status": {}, "film_of": None, "kept": {}}
        ta.TODAY_OVERRIDE = MONDAY
        try:
            pl = ta.build_payload(S, plan, [], {}, [], [["FEATURE"] + [""] * 13])
        finally:
            ta.TODAY_OVERRIDE = None
        tab = [t for t in pl["tabs"] if t["name"] == "Playlist Data"][0]
        self.assertEqual((tab["mode"], tab["plain"], tab["headers"]), ("replace", True, ta.PD_HEADER))


class Features(unittest.TestCase):
    def test_three_features_with_format_rating_runtime_and_ffec(self):
        rows, alerts = build()
        f = {r["title"]: r for r in only(rows, role="FEATURE")}
        self.assertEqual(len(f), 3)
        self.assertEqual((f["Hexed"]["format"], f["Hexed"]["rating"], f["Hexed"]["runtime_sec"], f["Hexed"]["ffec_sec"]),
                         ("FLAT", "PG", "6568.208", "5925.000"))
        self.assertEqual(f["Hexed"]["release_date"], "2026-11-25")
        self.assertEqual((f["Sunrise Reaping"]["format"], f["Sunrise Reaping"]["rating"]), ("SCOPE", "PG-13"))
        self.assertEqual(f["The Cat in the Hat"]["format"], "F-200")
        self.assertEqual(f["The Cat in the Hat"]["runtime_sec"], "6809.917")

    def test_feature_with_no_rating_anywhere_alerts(self):
        _, alerts = build()
        self.assertTrue(any("CatInTheHat" in a and "no rating" in a for a in alerts), alerts)

    def test_rating_comes_from_tmdb_when_the_name_has_none(self):
        FILMS["CATINTHEHAT"]["rating"] = "PG"
        try:
            rows, alerts = build()
        finally:
            FILMS["CATINTHEHAT"]["rating"] = ""
        self.assertEqual(only(rows, role="FEATURE", format="F-200")[0]["rating"], "PG")
        self.assertFalse(any("no rating" in a for a in alerts))

    def test_feature_without_ffec_alerts(self):
        def m(inst):
            for n, d in inst.items():
                if n.startswith("Hexed-wshr"):
                    d["ffec"] = {}
        _, alerts = build(m)
        self.assertTrue(any("no FFEC" in a and "Hexed" in a for a in alerts), alerts)

    def test_unknown_film_keeps_a_guessed_title_and_alerts(self):
        FILMS.pop("SUNRISEREAPING")
        try:
            rows, alerts = build()
        finally:
            FILMS["SUNRISEREAPING"] = {"ok": True, "title": "Sunrise Reaping", "release": "2026-10-30", "rating": "PG-13"}
        self.assertEqual(only(rows, role="FEATURE", cpl_name=next(r["cpl_name"] for r in only(rows, role="FEATURE") if "Sunrise" in r["cpl_name"]))[0]["release_date"], "")
        self.assertTrue(any("no TMDB match" in a for a in alerts))


class Trailers(unittest.TestCase):
    def test_two_rows_each_with_clean_title_and_version(self):
        rows, _ = build()
        hexed = only(rows, role="TRAILER", title="Hexed")
        self.assertEqual(sorted(r["format"] for r in hexed), ["FLAT", "SCOPE"])
        self.assertTrue(all(r["version"] == "TLR-2" and r["rating"] == "PG" and r["release_date"] == "2026-11-25" for r in hexed))
        self.assertTrue(all("TLR" not in r["title"] for r in only(rows, role="TRAILER")))

    def test_deleted_trailers_are_left_out_and_held_ones_marked(self):
        flat = next(r["cpl_name"] for r in build()[0] if r["role"] == "TRAILER" and r["title"] == "Drag" and r["format"] == "FLAT")
        scope = flat.replace("_F_", "_S_")
        rows, _ = build(deletes={scope: "Newer"}, kept={flat: ("x", "12/3")})
        drag = only(rows, role="TRAILER", title="Drag")
        self.assertEqual([r["format"] for r in drag], ["FLAT"])
        self.assertIn("running or scheduled", drag[0]["notes"])


class StreamAds(unittest.TestCase):
    def test_sv_week_rows(self):
        rows, _ = build()
        e = only(rows, role="SV_EARLY", cpl_name="Fb-SV early-PG13 20261123")[0]
        self.assertEqual((e["format"], e["rating"], e["start_date"], e["end_date"], e["only_for_title"]),
                         ("SCOPE", "PG-13", "2026-11-23", "2026-11-29", ""))
        self.assertEqual(e["runtime_sec"], "656.417")
        self.assertEqual(len(only(rows, role="SV_EARLY")) + len(only(rows, role="SV_LATE")), 18)

    def test_feature_specific_sv_ad_uses_the_title_and_says_when_the_feature_is_missing(self):
        rows, _ = build()
        g = only(rows, role="SV_LATE", only_for_title="Godzilla Zero")
        self.assertEqual({r["start_date"] for r in g}, {"2026-11-16", "2026-11-23"})
        self.assertTrue(all(r["rating"] == "R" and "not on the servers" in r["notes"] for r in g))

    def test_special_sv_ad_takes_the_exact_feature_title_when_the_feature_is_installed(self):
        FILMS["GODZILLAZERO"] = {"ok": True, "title": "Godzilla Zero", "release": "2026-11-20", "rating": "PG-13"}

        def m(inst):
            inst["GodzillaZero_FTR_S_EN-EN-CCAP_US-PG13_51-HI_4K_WB_20261120_DLX_SMPTE_OV"] = {
                "hosts": {"Projector 1"}, "kind": "feature", "cpls": {"x"}, "dur": {"Projector 1": 6000.0}, "ffec": {"Projector 1": 5600.0}}
        try:
            rows, _ = build(m)
        finally:
            del FILMS["GODZILLAZERO"]
        self.assertTrue(all(r["notes"] == "" for r in only(rows, role="SV_LATE", only_for_title="Godzilla Zero")))

    def test_next_week_sv_alert_waits_until_four_days_before(self):
        self.assertFalse(any("SV ads" in a for a in build()[1]))
        _, alerts = build(when=date(2026, 11, 26))
        msg = next(a for a in alerts if "SV ads" in a)
        self.assertIn("11/30", msg)
        self.assertIn("early PG-13", msg)
        self.assertIn("late R", msg)

    def test_next_week_alert_clears_when_the_pairs_arrive(self):
        def m(inst):
            for rating in ("G", "PG", "PG13", "R"):
                for pos in ("early", "late"):
                    inst["Fb-SV %s-%s 20261130" % (pos, rating)] = {"hosts": {"Projector 1"}, "kind": "Advertisement", "cpls": {pos + rating},
                                                                   "dur": {"Projector 1": 300.0}, "ffec": {}}
        _, alerts = build(m, when=date(2026, 11, 26))
        self.assertFalse(any("SV ads" in a for a in alerts), alerts)


class CokeChevyMarquee(unittest.TestCase):
    def test_coke_gen_dates_from_the_printout_flight_line(self):
        rows, _ = build()
        coke = only(rows, role="COKE")
        self.assertEqual(sorted(r["format"] for r in coke), ["FLAT", "SCOPE"])
        self.assertTrue(all((r["rating"], r["start_date"], r["end_date"], r["only_for_title"]) == ("ALL", "2026-10-30", "2026-12-03", "")
                            for r in coke))

    def test_coke_without_dates_is_left_out_with_an_alert(self):
        rows, alerts = build(printout=PRINTOUT[1:])
        self.assertEqual(only(rows, role="COKE"), [])
        self.assertTrue(any("Coke flight 11" in a for a in alerts))

    def test_gpg_flight_needs_titles_from_the_printout(self):
        def m(inst):
            for fmt in "SF":
                inst["KO60-FLT12-GPG_ADV_%s_EN-XX_US_51_2K_KO_20261201_CC_IOP_OV" % fmt] = {
                    "hosts": {"Projector 1"}, "kind": "advertisement", "cpls": {"g" + fmt}, "dur": {"Projector 1": 60.917}, "ffec": {}}
        p = PRINTOUT + [["", "", "", ""]]
        p[0] = ["2026 Coke Flight 11", "10/30 - 12/3", "", ""]
        p = [["Coke Flight 12", "12/4 - 1/7", "", ""]] + p
        rows, alerts = build(m, printout=p)
        self.assertTrue(any("GPG TITLES" in a for a in alerts))
        self.assertEqual(only(rows, role="COKE", notes="GPG flight 12"), [])
        rows, alerts = build(m, printout=p + [["GPG TITLES: Hexed, The Cat in the Hat", "", "", ""]])
        gpg = only(rows, role="COKE", notes="GPG flight 12")
        self.assertEqual(sorted({r["only_for_title"] for r in gpg}), ["Hexed", "The Cat in the Hat"])
        self.assertTrue(all(r["rating"] == "G,PG" and r["end_date"] == "2027-01-07" for r in gpg))
        self.assertFalse(any("active at the same time" in a for a in alerts), alerts)

    def test_chevy_pairs_and_exact_dates_split_on_11_25_and_11_26(self):
        rows, alerts = build()
        ch = {r["cpl_name"]: r for r in only(rows, role="CHEVY")}
        self.assertEqual(len(ch), 4)
        old = [r for n, r in ch.items() if ".0726." in n]
        new = [r for n, r in ch.items() if ".1126." in n]
        self.assertEqual({(r["start_date"], r["end_date"]) for r in old}, {("2026-11-20", "2026-11-25")})
        self.assertEqual({(r["start_date"], r["end_date"]) for r in new}, {("2026-11-26", "2026-12-31")})
        self.assertEqual(sorted(r["format"] for r in new), ["FLAT", "SCOPE"])
        self.assertFalse(any("active at the same time" in a for a in alerts), alerts)

    def test_chevy_without_dates_is_left_out(self):
        rows, alerts = build(printout=[r for r in PRINTOUT if "Chevy" not in r[0]])
        self.assertEqual(only(rows, role="CHEVY"), [])
        self.assertEqual(len([a for a in alerts if "Chevy" in a]), 4)

    def test_chevy_dates_from_a_campaign_email_beat_the_printout_and_a_disagreement_alerts(self):
        S = ta.new_state()
        S["specials"]["k"] = {"ids": ["TRL.GMC.1126.477", "TRL.GMC.1126.478"], "brand": "GMC", "placement": "Trailer",
                              "start": "2026-11-27", "end": "2026-12-31", "received": "2026-11-20", "alerts": [], "exclude": [], "length": 60}
        rows, alerts = build(state=S)
        new = [r for r in only(rows, role="CHEVY") if ".1126." in r["cpl_name"]]
        self.assertEqual({r["start_date"] for r in new}, {"2026-11-27"})
        self.assertTrue(any("Printout says" in a for a in alerts))

    def test_preshow_and_rating_cards(self):
        rows, _ = build()
        self.assertEqual(sorted((r["format"], r["runtime_sec"]) for r in only(rows, role="PRESHOW")),
                         [("FLAT", "31.958"), ("SCOPE", "31.958")])
        cards = only(rows, role="RATING_CARD")
        self.assertEqual(len(cards), 8)
        self.assertEqual({r["rating"] for r in cards}, {"G", "PG", "PG-13", "R"})
        self.assertTrue(all(r["runtime_sec"] == "5.042" for r in cards))

    def test_marquee_found_by_brand_name_from_the_printout_row(self):
        rows, _ = build()
        mq = only(rows, role="MARQUEE")
        self.assertEqual(sorted(r["format"] for r in mq), ["FLAT", "SCOPE"])
        self.assertTrue(all((r["rating"], r["start_date"], r["end_date"]) == ("G,PG,PG-13", "2026-11-17", "2026-11-22") for r in mq))
        self.assertTrue(all("brand name" in r["notes"] for r in mq))

    def test_geico_from_the_email_skips_josephine_and_counts_120_seconds(self):
        S = ta.new_state()
        ta.TODAY_OVERRIDE = MONDAY
        try:
            c = ta.parse_campaign("Geico MARQUEE Campaign", "Starting THURSDAY, November 26th, GEICO will be taking over the MARQUEE "
                                  "position and will run up to and including Sunday, December 27th\nThe spot is :120 seconds & is to run "
                                  "on ALL FEATURES EXCEPT JOSEPHINE\nMAR.GEI.1126.532\nMAR.GEI.1126.533\n", MONDAY, "")
            ta.apply_campaign(S, c, "Geico MARQUEE Campaign", MONDAY, [])
        finally:
            ta.TODAY_OVERRIDE = None
        for a in S["ads"].values():
            a.update(status="done", cpls=[{"id": "ggg", "title": "MAR.GEI.1126.532_geico26hol_flat"}])

        def m(inst):
            inst["MAR.GEI.1126.532_geico26hol_flat"] = {"hosts": {"Projector 1", "Projector 2"}, "kind": "Advertisement",
                                                        "cpls": {"ggg"}, "dur": {"Projector 1": 120.083, "Projector 2": 120.083}, "ffec": {}}
        rows, alerts = build(m, state=S)
        g = only(rows, role="MARQUEE", cpl_name="MAR.GEI.1126.532_geico26hol_flat")[0]
        self.assertEqual((g["format"], g["rating"], g["start_date"], g["end_date"], g["runtime_sec"], g["except_titles"]),
                         ("FLAT", "ALL", "2026-11-26", "2026-12-27", "120.083", "Josephine"))
        self.assertFalse(any("active at the same time" in a for a in alerts), alerts)

    def test_two_marquee_ads_on_the_same_days_alert(self):
        def m(inst):
            inst["google-mb-marquee-flat2"] = {"hosts": {"Projector 1"}, "kind": "Advertisement", "cpls": {"q"}, "dur": {"Projector 1": 60.0}, "ffec": {}}
        S = ta.new_state()
        S["specials"]["k"] = {"ids": ["MAR.GOO.1126.555"], "brand": "GOO", "placement": "Late Marquee", "start": "2026-11-20",
                              "end": "2026-11-27", "received": "2026-11-12", "alerts": [], "exclude": [], "length": 60}
        _, alerts = build(m, state=S)
        self.assertTrue(any("two MARQUEE FLAT ads are active" in a for a in alerts), alerts)


class RunLengths(unittest.TestCase):
    def test_a_projector_mismatch_alerts_and_uses_projector_1(self):
        def m(inst):
            inst["Hexed-wshr_FTR-2D_F_EN-EN-CCAP_US-PG_51-HI-VI_2K_WDAS_20261029_WDS_SMPTE_OV"]["dur"]["Projector 2"] = 6570.0
        rows, alerts = build(m)
        self.assertEqual(only(rows, role="FEATURE", title="Hexed")[0]["runtime_sec"], "6568.208")
        self.assertTrue(any("differs between the projectors" in a for a in alerts))

    def test_missing_run_length_is_blank_and_alerts(self):
        def m(inst):
            inst["PG_RTG_F_EN-XX_2K_20251001"]["dur"] = {}
        rows, alerts = build(m)
        self.assertEqual(only(rows, role="RATING_CARD", cpl_name="PG_RTG_F_EN-XX_2K_20251001")[0]["runtime_sec"], "")
        self.assertTrue(any("no run length for PG_RTG_F" in a for a in alerts))

    def test_nothing_unrelated_is_written(self):
        rows, _ = build()
        names = {r["cpl_name"] for r in rows}
        self.assertFalse(any(n.startswith(("FeaturePresentation", "Black MOS", "RealD", "GDC_LOGO")) for n in names))

    def test_build_plan_carries_run_lengths_from_the_scan(self):
        by_host = {}
        with open(os.path.join(HERE, "fixtures", "installed_dump_sample.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_host.setdefault(r["projector"], []).append({"name": r["name"], "kind": r["kind"], "issue": r["issue"],
                                                                "cpl": r["cpl_id"], "folder": r["folder"],
                                                                "dur": float(r["dur_sec"]) if r["dur_sec"] else None,
                                                                "ffec": float(r["ffec_sec"]) if r["ffec_sec"] else None})
        ta.TODAY_OVERRIDE = MONDAY
        try:
            plan = ta.build_plan(ta.new_state(), ta.Rules([]), by_host)
        finally:
            ta.TODAY_OVERRIDE = None
        d = plan["inst"]["Hexed-wshr_FTR-2D_F_EN-EN-CCAP_US-PG_51-HI-VI_2K_WDAS_20261029_WDS_SMPTE_OV"]
        self.assertEqual((d["dur"]["Projector 1"], d["dur"]["Projector 2"], d["ffec"]["Projector 1"]), (6568.208, 6568.208, 5925.0))


class WholeRun(unittest.TestCase):
    """run_cycle end to end with the projectors, the sheet and TMDB faked out."""

    def test_a_run_pushes_the_playlist_data_tab_and_a_second_run_is_unchanged(self):
        import tempfile
        pushes = []
        by_host = {}
        with open(os.path.join(HERE, "fixtures", "installed_dump_sample.csv"), newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_host.setdefault(r["projector"], []).append({"name": r["name"], "kind": r["kind"], "issue": r["issue"], "cpl": r["cpl_id"],
                                                                "folder": r["folder"], "dur": float(r["dur_sec"]) if r["dur_sec"] else None,
                                                                "ffec": float(r["ffec_sec"]) if r["ffec_sec"] else None})

        def fake_sheet(action, extra=None):
            if action == "printout":
                return {"ok": True, "lines": PRINTOUT}
            if action == "rules":
                return {"ok": True, "rules": []}
            if action == "push":
                pushes.append(extra)
                return {"ok": True, "summary": "ok"}
            return {"ok": True}

        tmp = tempfile.mkdtemp()
        saved = {k: getattr(ta, k) for k in ("DATA", "STATE_FILE", "LOG_FILE", "ADS_ENABLED", "SHEET_URL", "SHEET_TOKEN", "sheet_call",
                                              "ftp_scan_host", "resolve_film", "TODAY_OVERRIDE", "BULL_DIR")}
        try:
            ta.DATA, ta.STATE_FILE, ta.LOG_FILE = tmp, os.path.join(tmp, "s.json"), os.path.join(tmp, "l.log")
            ta.BULL_DIR = os.path.join(tmp, "bulletins")
            ta.ADS_ENABLED, ta.SHEET_URL, ta.SHEET_TOKEN = False, "http://x", "t"
            ta.sheet_call = fake_sheet
            ta.ftp_scan_host = lambda label, host, hs: by_host[label]
            ta.resolve_film = lambda S, rules, slug, hint: dict(lookup(slug, hint), id=slug.upper(), src="fake")
            ta.TODAY_OVERRIDE = MONDAY
            ta.run_cycle(dry=False)
            ta.run_cycle(dry=False)
        finally:
            for k, v in saved.items():
                setattr(ta, k, v)
        self.assertEqual(len(pushes), 1, "the second run changes nothing so it does not push again")
        tab = [t for t in pushes[0]["tabs"] if t["name"] == "Playlist Data"][0]
        self.assertEqual(tab["headers"], ta.PD_HEADER)
        self.assertTrue(tab["plain"])
        self.assertGreater(len(tab["rows"]), 40)
        self.assertEqual({r[0] for r in tab["rows"]}, {"FEATURE", "TRAILER", "SV_EARLY", "SV_LATE", "COKE", "CHEVY", "MARQUEE", "PRESHOW", "RATING_CARD"})


class BuilderCrash(unittest.TestCase):
    def test_a_bug_in_the_builder_never_stops_the_run(self):
        import tempfile
        by_host = {"Projector 1": [], "Projector 2": []}
        tmp = tempfile.mkdtemp()
        saved = {k: getattr(ta, k) for k in ("DATA", "STATE_FILE", "LOG_FILE", "ADS_ENABLED", "SHEET_URL", "SHEET_TOKEN", "sheet_call",
                                              "ftp_scan_host", "playlist_data", "TODAY_OVERRIDE", "BULL_DIR")}
        pushed = []

        def fake_sheet(action, extra=None):
            if action == "push":
                pushed.append(extra)
            return {"ok": True, "lines": [], "rules": []}

        def boom(*a, **k):
            raise ValueError("boom")
        try:
            ta.DATA, ta.STATE_FILE, ta.LOG_FILE = tmp, os.path.join(tmp, "s.json"), os.path.join(tmp, "l.log")
            ta.BULL_DIR = os.path.join(tmp, "b")
            ta.ADS_ENABLED, ta.SHEET_URL, ta.SHEET_TOKEN = False, "http://x", "t"
            ta.sheet_call, ta.ftp_scan_host, ta.playlist_data = fake_sheet, (lambda l, h, hs: by_host[l]), boom
            ta.TODAY_OVERRIDE = MONDAY
            _, plan, _ = ta.run_cycle(dry=False)
        finally:
            for k, v in saved.items():
                setattr(ta, k, v)
        self.assertEqual(len(pushed), 1)
        self.assertNotIn("Playlist Data", [t["name"] for t in pushed[0]["tabs"]])
        self.assertTrue(any("could not be built" in a for a in plan["pd_alerts"]))


class PrintoutParsing(unittest.TestCase):
    def test_block_from_the_real_printout(self):
        p = ta.parse_printout(PRINTOUT, MONDAY)
        self.assertEqual(p["coke"], {11: ("2026-10-30", "2026-12-03")})
        self.assertEqual(p["gpg_titles"], [])
        self.assertEqual([(s["brand"], sorted(s["nums"]), s["start"], s["end"]) for s in p["specials"]],
                         [("Chevy", ["146", "147"], "2026-11-20", "2026-11-25"), ("Chevy", ["477", "478"], "2026-11-26", "2026-12-31"),
                          ("Google", ["339", "347"], "2026-11-17", "2026-11-22")])

    def test_year_follows_the_calendar_around_new_year(self):
        self.assertEqual(ta.parse_month_day_range("12/28 - 1/3", date(2026, 12, 30)), ("2026-12-28", "2027-01-03"))
        self.assertEqual(ta.parse_month_day_range("1/2 - 1/10", date(2026, 12, 30)), ("2027-01-02", "2027-01-10"))
        self.assertIsNone(ta.parse_month_day_range("no dates here", MONDAY))


if __name__ == "__main__":
    unittest.main()
