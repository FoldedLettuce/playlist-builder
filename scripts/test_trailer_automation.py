"""Tests for the pure parts of trailer_automation.py. Run:  python -m unittest discover -s scripts -p "test_*.py" """
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ta", os.path.join(HERE, "trailer_automation.py"))
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)

NS = "http://www.smpte-ra.org/schemas/429-7/2006/CPL"


def cpl(reels, title="Hexed_FTR-1_F_EN-XX_US-13_51_4K_XX_20261101_SMPTE_OV"):
    body = ""
    for r in reels:
        marks = ""
        if r.get("markers"):
            marks = "<MainMarkers><EditRate>24 1</EditRate><EntryPoint>0</EntryPoint><MarkerList>%s</MarkerList></MainMarkers>" % "".join(
                "<Marker><Label>%s</Label><Offset>%d</Offset></Marker>" % (l, o) for l, o in r["markers"])
        body += ("<Reel><AssetList><MainPicture><EditRate>24 1</EditRate><IntrinsicDuration>%d</IntrinsicDuration>"
                 "<EntryPoint>0</EntryPoint><Duration>%d</Duration></MainPicture>%s</AssetList></Reel>") % (r["frames"], r["frames"], marks)
    return ('<CompositionPlaylist xmlns="%s"><Id>urn:uuid:abc</Id><IssueDate>2026-11-01T00:00:00Z</IssueDate>'
            "<ContentTitleText>%s</ContentTitleText><ContentKind>feature</ContentKind><ReelList>%s</ReelList>"
            "</CompositionPlaylist>") % (NS, title, body)


class CplTiming(unittest.TestCase):
    def test_single_reel_with_ffec(self):
        r = ta.parse_cpl(cpl([{"frames": 24 * 6000, "markers": [("FFOC", 24), ("FFEC", 24 * 5800)]}]).encode())
        self.assertEqual(r["dur"], 6000.0)
        self.assertEqual(r["ffec"], 5800.0)
        self.assertEqual(r["title"], "Hexed_FTR-1_F_EN-XX_US-13_51_4K_XX_20261101_SMPTE_OV")

    def test_ffec_in_second_reel_counts_the_first(self):
        r = ta.parse_cpl(cpl([{"frames": 24 * 3000}, {"frames": 24 * 3000, "markers": [("FFEC", 24 * 2800)]}]).encode())
        self.assertEqual(r["dur"], 6000.0)
        self.assertEqual(r["ffec"], 5800.0)

    def test_no_markers_gives_no_ffec_and_odd_edit_rate(self):
        x = cpl([{"frames": 2398}]).replace("24 1", "24000 1001")
        r = ta.parse_cpl(x.encode())
        self.assertAlmostEqual(r["dur"], 2398 / (24000 / 1001), places=3)
        self.assertIsNone(r["ffec"])

    def test_not_a_cpl(self):
        self.assertIsNone(ta.parse_cpl(b"<PackingList/>"))


class KeepRule(unittest.TestCase):
    def plan(self):
        return {"deletes": {"Ice_TLR-1": "Superseded by TLR-2", "Old_TLR-1": "28 days past release", "Ad 20261116": "Older weekly ad"},
                "managed": {"Ice_TLR-1": {}, "Old_TLR-1": {}}}

    def test_in_use_trailer_is_held_back(self):
        p = self.plan()
        kept = ta.protect_deletes(p, {"Ice_TLR-1": "2026-12-03"}, "")
        self.assertEqual(sorted(kept), ["Ice_TLR-1"])
        self.assertEqual(kept["Ice_TLR-1"][0], "Superseded by TLR-2")
        self.assertIn("12/3", kept["Ice_TLR-1"][1])
        self.assertEqual(sorted(p["deletes"]), ["Ad 20261116", "Old_TLR-1"])

    def test_ads_are_never_held(self):
        p = self.plan()
        ta.protect_deletes(p, {"Ad 20261116": "2026-12-03"}, "")
        self.assertIn("Ad 20261116", p["deletes"])

    def test_app_unreachable_holds_every_trailer_delete(self):
        p = self.plan()
        kept = ta.protect_deletes(p, {}, "could not reach the playlist app")
        self.assertEqual(sorted(kept), ["Ice_TLR-1", "Old_TLR-1"])
        self.assertEqual(list(p["deletes"]), ["Ad 20261116"])

    def test_not_set_up_changes_nothing(self):
        p = self.plan()
        self.assertEqual(ta.protect_deletes(p, None, ""), {})
        self.assertEqual(len(p["deletes"]), 3)


