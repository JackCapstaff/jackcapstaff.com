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
        login = LoginManager(cls.app)
        login.login_view = "login"

        @login.user_loader
        def load_user(user_id):
            return cls.db.session.get(cls.models["User"], int(user_id))

        cls.app.add_url_rule("/", endpoint="index", view_func=lambda: "")
        cls.app.add_url_rule("/login", endpoint="login", view_func=lambda: "Sign in")
        cls.app.add_url_rule("/live/", endpoint="clicktrack.clicktrack_index", view_func=lambda: "")
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

    def save(self, show, cues):
        return self.client.put(f"/presentations/api/shows/{show['id']}", json={
            "revision": show["revision"], "title": show["title"], "cues": cues,
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

    def test_timeline_validation_and_revision_conflicts(self):
        show = self.create()
        image, video, audio = self.asset(), self.asset("video", duration=20), self.asset("audio", duration=30)
        cues = [self.cue(kind="image", assetId=image["id"], advance="manual"),
                self.cue("next", start=5, kind="video", assetId=video["id"], transition="crossfade"),
                self.cue("caption", lane="text", kind="text", text="<b>Literal text</b>"),
                self.cue("music", lane="audio", kind="audio", assetId=audio["id"], volume=1)]
        saved = self.save(show, cues)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["revision"], 2)
        self.assertEqual(self.save(show, cues).status_code, 409)
        show = saved.json
        for invalid in (
            {**cues[0], "start": True}, {**cues[0], "duration": -1},
            {**cues[0], "fade": 6}, {**cues[0], "start": float("inf")},
            {**cues[1], "offset": 19}, {**cues[2], "color": "url(evil)"},
            {**cues[2], "text": ""}, {**cues[0], "lane": "audio"},
            {**cues[0], "assetId": self.asset(owner=self.other_id)["id"]},
            {**cues[3], "transition": "crossfade"},
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
        self.assertEqual(self.save(show, [self.cue(kind="youtube", assetId=yt.json["id"], duration=181)]).status_code, 400)

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
