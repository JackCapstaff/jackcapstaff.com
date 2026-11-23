import unittest
from pathlib import Path

from clicktrack import _clean_section


class ClickTrackSectionTests(unittest.TestCase):
    def test_auto_and_explicit_count_ins_round_trip(self):
        for value in (None, 0, 1, 8):
            with self.subTest(count_in=value):
                section = _clean_section({"countIn": value})
                self.assertEqual(section["countIn"], value)
                self.assertEqual(_clean_section(section)["countIn"], value)
        self.assertIsNone(_clean_section({})["countIn"])

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

        root = Path(__file__).resolve().parents[1]
        cls.app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "assets"))
        cls.app.config.update(TESTING=True, SECRET_KEY="local-test", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        cls.db = SQLAlchemy(cls.app)
        cls.models = init_models(cls.db)
        cls.app.db = cls.db
        cls.app.ClickTrack = cls.models["ClickTrack"]
        cls.app.ClickTrackVideoDraft = cls.models["ClickTrackVideoDraft"]
        login = LoginManager(cls.app)

        cls.app.add_url_rule("/", endpoint="index", view_func=lambda: "")

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

    def test_offline_shell_is_self_contained_and_never_contains_account_data(self):
        self.client.post("/clicktrack/api/tracks", json=self.payload)
        for client in (self.client, self.app.test_client()):
            with self.subTest(signed_in=client is self.client):
                response = client.get("/clicktrack/offline/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Clicktrack-Offline-Shell"], "1")
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn(b"offline: true", response.data)
                self.assertIn(b"authenticated: false", response.data)
                self.assertIn(b"<style>", response.data)
                self.assertNotIn(b"<script src=", response.data)
                self.assertNotIn(b'id="view-video"', response.data)
                self.assertNotIn(b'id="practice-video"', response.data)
                self.assertNotIn(b'id="practice-enable"', response.data)
                self.assertIn(b'id="audio-output"', response.data)
                self.assertIn(b'id="rehearsal-panel"', response.data)
                self.assertIn(b'id="rehearsal-dialog"', response.data)
                self.assertIn(b'id="rehearsal-play"', response.data)
                self.assertIn(b'id="pref-beat-visual"', response.data)
                self.assertIn(b'id="d-pendulum"', response.data)
                self.assertNotIn(self.payload["title"].encode(), response.data)
        guest = self.app.test_client()
        self.assertEqual(guest.get("/clicktrack/api/tracks").status_code, 401)
        with guest.get("/clicktrack/offline/worker.js") as worker:
            self.assertEqual(worker.status_code, 200)
            self.assertIn("javascript", worker.mimetype)

    def test_video_builder_is_separate_from_existing_editor(self):
        response = self.client.get("/clicktrack/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="btn-video-track"', response.data)
        self.assertIn(b'id="view-video"', response.data)
        self.assertIn(b'id="view-edit"', response.data)
        self.assertIn(b'id="builder-video"', response.data)
        transport = response.data.split(b'id="builder-transport"', 1)[1].split(b"</fieldset>", 1)[0]
        self.assertIn(b'id="builder-play" aria-keyshortcuts="Enter"', transport)
        self.assertIn(b'id="builder-add" aria-keyshortcuts="Space"', transport)
        self.assertNotIn(b'id="builder-resume"', response.data)
        self.assertIn(b'id="builder-timeline-rail"', response.data)
        self.assertIn(b'id="builder-nudge-forward"', response.data)
        self.assertIn(b'id="builder-review" checked', response.data)
        self.assertIn(b'id="builder-retime"', response.data)
        self.assertIn(b'id="btn-edit-video"', response.data)
        self.assertIn(b'id="builder-target-track"', response.data)
        self.assertIn(b'id="builder-overwrite"', response.data)
        self.assertIn(b'id="track-count-in"', response.data)
        self.assertIn(b'id="builder-count-in"', response.data)
        self.assertIn(b'id="builder-preview"', response.data)

    def test_practice_video_is_separate_from_pre_show_audio_test(self):
        response = self.client.get("/clicktrack/")
        self.assertIn(b'id="practice-video"', response.data)
        self.assertIn(b'type="checkbox" id="practice-enable" aria-controls="practice-video"', response.data)
        self.assertIn(b'<section id="practice-video" class="practice-video" aria-label="Practice video" hidden>', response.data)
        self.assertIn(b'id="btn-practice-edit-video"', response.data)
        self.assertIn(b'id="practice-controls" hidden', response.data)
        self.assertIn(b'id="practice-resync"', response.data)
        self.assertIn(b'id="practice-from-start" disabled', response.data)
        self.assertIn(b'id="practice-show-cues"', response.data)
        self.assertIn(b'<option value="pendulum">Swinging metronome</option>', response.data)
        self.assertIn(b'<option value="both">Both</option>', response.data)
        self.assertIn(b'id="builder-visual-cue"', response.data)
        self.assertIn(b'id="btn-test-clicks"', response.data)

    def test_rehearsal_count_in_available_online_and_offline(self):
        for path in ("/clicktrack/", "/clicktrack/offline/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                options = response.data.split(b'id="rehearsal-count-in"', 1)[1].split(b"</select>", 1)[0]
                self.assertIn(b'<option value="">Use section setting</option>', options)
                self.assertIn(b'<option value="0">No count-in</option>', options)
                self.assertIn(b'<option value="1">1 bar</option>', options)
                self.assertIn(b'<option value="8">8 bars</option>', options)
                self.assertIn(b'id="rehearsal-list"', response.data)
                rehearsal = response.data.split(b'id="rehearsal-count-in"', 1)[1].split(b"</select>", 1)[0]
                self.assertIn(b'<option value="0">No count-in</option>', rehearsal)
                self.assertIn(b'<option value="8">8 bars</option>', rehearsal)

    def test_quick_editor_is_available_online_but_hidden_offline(self):
        online = self.client.get("/clicktrack/")
        offline = self.client.get("/clicktrack/offline/")
        self.assertIn(b'id="btn-quick-edit">', online.data)
        self.assertIn(b'id="btn-quick-edit" hidden>', offline.data)
        for response in (online, offline):
            self.assertIn(b'id="quick-edit-dialog"', response.data)
            self.assertIn(b'id="quick-edit-section"', response.data)
            self.assertIn(b'id="quick-edit-video"', response.data)

    def test_track_actions_group_creation_json_and_pedal_setup(self):
        response = self.client.get("/clicktrack/")
        menu = response.data.split(b'id="track-actions">', 1)[1].split(b"</details>", 1)[0]
        for action in (b"btn-new-track", b"btn-video-track", b"btn-import-track",
                       b"btn-export-track", b"btn-pedal-setup"):
            self.assertIn(b'id="' + action + b'"', menu)
        self.assertNotIn(b'id="btn-edit-video"', menu)

    def test_track_video_link_persists_without_changing_sections(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post(path, json={"videoId": "abcdefghijk", "markers": [marker]}).json
        url = "/clicktrack/api/tracks"
        track = self.client.post(url, json={**self.payload, "videoDraftId": draft["id"]}).json
        self.assertEqual(track["videoDraftId"], draft["id"])
        self.assertEqual(self.client.put(f"{url}/{track['id']}", json=self.payload).json["videoDraftId"], draft["id"])
        self.assertEqual(self.client.delete(f"{path}/{draft['id']}").status_code, 200)
        reloaded = self.client.get(url).json["tracks"][0]
        self.assertIsNone(reloaded["videoDraftId"])
        self.assertEqual(reloaded["sections"], track["sections"])

    def test_youtube_draft_round_trip_across_sessions(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "bar": "", "num": "4", "den": "4", "unit": "auto",
                  "shape": "steady", "bpm": "", "dialogue": "", "figure": "", "visualCue": False}
        payload = {"title": "Snowman cues", "videoId": "abcdefghijk",
                   "markers": [marker, {**marker, "time": 25.123, "bar": "1", "dialogue": "First cue",
                                         "figure": "A", "visualCue": True}]}
        saved = self.client.post(path, json=payload)
        self.assertEqual(saved.status_code, 201)
        draft_id = saved.json["id"]
        with self.client.session_transaction() as session:
            owner_id = session["_user_id"]
        other_device = self.app.test_client()
        with other_device.session_transaction() as session:
            session["_user_id"] = owner_id
        self.assertEqual(other_device.get(path).json, {"drafts": [{"id": draft_id, "title": payload["title"]}]})
        url = f"{path}/{draft_id}"
        self.assertEqual(other_device.get(url).json, {**payload, "id": draft_id})
        self.assertEqual(other_device.put(url, json={**payload, "title": "Revised"}).json["title"], "Revised")
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(other_device.get(url).status_code, 404)

    def test_section_visual_cue_flags_round_trip_and_default_off(self):
        response = self.client.post("/clicktrack/api/tracks", json={
            **self.payload,
            "sections": [{**self.payload["sections"][0], "visualCue": True, "visualEndCue": True},
                         {"name": "Unmarked"}, {"visualCue": "false", "visualEndCue": 1}],
        })
        self.assertEqual(response.status_code, 201)
        saved = self.client.get("/clicktrack/api/tracks").json["tracks"][0]["sections"]
        self.assertEqual([(s["visualCue"], s["visualEndCue"]) for s in saved],
                         [(True, True), (False, False), (False, False)])

    def test_video_draft_hit_point_numbers_are_independent_of_time_and_bar(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"den": "4", "unit": "auto", "shape": "steady"}
        points = [{**marker, "time": t, "bar": bar, "label": label} for t, bar, label in (
            (0, "", "0"), (30, "42", "16"), (32.125, "43", " 16a "), (34, "44", "17")
        )]
        payload = {"videoId": "abcdefghijk", "markers": points}
        saved = self.client.post(path, json=payload)
        self.assertEqual(saved.status_code, 201)
        url = f"{path}/{saved.json['id']}"
        self.assertEqual([(m["label"], m["time"], m["bar"]) for m in self.client.get(url).json["markers"]],
                         [("0", 0, ""), ("16", 30, "42"), ("16A", 32.125, "43"), ("17", 34, "44")])
        for invalid in ("16.", "16", 16):
            with self.subTest(label=invalid):
                points[2]["label"] = invalid
                self.assertEqual(self.client.put(url, json=payload).status_code, 400)
        self.assertEqual(self.client.get(url).json["markers"][2]["label"], "16A")

    def test_video_draft_skip_count_round_trips_and_rejects_invalid_values(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        points = [marker, {**marker, "time": 10, "bar": "12", "skip": "1"}]
        payload = {"videoId": "abcdefghijk", "markers": points}
        saved = self.client.post(path, json=payload)
        self.assertEqual(saved.status_code, 201)
        url = f"{path}/{saved.json['id']}"
        self.assertEqual(self.client.get(url).json["markers"][1]["skip"], "1")
        for invalid in ("-1", "1.5", "9999", True):
            with self.subTest(skip=invalid):
                points[1]["skip"] = invalid
                self.assertEqual(self.client.put(url, json=payload).status_code, 400)
                self.assertEqual(self.client.get(url).json["markers"][1]["skip"], "1")

    def test_rehearsal_figures_round_trip_without_changing_score_bars(self):
        path = "/clicktrack/api/tracks"
        sections = [{**self.payload["sections"][0], "figure": "  6A  ", "startBar": 42},
                    {"type": "pause", "figure": "B"}, {"name": "Unmarked"}]
        response = self.client.post(path, json={**self.payload, "sections": sections})
        self.assertEqual(response.status_code, 201)
        saved = self.client.get(path).json["tracks"][0]["sections"]
        self.assertEqual([section["figure"] for section in saved], ["6A", "B", ""])
        self.assertEqual(saved[0]["startBar"], 42)
        for page in ("/clicktrack/", "/clicktrack/offline/"):
            self.assertIn(b'id="rehearsal-list"', self.client.get(page).data)

    def test_drafts_reject_bad_inputs_and_other_accounts(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        valid = {"videoId": "abcdefghijk", "markers": [marker]}
        for invalid in (
            {**valid, "videoId": "https://example.com/"},
            {**valid, "markers": [{**marker, "time": 1}]},
            {**valid, "markers": [marker, marker]},
            {**valid, "markers": [marker, {**marker, "time": 1.0001}]},
        ):
            self.assertEqual(self.client.post(path, json=invalid).status_code, 400)
        draft_id = self.client.post(path, json=valid).json["id"]
        other = self.app.test_client()
        self.assertEqual(other.get(path).status_code, 401)
        with self.app.app_context():
            user = self.models["User"](username="other", email="other@example.invalid", password_hash="unused")
            self.db.session.add(user)
            self.db.session.commit()
            user_id = user.id
        with other.session_transaction() as session:
            session["_user_id"] = str(user_id)
        self.assertEqual(other.get(path).json, {"drafts": []})
        self.assertEqual(other.post("/clicktrack/api/tracks", json={**self.payload, "videoDraftId": draft_id}).status_code, 400)
        for method in ("get", "put", "delete"):
            self.assertEqual(getattr(other, method)(f"{path}/{draft_id}", json=valid).status_code, 404)

    def test_audio_output_is_part_of_pre_show_check(self):
        response = self.client.get("/clicktrack/")
        pre_show = response.data.split(b'<details id="pre-show">', 1)[1].split(b"</details>", 1)[0]
        self.assertIn(b'id="audio-output"', pre_show)
        self.assertIn(b'id="btn-test-clicks"', pre_show)


if __name__ == "__main__":
    unittest.main()
