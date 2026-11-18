import unittest

from clicktrack import _clean_section


class ClickTrackSectionTests(unittest.TestCase):
    def test_existing_sections_receive_sound_defaults(self):
        section = _clean_section({"num": 6, "den": 8, "click": "beats"})
        self.assertEqual(section["click"], "beats")
        self.assertEqual(
            [section[key] for key in ("unitPitch", "unitVelocity", "subPitch", "subVelocity")],
            [0, 53, 0, 32],
        )

    def test_sound_settings_and_multiline_notes_round_trip(self):
        section = _clean_section({
            "num": 6, "den": 8, "click": "units",
            "unitPitch": -12, "unitVelocity": 75,
            "subPitch": 7, "subVelocity": 0,
            "notes": "Watch the screen\nWait for the narration",
        })
        self.assertEqual(_clean_section(section), section)
        self.assertEqual(section["unitPitch"], -12)
        self.assertEqual(section["unitVelocity"], 75)
        self.assertEqual(section["subPitch"], 7)
        self.assertEqual(section["subVelocity"], 0)
        self.assertIn("\n", section["notes"])

    def test_sound_settings_are_clamped(self):
        section = _clean_section({
            "unitPitch": -100, "unitVelocity": 200,
            "subPitch": 100, "subVelocity": -1,
        })
        self.assertEqual(
            [section[key] for key in ("unitPitch", "unitVelocity", "subPitch", "subVelocity")],
            [-24, 127, 24, 0],
        )

    def test_invalid_sound_settings_use_defaults(self):
        section = _clean_section({
            "unitPitch": "invalid", "unitVelocity": None,
            "subPitch": float("inf"), "subVelocity": "invalid",
        })
        self.assertEqual(
            [section[key] for key in ("unitPitch", "unitVelocity", "subPitch", "subVelocity")],
            [0, 53, 0, 32],
        )
        self.assertEqual(_clean_section({"unitVelocity": float("inf")})["unitVelocity"], 53)


if __name__ == "__main__":
    unittest.main()