class SpecialAdEmail(unittest.TestCase):
    TEXT = ("Hello,\n\nStarting THURSDAY, November 26th, GEICO will be taking over the MARQUEE\n"
            "position and will run up to and including Sunday, December 27th\n\n"
            "The spot is :120 seconds & is to run on ALL FEATURES EXCEPT JOSEPHINE\n\n"
            "Geico Holiday Special Rev 120 Flat\nMAR.GEI.1126.532\nGeico Holiday Special Rev 120 Scope\nMAR.GEI.1126.533\n")
    HTML = ('<p>Flat:</p><a href="https://www.dropbox.com/scl/fo/aaa/BBB?rlkey=k1&amp;st=s1&amp;dl=0" title="x">'
            '<span>MAR.GEI.1126.532_geico26hol_flat</span></a><p>Scope:</p>'
            '<a href="https://www.dropbox.com/scl/fo/ccc/DDD?rlkey=k2&amp;st=s2&amp;dl=0"><span>MAR.GEI.1126.533_geico26hol_scope</span></a>')
    SUBJ = "Geico MARQUEE Campaign - Starting THURSDAY, 11/26"

    def parse(self):
        from datetime import date
        return ta.parse_campaign(self.SUBJ, self.TEXT, date(2026, 11, 23), self.HTML)

    def test_dates_brand_length_and_exclusion(self):
        c = self.parse()
        self.assertEqual((c["start"], c["end"]), ("2026-11-26", "2026-12-27"))
        self.assertEqual(c["name"], "Geico")
        self.assertEqual(c["code"], "GEI")
        self.assertEqual(c["length"], 120)
        self.assertEqual(c["exclude"], ["Josephine"])
        self.assertEqual(c["ratings"], "All Ratings")
        self.assertEqual(c["placement"], "Late Marquee")
        self.assertEqual(c["ids"], ["MAR.GEI.1126.532", "MAR.GEI.1126.533"])

    def test_dropbox_links_unescaped_and_forced_to_download(self):
        c = self.parse()
        self.assertIn("&st=s1&", c["flat_url"])
        self.assertTrue(c["scope_url"].endswith("dl=0"))
        d = ta.dropbox_direct(c["flat_url"])
        self.assertTrue(d.endswith("dl=1"))
        self.assertEqual(d.count("dl="), 1)
        self.assertIn("rlkey=k1", d)

    def test_year_rolls_over(self):
        from datetime import date
        c = ta.parse_campaign("X MARQUEE Campaign", "Starting Monday, December 28th, X will be taking over the MARQUEE "
                              "and will run up to and including Sunday, January 10th MAR.XXX.1228.1", date(2026, 12, 20), "")
        self.assertEqual((c["start"], c["end"]), ("2026-12-28", "2027-01-10"))

    def test_apply_queues_two_downloads_and_one_special(self):
        from datetime import date
        S = ta.new_state()
        ta.TODAY_OVERRIDE = date(2026, 11, 23)
        try:
            rep = []
            ta.apply_campaign(S, self.parse(), self.SUBJ, date(2026, 11, 23), rep)
            ta.apply_campaign(S, self.parse(), self.SUBJ, date(2026, 11, 23), rep)       # the same email twice
            self.assertEqual(len(S["specials"]), 1)
            self.assertEqual(len(S["ads"]), 2)
            self.assertTrue(all(u.endswith("dl=1") and a["status"] == "pending" for u, a in S["ads"].items()))
            row = ta.special_rows(S)[0]
            self.assertEqual(row["dates"], "11/26 - 12/27")
            self.assertIn("not Josephine", row["ratings"])
        finally:
            ta.TODAY_OVERRIDE = None

    def test_cielo_hbr_emails_are_ignored_but_weekly_ones_are_not(self):
        self.assertIsNone(ta.classify_message("a@cielocinema.com", "HBR Campaign Ready for Download"))
        self.assertEqual(ta.classify_message("a@cielocinema.com", "Ready for Download: sv weekly"), "cielo_ad")
        self.assertEqual(ta.classify_message("a@cinemaproductionsinc.com", "Geico MARQUEE Campaign"), "campaign")


if __name__ == "__main__":
    unittest.main()
