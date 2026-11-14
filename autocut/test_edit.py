import unittest

from edit import build_export_filter, clean_ranges, parse_scene_log


class EditTests(unittest.TestCase):
    def test_ranges_drop_overlaps_and_tiny_pieces(self):
        ranges = clean_ranges(
            [
                {"start": -1, "end": 1.2},
                {"start": 1.0, "end": 2.4},
                {"start": 3, "end": 3.02},
                {"start": 4, "end": 5},
            ],
            8,
        )
        self.assertEqual(ranges[0]["start"], 0)
        self.assertEqual(ranges[0]["end"], 1.2)
        self.assertEqual(ranges[1]["start"], 1.2)
        self.assertEqual(ranges[-1], {"start": 4, "end": 5})

    def test_ranges_reject_empty(self):
        with self.assertRaises(ValueError):
            clean_ranges([{"start": 0, "end": 0.01}], 4)

    def test_scene_log_pairs_time_and_score(self):
        log = "\n".join(
            [
                "[meta] frame:0 pts:10 pts_time:5.5",
                "[meta] lavfi.scene_score=0.81234",
                "pts_time:9.25 lavfi.scene_score=0.2",
            ]
        )
        scenes = parse_scene_log(log)
        self.assertEqual(scenes, [{"t": 5.5, "score": 0.8123}, {"t": 9.25, "score": 0.2}])

    def test_filter_graph_stays_a_plain_filter(self):
        graph, video, audio = build_export_filter(
            [{"start": 0.5, "end": 2.25}, {"start": 4, "end": 5.5}],
            fps="30000/1001",
            has_audio=True,
            aspect="9:16",
            normalize=True,
            rotation=-90,
        )
        self.assertEqual(video, "[vout]")
        self.assertEqual(audio, "[aout]")
        self.assertIn("crop=720:1280", graph)
        self.assertIn("transpose=2", graph)
        self.assertIn("trim=start=0.500:end=2.250", graph)
        self.assertNotIn(" ", graph)
        self.assertNotRegex(graph, r"[^0-9A-Za-z_\[\]=:,.\-+/;]")

    def test_filter_rejects_a_smuggled_fps(self):
        graph, _, _ = build_export_filter(
            [{"start": 0, "end": 1}],
            fps="30;id",
            has_audio=False,
            aspect="original",
            normalize=False,
            rotation=0,
        )
        self.assertIn("fps=30", graph)
        self.assertNotIn("id", graph)


if __name__ == "__main__":
    unittest.main()
