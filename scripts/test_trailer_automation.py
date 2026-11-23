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


if __name__ == "__main__":
    unittest.main()
