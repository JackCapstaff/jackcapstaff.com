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

    def test_calculator_inputs_round_trip(self):
        section = _clean_section({"calcStart": "0:25.5", "calcEnd": "1:03"})
        self.assertEqual(section["calcStart"], "0:25.5")
        self.assertEqual(section["calcEnd"], "1:03")
        self.assertEqual(_clean_section(section), section)


class ClickTrackStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flask import Flask
        from flask_login import LoginManager
        from flask_sqlalchemy import SQLAlchemy
        from clicktrack import clicktrack_bp
        from models import init_models

        cls.app = Flask(__name__)
        cls.app.config.update(TESTING=True, SECRET_KEY="local-test", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        cls.db = SQLAlchemy(cls.app)
        cls.models = init_models(cls.db)
        cls.app.db = cls.db
        cls.app.ClickTrack = cls.models["ClickTrack"]
        login = LoginManager(cls.app)

        @login.user_loader
        def load_user(user_id):
            return cls.db.session.get(cls.models["User"], int(user_id))

        cls.app.register_blueprint(clicktrack_bp)

    def setUp(self):
        with self.app.app_context():
            self.db.create_all()
            user = self.models["User"](username="test", email="test@example.invalid", password_hash="unused")
            self.db.session.add(user)
            self.db.session.commit()
            user_id = user.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        self.payload = {
            "title": "Snowman timing test", "notes": "Do not replace",
            "sections": [{"name": "Opening", "bpm": 120, "num": 6, "den": 8, "bars": 8,
                          "calcStart": "0:25", "calcEnd": "0:45", "notes": "Watch cue"}],
        }

    def tearDown(self):
        with self.app.app_context():
            self.db.session.remove()
            self.db.drop_all()

    def test_offset_and_calculator_persist(self):
        response = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoOffset": 25.5})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["videoOffset"], 25.5)
        saved = self.client.get("/clicktrack/api/tracks").json["tracks"][0]
        self.assertEqual(saved["videoOffset"], 25.5)
        self.assertEqual(saved["sections"][0]["calcEnd"], "0:45")

    def test_legacy_update_preserves_offset_and_existing_notes(self):
        saved = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoOffset": 25}).json
        response = self.client.put("/clicktrack/api/tracks/" + str(saved["id"]), json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["videoOffset"], 25)
        self.assertEqual(response.json["notes"], self.payload["notes"])
        self.assertEqual(response.json["sections"][0]["notes"], "Watch cue")

    def test_legacy_track_defaults_to_zero_offset(self):
        response = self.client.post("/clicktrack/api/tracks", json=self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["videoOffset"], 0)

    def test_bad_offset_does_not_overwrite_track(self):
        saved = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoOffset": 25}).json
        for value in [-1, 86401, "0:25", True, None, float("inf"), float("nan")]:
            with self.subTest(value=value):
                response = self.client.put("/clicktrack/api/tracks/" + str(saved["id"]),
                                           json={**self.payload, "videoOffset": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.get("/clicktrack/api/tracks").json["tracks"][0]["videoOffset"], 25)


if __name__ == "__main__":
    unittest.main()
