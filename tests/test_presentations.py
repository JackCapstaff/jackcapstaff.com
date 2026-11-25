"""Isolated API and browser tests for layered presentations (no app startup jobs)."""
import io
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

from models import init_models
from presentationstudio import presentations_bp
from clicktrack import clicktrack_bp


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "assets"))
        cls.app.config.update(TESTING=True, SECRET_KEY="presentation-tests",
                              SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        cls.db = SQLAlchemy(cls.app)
        cls.models = init_models(cls.db)
        cls.app.db = cls.db
        cls.app.StudioPresentation = cls.models["StudioPresentation"]
        cls.app.PresentationAsset = cls.models["PresentationAsset"]
        cls.app.ClickTrack = cls.models["ClickTrack"]
        cls.app.User = cls.models["User"]
        cls.app.ClickTrackVideoDraft = cls.models["ClickTrackVideoDraft"]
        login = LoginManager(cls.app)
        login.login_view = "login"

        @login.user_loader
        def load_user(user_id):
            return cls.db.session.get(cls.models["User"], int(user_id))

        cls.app.add_url_rule("/", endpoint="index", view_func=lambda: "")
        cls.app.add_url_rule("/login", endpoint="login", view_func=lambda: "Sign in")
        cls.app.register_blueprint(clicktrack_bp)
        cls.app.register_blueprint(presentations_bp)

    def setUp(self):
        with self.app.app_context():
            self.db.create_all()
            admin = self.models["User"](username="admin", email="admin@example.invalid", password_hash="unused", role="admin")
            other = self.models["User"](username="other", email="other@example.invalid", password_hash="unused", role="admin")
            viewer = self.models["User"](username="viewer", email="viewer@example.invalid", password_hash="unused", role="viewer")
            self.db.session.add_all([admin, other, viewer])
            self.db.session.commit()
            self.admin_id, self.other_id, self.viewer_id = admin.id, other.id, viewer.id
        self.client = self.login(self.admin_id)
        self.guest = self.app.test_client()

    def login(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        return client

    def tearDown(self):
        with self.app.app_context():
            self.db.session.remove()
            self.db.drop_all()

    def create(self):
        response = self.client.post("/presentations/api/shows", json={"title": "Concert"})
        self.assertEqual(response.status_code, 201)
        return response.json

    def asset(self, kind="image", owner=None, duration=None):
        with self.app.app_context():
            asset = self.models["PresentationAsset"](user_id=owner or self.admin_id,
                name="Test " + kind, kind=kind, url="abcdefghijk" if kind == "youtube" else "https://media.test/example." + ("png" if kind == "image" else "mp4"),
                duration=duration)
            self.db.session.add(asset)
            self.db.session.commit()
            return asset.to_dict()

    def cue(self, cue_id="one", kind="blank", lane="picture", start=0, duration=5, **kwargs):
        return {"id": cue_id, "name": cue_id, "kind": kind, "lane": lane, "start": start,
                "duration": duration, "transition": "cut", "fade": 0.8, "fadeOut": 0,
                "advance": "auto", "offset": 0, "volume": 0, **kwargs}

    def save(self, show, cues, **extra):
        return self.client.put(f"/presentations/api/shows/{show['id']}", json={
            "revision": show["revision"], "title": show["title"], "cues": cues, **extra,
        })

    def test_permissions_ownership_and_public_link_lifecycle(self):
        show = self.create()
        self.assertEqual(self.guest.get("/presentations/").status_code, 302)
        viewer, other = self.login(self.viewer_id), self.login(self.other_id)
        for client, expected in ((self.guest, 401), (viewer, 403)):
            for path in ("/presentations/api/shows", "/presentations/api/assets"):
                self.assertEqual(client.get(path).status_code, expected)
                self.assertEqual(client.post(path, json={}).status_code, expected)
            self.assertEqual(client.put(f"/presentations/api/shows/{show['id']}/live", json={}).status_code, expected)
        self.assertEqual(other.get(f"/presentations/api/shows/{show['id']}").status_code, 404)
        for path in ("/presentations/", show["editUrl"], show["controlUrl"], show["url"]):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.guest.get(show["url"] + "state").headers["Cache-Control"], "no-store")
        self.assertFalse(self.guest.get(show["url"] + "state").json["active"])
        self.assertEqual(self.guest.post(show["url"] + "state", json={}).status_code, 405)
        self.assertEqual(self.save(show, [self.cue()]).status_code, 200)
        live_path = f"/presentations/api/shows/{show['id']}/live"
        live = self.client.put(live_path, json={"version": 0, "action": "start"}).json
        self.assertTrue(self.guest.get(show["url"] + "state").json["active"])
        self.assertEqual(self.client.put(live_path, json={"version": 0, "action": "pause"}).status_code, 409)
        revoked = self.client.put(live_path, json={"version": live["version"], "action": "revoke"}).json
        self.assertNotEqual(revoked["url"], show["url"])
        self.assertEqual(self.guest.get(show["url"]).status_code, 404)
        self.assertFalse(self.guest.get(revoked["url"] + "state").json["active"])

    def test_performance_home_only_shows_media_centre_to_admins(self):
        self.assertEqual(self.guest.get("/live/home/").status_code, 302)
        admin_home = self.client.get("/live/home/")
        self.assertEqual(admin_home.status_code, 200)
        self.assertIn(b'Open Click Tracks', admin_home.data)
        self.assertIn(b'href="/presentations/"', admin_home.data)
        self.assertIn(b'Open Media Centre', admin_home.data)
        for role in ("viewer", "editor", "performer"):
            with self.app.app_context():
                user = self.db.session.get(self.models["User"], self.viewer_id)
                user.role = role
                self.db.session.commit()
            client = self.login(self.viewer_id)
            home = client.get("/live/home/")
            self.assertEqual(home.status_code, 200)
            self.assertIn(b'Open Click Tracks', home.data)
            self.assertNotIn(b'Media Centre', home.data)
            self.assertNotIn(b'/presentations/', home.data)
            for path in ("/presentations/", "/presentations/api/shows", "/presentations/api/assets"):
                self.assertEqual(client.get(path).status_code, 403)
        media = self.client.get("/presentations/")
        self.assertIn(b'Media Centre', media.data)
        self.assertIn(b'href="/live/home/"', media.data)

    def test_timeline_validation_and_revision_conflicts(self):
        show = self.create()
        image, video, audio = self.asset(), self.asset("video", duration=20), self.asset("audio", duration=30)
        cues = [self.cue(kind="image", assetId=image["id"], advance="manual"),
                self.cue("next", start=5, kind="video", assetId=video["id"], transition="crossfade", volume=0.37, muted=True),
                self.cue("caption", lane="text", kind="text", text="<b>Literal text</b>"),
                self.cue("music", lane="audio", kind="audio", assetId=audio["id"], volume=1)]
        saved = self.save(show, cues)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["revision"], 2)
        video_cue = next(cue for cue in saved.json["cues"] if cue["id"] == "next")
        self.assertTrue(video_cue["muted"])
        self.assertEqual(video_cue["volume"], 0.37)
        self.assertFalse(next(cue for cue in saved.json["cues"] if cue["id"] == "music")["muted"])
        self.assertEqual(self.save(show, cues).status_code, 409)
        show = saved.json
        for invalid in (
            {**cues[0], "start": True}, {**cues[0], "duration": -1},
            {**cues[0], "fade": 6}, {**cues[0], "start": float("inf")},
            {**cues[1], "offset": 19}, {**cues[2], "color": "url(evil)"},
            {**cues[2], "text": ""}, {**cues[0], "lane": "audio"},
            {**cues[0], "assetId": self.asset(owner=self.other_id)["id"]},
            {**cues[3], "transition": "crossfade"},
            {**cues[1], "muted": "false"}, {**cues[1], "muted": 1},
            {**cues[1], "volume": 1.01}, {**cues[1], "volume": -0.01},
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.save(show, [invalid]).status_code, 400)
        self.assertEqual(self.save(show, [self.cue(), self.cue("overlap", start=4)]).status_code, 400)
        self.assertEqual(self.save(show, [self.cue(), self.cue()]).status_code, 400)

    def test_uploaded_media_and_youtube_validation(self):
        data = {"url": "https://youtu.be/abcdefghijk", "name": "Film excerpt"}
        yt = self.client.post("/presentations/api/assets/youtube", json=data)
        self.assertEqual(yt.status_code, 201)
        self.assertEqual(yt.json["kind"], "youtube")
        for url in ("javascript:alert(1)", "https://evil.test/watch?v=abcdefghijk", "https://youtube.com/watch?v=bad", "https://[bad"):
            self.assertEqual(self.client.post("/presentations/api/assets/youtube", json={**data, "url": url}).status_code, 400)
        show = self.create()
        long_show = self.save(show, [self.cue(kind="youtube", assetId=yt.json["id"], duration=7200, offset=120)])
        self.assertEqual(long_show.status_code, 200, long_show.json)
        self.assertEqual(long_show.json["cues"][0]["duration"], 7200)
        self.assertEqual(self.save(long_show.json, [self.cue(
            kind="youtube", assetId=yt.json["id"], duration=86400, start=1
        )]).status_code, 400)

        upload_data = {"publicId": f"jackcapstaff/presentations/{self.admin_id}/" + "a" * 32, "name": "clip.mp4"}
        def upload():
            return self.client.post("/presentations/api/assets", json=upload_data)

        with patch.dict(os.environ, {"CLOUDINARY_URL": ""}):
            self.assertEqual(upload().status_code, 503)
        result = {"resource_type": "video", "duration": 30, "width": 1280, "height": 720,
                  "format": "mp4", "bytes": 1000, "secure_url": "https://res.cloudinary.com/test/video/upload/clip.mp4"}
        with patch.dict(os.environ, {"CLOUDINARY_URL": "cloudinary://test:test@test"}):
            with patch("presentationstudio.cloudinary.config", return_value=SimpleNamespace(
                cloud_name="test", api_key="test-key", api_secret="test-secret"
            )):
                signed = self.client.post("/presentations/api/uploads/sign", json={"name": "clip.mp4", "size": 1000})
                self.assertEqual(signed.status_code, 200, signed.json)
                self.assertEqual(signed.json["endpoint"], "https://api.cloudinary.com/v1_1/test/video/upload")
                self.assertIn("signature", signed.json["parameters"])
                self.assertNotIn("api_secret", signed.json["parameters"])
                self.assertEqual(self.client.post("/presentations/api/uploads/sign", json={
                    "name": "clip.mp4", "size": 100 * 1024 * 1024
                }).status_code, 200)
                self.assertEqual(self.client.post("/presentations/api/uploads/sign", json={
                    "name": "clip.mp4", "size": 100 * 1024 * 1024 + 1
                }).status_code, 400)
            result["duration"] = 180
            with patch("presentationstudio.cloudinary.api.resource", return_value=result) as uploader:
                response = upload()
                self.assertEqual(response.status_code, 201, response.json)
                self.assertEqual(response.json["duration"], 180)
                self.assertEqual(uploader.call_args.kwargs["resource_type"], "video")
                asset_id = response.json["id"]
                self.assertEqual(upload().status_code, 200)
                self.assertEqual(uploader.call_count, 1)
            upload_data["publicId"] = f"jackcapstaff/presentations/{self.admin_id}/" + "b" * 32
            with patch("presentationstudio.cloudinary.api.resource", return_value={**result, "duration": 181}), \
                 patch("presentationstudio.cloudinary.uploader.destroy", return_value={"result": "ok"}) as cleanup:
                self.assertEqual(upload().status_code, 400)
                cleanup.assert_called_once()
            from cloudinary.exceptions import Error
            with patch("presentationstudio.cloudinary.api.resource", side_effect=Error("Test failure")):
                with self.assertLogs(self.app.logger, level="ERROR"):
                    self.assertEqual(upload().status_code, 502)
            upload_data["publicId"] = f"jackcapstaff/presentations/{self.other_id}/" + "b" * 32
            self.assertEqual(upload().status_code, 400)
            with patch("presentationstudio.cloudinary.uploader.destroy", return_value={"result": "ok"}):
                self.assertEqual(self.client.delete(f"/presentations/api/assets/{asset_id}", json={}).status_code, 200)
        self.assertEqual(self.client.post("/presentations/api/assets", data={"file": (io.BytesIO(b"x"), "clip.mp4")}).status_code, 400)

    def test_used_media_cannot_be_deleted_until_saved_and_live_references_are_removed(self):
        image = self.asset()
        show = self.save(self.create(), [self.cue(kind="image", assetId=image["id"])]).json
        path = f"/presentations/api/assets/{image['id']}"
        self.assertEqual(self.login(self.other_id).delete(path, json={}).status_code, 404)
        self.assertEqual(self.client.delete(path, json={}).status_code, 409)
        live_path = f"/presentations/api/shows/{show['id']}/live"
        live = self.client.put(live_path, json={"version": 0, "action": "start"}).json
        self.save(show, [self.cue()])
        self.assertEqual(self.client.delete(path, json={}).status_code, 409)
        self.client.put(live_path, json={"version": live["version"], "action": "stop"})
        self.assertEqual(self.client.delete(path, json={}).status_code, 200)

    def test_musical_links_validate_positions_and_freeze_broadcast_snapshot(self):
        created = self.client.post("/live/api/tracks", json={"title": "Musical set", "sections": [
            {"name": "Opening", "bars": 4, "num": 4, "den": 4, "countIn": 0},
            {"name": "Wait", "type": "pause"}, {"name": "Finale", "bars": 2},
        ]})
        self.assertEqual(created.status_code, 201, created.json)
        track = self.client.get("/presentations/api/music-tracks").json["tracks"][0]
        show = self.create()
        cues = [self.cue("first", duration=10), self.cue("second", start=10, duration=20)]
        music = {"trackId": track["id"], "signature": track["signature"], "holds": {"1": "continue"}, "events": [
            {"id": "opening", "section": 0, "bar": 1, "beat": 1, "cueId": "first", "mode": "hold"},
            {"id": "change", "section": 0, "bar": 3, "beat": 2, "cueId": "second", "mode": "run"},
        ]}
        saved = self.save(show, cues, music=music)
        self.assertEqual(saved.status_code, 200, saved.json)
        show = saved.json
        path = f"/presentations/api/shows/{show['id']}"
        for invalid in (
            {**music, "signature": "old"}, {**music, "trackId": 999999},
            {**music, "events": [{**music["events"][0], "bar": 5}]},
            {**music, "events": [{**music["events"][0], "beat": 5}]},
            {**music, "events": [{**music["events"][0], "cueId": "missing"}]},
            {**music, "events": [music["events"][0], music["events"][0]]},
            {**music, "holds": {"999": "pause"}},
        ):
            self.assertIn(self.save(show, cues, music=invalid).status_code, (400, 409))
        live = self.client.put(path + "/live", json={"version": 0, "action": "start"}).json
        self.assertFalse(live["playing"])
        session = live["show"]["music"]["session"]
        performer_url = f"/presentations/{show['id']}/music/{session}/"
        self.assertEqual(self.guest.get(performer_url).status_code, 302)
        self.assertEqual(self.login(self.other_id).get(performer_url).status_code, 404)
        performer = self.client.get(performer_url)
        self.assertEqual(performer.status_code, 200)
        self.assertIn(b'musicBridge:', performer.data)
        version = live["version"]
        def command(**values):
            nonlocal version
            result = self.client.put(path + "/live", json={"version": version, "action": "music",
                "session": session, "playing": True, **values})
            if result.status_code == 200:
                version = result.json["version"]
            return result
        held = command(eventId="opening", elapsed=0.1).json
        self.assertEqual(held["position"], 0)
        self.assertFalse(held["playing"])
        self.assertTrue(held["musicFrozen"])
        running = command(eventId="change", elapsed=0.2).json
        self.assertEqual(running["position"], 10.2)
        self.assertTrue(running["playing"])
        self.assertFalse(running["musicFrozen"])
        self.assertEqual(command(session="wrong").status_code, 409)
        self.assertEqual(command(eventId="missing").status_code, 400)
        self.assertFalse(command(playing=False).json["playing"])
        self.assertNotIn("music", self.guest.get(show["url"] + "state").json["show"])
        # A running musical snapshot survives track edits; restarting requires a reviewed link.
        changed = self.client.put(f"/live/api/tracks/{track['id']}", json={
            "title": track["title"], "sections": [{**section, "bpm": 80} for section in track["sections"]],
        })
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(command().json["playing"])
        self.assertEqual(self.client.put(path + "/live", json={"version": version, "action": "start"}).status_code, 409)
        programme = {"sets": [show["id"]], "placeholderId": self.asset()["id"]}
        concert = self.save(self.create(), [], programme=programme).json
        self.assertEqual(self.client.get(f"/presentations/api/shows/{concert['id']}/compiled").status_code, 409)

    def test_video_local_hits_and_nested_concert_music_compilation(self):
        created = self.client.post("/live/api/tracks", json={"title": "Film score", "sections": [
            {"name": "First scene", "bpm": 120, "bars": 8, "countIn": 0},
            {"name": "Wait", "type": "pause"},
            {"name": "Second scene", "bpm": 90, "bars": 8, "countIn": 0},
        ]})
        self.assertEqual(created.status_code, 201)
        track = self.client.get("/presentations/api/music-tracks").json["tracks"][0]
        cues = [
            self.cue("video-one", kind="youtube", assetId=self.asset("youtube")["id"], offset=30, duration=20),
            self.cue("video-two", kind="video", assetId=self.asset("video", duration=100)["id"],
                     offset=60, start=20, duration=30),
        ]
        music = {"trackId": track["id"], "signature": track["signature"], "holds": {"1": "continue"}, "events": [
            {"id": "hit-one", "section": 0, "bar": 1, "beat": 1, "cueId": "video-one", "mediaTime": 32.125, "mode": "run"},
            {"id": "hit-two", "section": 2, "bar": 1, "beat": 1, "cueId": "video-two", "mediaTime": 65, "mode": "hold"},
        ]}
        show = self.create()
        for value in (True, "32", -1, 29.999, 50):
            invalid = {**music, "events": [{**music["events"][0], "mediaTime": value}]}
            self.assertEqual(self.save(show, cues, music=invalid).status_code, 400)
        self.assertEqual(self.save(show, [self.cue("video-one")], music={
            **music, "events": [music["events"][0]]}).status_code, 400)
        show = self.save(show, cues, music=music).json
        path = f"/presentations/api/shows/{show['id']}"
        live = self.client.put(path + "/live", json={"version": 0, "action": "start"}).json
        def trigger(state, event_id, elapsed=0):
            response = self.client.put(path + "/live", json={"version": state["version"], "action": "music",
                "session": state["show"]["music"]["session"], "eventId": event_id, "elapsed": elapsed, "playing": True})
            self.assertEqual(response.status_code, 200, response.json)
            return response.json
        hit = trigger(live, "hit-one", 0.1)
        self.assertAlmostEqual(hit["position"], 2.225)
        hit = trigger(hit, "hit-two", 0.1)
        self.assertEqual(hit["position"], 25)
        self.assertFalse(hit["playing"])
        self.assertTrue(hit["musicFrozen"])
        logo = self.asset()
        visual_set = self.save(self.create(), [self.cue("unscored", start=1, duration=5)], title="Visuals only").json
        nested = self.save(self.create(), [], programme={
            "sets": [show["id"], visual_set["id"]], "placeholderId": logo["id"]}).json
        concert = self.save(self.create(), [], programme={
            "sets": [nested["id"], show["id"]], "placeholderId": logo["id"]}).json
        path = f"/presentations/api/shows/{concert['id']}"
        compiled = self.client.get(path + "/compiled")
        self.assertEqual(compiled.status_code, 200, compiled.json)
        combined = compiled.json["music"]
        self.assertEqual([section["type"] for section in combined["track"]["sections"]],
                         ["click", "pause", "click", "pause", "pause", "pause", "click", "pause", "click"])
        self.assertEqual(len({event["id"] for event in combined["events"]}), 7)
        self.assertEqual([event["section"] for event in combined["events"]], [0, 2, 3, 4, 5, 6, 8])
        self.assertEqual(combined["holds"], {"1": "continue", "3": "pause", "4": "continue", "5": "pause", "7": "continue"})
        self.assertEqual(combined["track"]["videoOffset"], 0)
        self.assertIsNone(combined["track"]["videoDraftId"])
        live = self.client.put(path + "/live", json={"version": 0, "action": "start"}).json
        session = live["show"]["music"]["session"]
        first = trigger(live, "0/0/hit-one", 100)
        self.assertAlmostEqual(first["position"], 49.999)
        self.assertFalse(first["playing"])
        interval = trigger(first, "generated-0/interval-0", 10)
        self.assertEqual(interval["position"], 50)
        self.assertFalse(interval["playing"])
        self.assertTrue(interval["musicFrozen"])
        unscored = trigger(interval, "generated-0/1/unscored")
        self.assertEqual(unscored["position"], 50.1)
        self.assertTrue(unscored["playing"])
        second = trigger(unscored, "1/hit-two")
        self.assertAlmostEqual(second["position"], 81.2)
        # Repeated child sets receive unique event/cue IDs; a running concert is immutable.
        changed = self.save(show, cues, music={**music, "events": [
            {**music["events"][0], "mediaTime": 35}, music["events"][1]]})
        self.assertEqual(changed.status_code, 200)
        frozen = self.client.get(path + "/live").json
        self.assertEqual(frozen["show"]["music"]["session"], session)
        self.assertEqual(frozen["show"]["music"]["events"][0]["mediaTime"], 32.125)
        public = self.guest.get(concert["url"] + "state").json
        self.assertNotIn("music", public["show"])
        self.assertIn("musicBoundary", public)

    def test_linked_concert_compilation_intervals_snapshot_and_cycles(self):
        logo = self.asset()
        sets = [self.save(self.create(), [self.cue(duration=2, advance="manual"),
                self.cue("music", lane="audio", kind="audio", duration=2, assetId=self.asset("audio", duration=10)["id"]) ]).json
                for _ in range(3)]
        concert = self.create()
        programme = {"sets": [item["id"] for item in sets], "placeholderId": logo["id"]}
        concert = self.save(concert, [], programme=programme).json
        self.assertEqual(concert["programme"], programme)
        path = f"/presentations/api/shows/{concert['id']}"
        compiled = self.client.get(path + "/compiled")
        self.assertEqual(compiled.status_code, 200, compiled.json)
        cues = compiled.json["cues"]
        self.assertEqual(len({cue["id"] for cue in cues}), len(cues))
        intervals = [cue for cue in cues if cue["kind"] == "image"]
        self.assertEqual([cue["start"] for cue in intervals], [2, 4.1])
        self.assertTrue(all(cue["advance"] == "manual" for cue in intervals))
        first = next(cue for cue in cues if cue["start"] == 0 and cue["lane"] == "picture")
        self.assertEqual(first["advance"], "auto")
        self.assertEqual(self.login(self.other_id).get(path + "/compiled").status_code, 404)
        self.assertEqual(self.guest.get(path + "/compiled").status_code, 401)
        self.assertEqual(self.client.delete(f"/presentations/api/shows/{sets[0]['id']}",
            json={"revision": sets[0]["revision"]}).status_code, 409)
        self.assertEqual(self.client.delete(f"/presentations/api/assets/{logo['id']}", json={}).status_code, 409)
        for invalid in (
            {"sets": [concert["id"]], "placeholderId": logo["id"]},
            {"sets": [999999], "placeholderId": logo["id"]},
            {"sets": [sets[0]["id"]], "placeholderId": self.asset(owner=self.other_id)["id"]},
            {"sets": [True], "placeholderId": logo["id"]},
        ):
            self.assertEqual(self.save(concert, [], programme=invalid).status_code, 400)
        self.assertEqual(self.save(sets[0], [], programme={
            "sets": [concert["id"]], "placeholderId": logo["id"],
        }).status_code, 400)
        version = 0
        clock = time.time()
        def command(action):
            nonlocal version
            with patch("presentationstudio.time.time", return_value=clock):
                result = self.client.put(path + "/live", json={"version": version, "action": action})
            self.assertEqual(result.status_code, 200, result.json)
            version = result.json["version"]
            return result.json
        command("start")
        clock += 3
        held = command("heartbeat")
        self.assertFalse(held["playing"])
        self.assertAlmostEqual(held["position"], 2.099)
        clock += 300
        self.assertAlmostEqual(command("heartbeat")["position"], 2.099)
        self.assertEqual(command("next")["position"], 2.1)
        # Edits to linked sets affect a subsequent start, never the active snapshot.
        sets[0] = self.save(sets[0], [self.cue(duration=10)]).json
        self.assertEqual(command("heartbeat")["show"]["cues"][0]["duration"], 2)
        self.assertEqual(command("start")["show"]["cues"][0]["duration"], 10)
        nested = self.save(self.create(), [], programme={
            "sets": [concert["id"], concert["id"]], "placeholderId": logo["id"],
        }).json
        nested_show = self.client.get(f"/presentations/api/shows/{nested['id']}/compiled")
        self.assertEqual(nested_show.status_code, 200, nested_show.json)
        self.assertEqual(len({cue["id"] for cue in nested_show.json["cues"]}), len(nested_show.json["cues"]))

    def test_placeholder_pauses_all_layers_resumes_and_preserves_selection(self):
        image = self.asset()
        foreign = self.asset(owner=self.other_id)
        show = self.save(self.create(), [self.cue(duration=60), self.cue("next", start=60)]).json
        path = f"/presentations/api/shows/{show['id']}/live"
        clock = time.time()
        version = 0
        def command(action, expected=200, **extra):
            nonlocal version
            with patch("presentationstudio.time.time", return_value=clock):
                result = self.client.put(path, json={"version": version, "action": action, **extra})
            self.assertEqual(result.status_code, expected, result.json)
            if expected == 200:
                version = result.json["version"]
            return result.json
        command("setPlaceholder", expected=400, assetId=foreign["id"])
        command("setPlaceholder", expected=400, assetId=self.asset("audio")["id"])
        command("setPlaceholder", assetId=image["id"])
        command("start")
        clock += 3
        held = command("placeholder")
        self.assertEqual(held["position"], 3)
        self.assertTrue(held["placeholder"])
        self.assertFalse(held["playing"])
        clock += 20
        held = command("heartbeat")
        self.assertEqual(held["position"], 3)
        with patch("presentationstudio.time.time", return_value=clock):
            public = self.guest.get(show["url"] + "state").json
        self.assertTrue(public["placeholder"])
        self.assertEqual(public["placeholderAsset"]["id"], image["id"])
        command("setPlaceholder", expected=400, assetId=None)
        self.assertEqual(self.client.delete(f"/presentations/api/assets/{image['id']}", json={}).status_code, 409)
        resumed = command("play")
        self.assertFalse(resumed["placeholder"])
        self.assertEqual(resumed["position"], 3)
        clock += 2
        self.assertEqual(command("heartbeat")["position"], 5)
        command("placeholder")
        jumped = command("jump", cueId="next")
        self.assertEqual(jumped["position"], 60)
        self.assertTrue(jumped["playing"])
        self.assertFalse(jumped["placeholder"])
        command("jump", expected=400, cueId="missing")
        command("stop")
        restarted = command("start")
        self.assertEqual(restarted["placeholderAsset"]["id"], image["id"])
        command("stop")
        command("setPlaceholder", assetId=None)
        self.assertEqual(self.client.delete(f"/presentations/api/assets/{image['id']}", json={}).status_code, 200)

    def test_manual_auto_layers_snapshot_seek_blackout_and_heartbeat(self):
        show = self.create()
        cues = [self.cue(advance="manual"), self.cue("two", start=5),
                self.cue("title", lane="text", kind="text", text="Hello", duration=10)]
        show = self.save(show, cues).json
        path = f"/presentations/api/shows/{show['id']}/live"
        clock = time.time()
        version = 0

        def command(action, **kwargs):
            nonlocal version
            with patch("presentationstudio.time.time", return_value=clock):
                response = self.client.put(path, json={"version": version, "action": action, **kwargs})
            self.assertEqual(response.status_code, 200, response.json)
            version = response.json["version"]
            return response.json

        command("start")
        clock += 6
        held = command("heartbeat")
        self.assertFalse(held["playing"])
        self.assertAlmostEqual(held["position"], 4.999)
        moved = command("next")
        self.assertEqual(moved["position"], 5)
        self.assertTrue(moved["playing"])
        clock += 1
        command("black")
        with patch("presentationstudio.time.time", return_value=clock):
            self.assertTrue(self.guest.get(show["url"] + "state").json["black"])
        command("pause")
        clock += 10
        self.assertEqual(command("heartbeat")["position"], 6)
        command("seek", position=2)
        self.assertEqual(command("play")["position"], 2)
        # A draft edit must not mutate the broadcast snapshot.
        self.save(show, [self.cue("changed", duration=30)])
        with patch("presentationstudio.time.time", return_value=clock):
            self.assertEqual(len(self.guest.get(show["url"] + "state").json["show"]["cues"]), 3)
        with patch("presentationstudio.time.time", return_value=clock + 9):
            stale = self.guest.get(show["url"] + "state").json
        self.assertFalse(stale["active"])
        self.assertEqual(stale["show"]["cues"], [])
        command("stop")
        self.assertFalse(command("start")["black"])
