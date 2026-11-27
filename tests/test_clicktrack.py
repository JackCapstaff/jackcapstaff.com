import unittest
from pathlib import Path

from clicktrack import _clean_section


class ClickTrackSectionTests(unittest.TestCase):
    def test_existing_database_receives_private_publication_default(self):
        import ast
        from types import SimpleNamespace
        from sqlalchemy import create_engine, inspect, text

        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "ensure_optional_columns")
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE click_track (id INTEGER PRIMARY KEY, title TEXT)"))
                connection.execute(text("INSERT INTO click_track (title) VALUES ('Existing private track')"))
                connection.execute(text("CREATE TABLE page_content (id INTEGER PRIMARY KEY)"))
                connection.execute(text("CREATE TABLE studio_presentation (id INTEGER PRIMARY KEY, title TEXT)"))
                connection.execute(text("INSERT INTO studio_presentation (title) VALUES ('Existing set')"))
            namespace = {"db": SimpleNamespace(engine=engine), "inspect": inspect, "text": text}
            exec(compile(ast.Module(body=[function], type_ignores=[]), "schema-upgrade", "exec"), namespace)
            namespace["ensure_optional_columns"]()
            namespace["ensure_optional_columns"]()
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT is_public FROM click_track")).scalar_one(), 0)
                self.assertIsNone(connection.execute(text("SELECT programme FROM studio_presentation")).scalar_one())
                self.assertIsNone(connection.execute(text("SELECT music FROM studio_presentation")).scalar_one())
                for column in ("settings", "history", "reports"):
                    self.assertIsNone(connection.execute(text(f"SELECT {column} FROM studio_presentation")).scalar_one())
                self.assertEqual(connection.execute(text("SELECT reports_version FROM studio_presentation")).scalar_one(), 0)
        finally:
            engine.dispose()

    def test_timed_pause_round_trips_millisecond_duration(self):
        section = _clean_section({"type": "timedPause", "pauseDuration": 2.123, "bars": 1, "startBar": 175})
        self.assertEqual(section["type"], "timedPause")
        self.assertEqual(section["pauseDuration"], 2.123)
        self.assertEqual(_clean_section(section), section)

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

    def test_timed_pause_subdivision_defaults_and_bounds(self):
        self.assertEqual(_clean_section({"type": "timedPause"})["pauseSubdivision"], 1)
        self.assertEqual(_clean_section({"type": "timedPause", "pauseSubdivision": 3})["pauseSubdivision"], 3)
        self.assertEqual(_clean_section({"type": "timedPause", "pauseSubdivision": 9})["pauseSubdivision"], 4)

    def test_compound_click_defaults_and_explicit_choices(self):
        for numerator in (6, 9, 12):
            with self.subTest(numerator=numerator):
                section = _clean_section({"num": numerator, "den": 8})
                self.assertEqual(section["click"], "units")
        self.assertEqual(_clean_section({"num": 4, "den": 4})["click"], "beats")
        section = _clean_section({
            "num": 6, "den": 8, "click": "beats", "compoundClickDefaulted": True,
        })
        self.assertEqual(_clean_section(section), section)
        self.assertEqual(section["click"], "beats")
        self.assertTrue(section["compoundClickDefaulted"])

    def test_ramp_mode_defaults_and_round_trips(self):
        self.assertEqual(_clean_section({"bpmEnd": 80})["rampMode"], "linearBeat")
        for mode in ("linearBeat", "linearTime"):
            section = _clean_section({"bpm": 120, "bpmEnd": 80, "rampMode": mode})
            self.assertEqual(section["rampMode"], mode)
            self.assertEqual(_clean_section(section), section)
        self.assertEqual(_clean_section({"rampMode": "invalid"})["rampMode"], "linearBeat")

    def test_section_hit_point_round_trips(self):
        section = _clean_section({"hitPoint": "24A", "figure": "5", "startBar": 267})
        self.assertEqual(_clean_section(section), section)
        self.assertEqual(section["hitPoint"], "24A")
        self.assertEqual(_clean_section({})["hitPoint"], "")

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
        cls.app.User = cls.models["User"]
        cls.app.ClickTrackVideoDraft = cls.models["ClickTrackVideoDraft"]
        cls.app.ClickTrackPresentation = cls.models["ClickTrackPresentation"]
        login = LoginManager(cls.app)

        cls.app.add_url_rule("/", endpoint="index", view_func=lambda: "")

        @login.user_loader
        def load_user(user_id):
            return cls.db.session.get(cls.models["User"], int(user_id))

        cls.app.register_blueprint(clicktrack_bp)
        cls.app.register_blueprint(clicktrack_bp, name="clicktrack_legacy", url_prefix="/clicktrack")
        # Exercise the production auth handlers without importing the scheduler-starting app.
        import ast
        from urllib.parse import urljoin, urlparse
        from flask import flash, redirect, render_template, request, url_for
        from flask_login import current_user, login_user

        source = (root / "app.py").read_text(encoding="utf-8")
        handlers = [node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name in ("login", "register", "_is_safe_redirect_target")]
        namespace = {
            "app": cls.app, "db": cls.db, "User": cls.models["User"], "current_user": current_user,
            "login_user": login_user, "flash": flash, "redirect": redirect, "render_template": render_template,
            "request": request, "url_for": url_for, "urlparse": urlparse, "urljoin": urljoin,
        }
        exec(compile(ast.Module(body=handlers, type_ignores=[]), "auth-handlers", "exec"), namespace)
        cls.app.add_url_rule("/forgot-password", endpoint="forgot_password", view_func=lambda: "")

    def setUp(self):
        with self.app.app_context():
            self.db.create_all()
            user = self.models["User"](username="test", email="test@example.invalid", password_hash="unused", role="admin")
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

    def test_band_invite_signup_login_and_publication_access(self):
        from jinja2 import ChoiceLoader, DictLoader
        from urllib.parse import parse_qs, urlsplit

        track = self.client.post("/live/api/tracks", json=self.payload).json
        invite = f"/live/invite/{track['id']}/"
        guest = self.app.test_client()
        self.assertEqual(guest.get(invite).status_code, 404)
        self.client.put(f"/live/api/tracks/{track['id']}/publication", json={"isPublic": True})
        page = guest.get(invite)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Create an account", page.data)
        self.assertNotIn(track["title"].encode(), page.data)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        old_loader = self.app.jinja_loader
        self.app.jinja_loader = ChoiceLoader([
            DictLoader({"base.html": "{% block main_content %}{% endblock %}"}),
            old_loader,
        ])
        self.app.jinja_env.cache.clear()
        try:
            from html.parser import HTMLParser

            class Links(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.hrefs = []

                def handle_starttag(self, tag, attrs):
                    if tag == "a":
                        self.hrefs.append(dict(attrs).get("href", ""))

            login_page = guest.get("/login", query_string={"next": invite})
            links = Links()
            links.feed(login_page.get_data(as_text=True))
            signup = next(link for link in links.hrefs if urlsplit(link).path == "/register")
            self.assertEqual(parse_qs(urlsplit(signup).query)["next"], [invite])
            signup_page = guest.get(signup)
            self.assertIn(f'value="{invite}"'.encode(), signup_page.data)
            registered = guest.post("/register", data={
                "username": "invited-member", "email": "member@example.invalid", "name": "Member",
                "password": "test-invitation-password", "password_confirm": "test-invitation-password",
                "next": invite,
            })
            self.assertEqual(registered.status_code, 302)
            self.assertEqual(registered.headers["Location"], invite)
            with self.app.app_context():
                member = self.models["User"].query.filter_by(username="invited-member").one()
                self.assertEqual(member.role, "viewer")
            performance = guest.get(invite)
            self.assertIn(b"canEdit: false", performance.data)
            self.assertIn(b"invitedTrack:", performance.data)
            self.assertIn(track["title"].encode(), performance.data)
            self.assertEqual(guest.post("/live/api/tracks", json=self.payload).status_code, 403)
            returning = self.app.test_client()
            logged_in = returning.post("/login", data={
                "identifier": "invited-member", "password": "test-invitation-password", "next": invite,
            })
            self.assertEqual(logged_in.headers["Location"], invite)
            self.assertEqual(returning.get(invite).status_code, 200)
            self.client.put(f"/live/api/tracks/{track['id']}/publication", json={"isPublic": False})
            self.assertEqual(guest.get(invite).status_code, 404)
            self.assertEqual(self.app.test_client().get(invite).status_code, 404)
        finally:
            self.app.jinja_loader = old_loader
            self.app.jinja_env.cache.clear()

    def test_only_admins_edit_and_only_published_tracks_are_performable(self):
        private = self.client.post("/clicktrack/api/tracks", json={**self.payload, "title": "Private"}).json
        published = self.client.post("/clicktrack/api/tracks", json={**self.payload, "title": "Published", "isPublic": True}).json
        self.assertFalse(published["isPublic"])
        marker = {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={"videoId": "abcdefghijk", "markers": [marker]}).json
        private_draft = self.client.post("/clicktrack/api/video-drafts", json={"videoId": "zyxwvutsrqp", "markers": [marker]}).json
        published = self.client.put(f"/clicktrack/api/tracks/{published['id']}", json={
            **self.payload, "title": "Published", "videoDraftId": draft["id"],
        }).json
        publication = f"/clicktrack/api/tracks/{published['id']}/publication"
        self.assertEqual(self.client.put(publication, json={"isPublic": "true"}).status_code, 400)
        published = self.client.put(publication, json={"isPublic": True}).json
        self.assertTrue(published["isPublic"])
        for role in ("viewer", "editor"):
            with self.subTest(role=role):
                with self.app.app_context():
                    user = self.models["User"](username=role, email=f"{role}@example.invalid", password_hash="unused", role=role)
                    self.db.session.add(user)
                    self.db.session.commit()
                    user_id = user.id
                    legacy = self.models["ClickTrack"](user_id=user_id, title="Legacy personal", is_public=True, sections=[])
                    self.db.session.add(legacy)
                    self.db.session.commit()
                performer = self.app.test_client()
                with performer.session_transaction() as session:
                    session["_user_id"] = str(user_id)
                self.assertEqual(performer.get("/clicktrack/").status_code, 200)
                self.assertIn(b"canEdit: false", performer.get("/clicktrack/").data)
                self.assertEqual(performer.get("/clicktrack/api/tracks").json["tracks"], [published])
                self.assertEqual(performer.get(f"/clicktrack/api/video-drafts/{draft['id']}").status_code, 200)
                self.assertEqual(performer.get(f"/clicktrack/api/video-drafts/{private_draft['id']}").status_code, 404)
                self.assertEqual(performer.get("/clicktrack/api/video-drafts").status_code, 403)
                for method, path in (
                    ("post", "/clicktrack/api/tracks"),
                    ("put", f"/clicktrack/api/tracks/{published['id']}"),
                    ("delete", f"/clicktrack/api/tracks/{published['id']}"),
                    ("put", f"/clicktrack/api/tracks/{published['id']}/video-editor"),
                    ("put", f"/clicktrack/api/tracks/{published['id']}/video-link"),
                    ("put", publication),
                    ("post", "/clicktrack/api/tracks/from-video-draft"),
                    ("post", "/clicktrack/api/tracks/restore-backup"),
                    ("post", "/clicktrack/api/video-drafts"),
                    ("put", f"/clicktrack/api/video-drafts/{draft['id']}"),
                    ("delete", f"/clicktrack/api/video-drafts/{draft['id']}"),
                    ("put", f"/clicktrack/api/video-drafts/{draft['id']}/unlinked"),
                ):
                    self.assertEqual(getattr(performer, method)(path, json=self.payload).status_code, 403, path)
                state = {"videoId": "abcdefghijk", "time": 0, "playing": False, "blank": True}
                self.assertEqual(performer.post("/clicktrack/api/presentations", json=state).status_code, 403)
                self.assertEqual(performer.post("/live/api/presentations", json=state).status_code, 403)
                self.assertEqual(performer.post("/live/api/tracks", json=self.payload).status_code, 403)
                self.assertEqual(performer.get("/live/api/video-drafts").status_code, 403)
                for method in ("put", "delete"):
                    self.assertEqual(getattr(performer, method)("/clicktrack/api/presentations/1", json=state).status_code, 403)
        self.assertIn(private, self.client.get("/clicktrack/api/tracks").json["tracks"])
        self.assertEqual(self.client.put(publication, json={"isPublic": False}).status_code, 200)
        self.assertEqual(performer.get("/clicktrack/api/tracks").json["tracks"], [])
        self.assertEqual(performer.get(f"/clicktrack/api/video-drafts/{draft['id']}").status_code, 404)
        self.assertEqual(performer.post("/clicktrack/api/presentations", json=state).status_code, 403)
        self.assertEqual(self.app.test_client().get("/clicktrack/api/tracks").status_code, 401)

    def test_public_presentation_is_anonymous_read_only_and_revocable(self):
        from unittest.mock import patch
        import time

        now = time.time()
        marker = {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "title": "Private cue sheet", "videoId": "abcdefghijk", "markers": [marker],
        })
        self.assertEqual(draft.status_code, 201)
        state = {"videoId": "abcdefghijk", "time": 12.5, "playing": True, "blank": False,
                 "notes": "must not be published"}
        with patch("clicktrack.time.time", return_value=now):
            created = self.client.post("/clicktrack/api/presentations", json=state)
        self.assertEqual(created.status_code, 201, created.json)
        live = created.json
        public = self.app.test_client()
        with patch("clicktrack.time.time", return_value=now + 1):
            page = public.get(live["url"])
            response = public.get(live["url"] + "state")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"CLICKTRACK_PUBLIC_STATE_URL", page.data)
        self.assertNotIn(b"Private cue sheet", page.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(page.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(response.json, {
            "source": {"kind": "youtube", "id": "abcdefghijk", "key": "abcdefghijk"},
            "time": 13.5, "playing": True, "blank": False, "waiting": False, "offset": 0, "muted": True,
        })
        path = f"/clicktrack/api/presentations/{live['id']}"
        self.assertEqual(public.post("/clicktrack/api/presentations", json=state).status_code, 401)
        self.assertEqual(public.put(path, json=state).status_code, 401)
        self.assertEqual(public.delete(path).status_code, 401)
        self.assertEqual(public.put(live["url"] + "state", json=state).status_code, 405)
        with self.app.app_context():
            other = self.models["User"](username="other", email="other@example.invalid", password_hash="unused", role="admin")
            self.db.session.add(other)
            self.db.session.commit()
            other_id = other.id
        with public.session_transaction() as session:
            session["_user_id"] = str(other_id)
        self.assertEqual(public.put(path, json=state).status_code, 404)
        self.assertEqual(public.delete(path).status_code, 404)
        with patch("clicktrack.time.time", return_value=now + 2):
            self.assertEqual(self.client.put(path, json={**state, "time": 20, "playing": False}).status_code, 200)
        with patch("clicktrack.time.time", return_value=now + 4):
            self.assertEqual(public.get(live["url"] + "state").json["time"], 20)
        with patch("clicktrack.time.time", return_value=now + 3):
            self.client.put(path, json={**state, "time": 0, "blank": True})
        with patch("clicktrack.time.time", return_value=now + 4):
            blank = public.get(live["url"] + "state").json
        self.assertTrue(blank["blank"])
        self.assertFalse(blank["playing"])
        self.assertEqual(blank["time"], 0)
        with patch("clicktrack.time.time", return_value=now + 9):
            stale = public.get(live["url"] + "state").json
        self.assertTrue(stale["waiting"])
        self.assertTrue(stale["blank"])
        self.assertFalse(stale["playing"])
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(public.get(live["url"]).status_code, 404)
        self.assertEqual(public.get(live["url"] + "state").status_code, 404)

    def test_public_presentation_live_offset_only_changes_running_audience(self):
        from unittest.mock import patch
        import time

        now = time.time()
        self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [{"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}],
        })
        state = {"videoId": "abcdefghijk", "time": 20, "playing": True, "blank": False, "offset": 0.5}
        with patch("clicktrack.time.time", return_value=now):
            live = self.client.post("/clicktrack/api/presentations", json=state).json
        path = f"/clicktrack/api/presentations/{live['id']}"
        public = self.app.test_client()
        for offset, playing, blank, position, expected in (
            (0.5, True, False, 20, 21.5),
            (-0.5, True, False, 20, 20.5),
            (0.5, False, False, 20, 20),
            (0.5, True, True, 0, 0),
            (-10, True, False, 1, 0),
            (10, True, False, 86400, 86400),
        ):
            with self.subTest(offset=offset, playing=playing, blank=blank, position=position):
                with patch("clicktrack.time.time", return_value=now):
                    self.assertEqual(self.client.put(path, json={
                        **state, "offset": offset, "playing": playing, "blank": blank, "time": position,
                    }).status_code, 200)
                with patch("clicktrack.time.time", return_value=now + 1):
                    response = public.get(live["url"] + "state").json
                self.assertEqual(response["time"], expected)
                self.assertEqual(response["offset"], offset)
        with patch("clicktrack.time.time", return_value=now):
            self.client.put(path, json=state)
        with patch("clicktrack.time.time", return_value=now + 6):
            response = public.get(live["url"] + "state").json
        self.assertTrue(response["blank"])
        self.assertFalse(response["playing"])
        self.assertEqual(response["time"], 20)
        with self.app.app_context():
            saved = self.db.session.get(self.models["ClickTrackPresentation"], live["id"])
            self.assertEqual(saved.state["time"], 20)
            # Rows written before the offset feature remain readable.
            saved.state = {key: value for key, value in state.items() if key != "offset"}
            self.db.session.commit()
        with patch("clicktrack.time.time", return_value=now + 1):
            response = public.get(live["url"] + "state").json
        self.assertEqual(response["time"], 21)
        self.assertEqual(response["offset"], 0)
        with patch("clicktrack.time.time", return_value=now):
            self.assertEqual(self.client.put(path, json={**state, "audio": True}).status_code, 200)
        with patch("clicktrack.time.time", return_value=now + 1):
            self.assertFalse(public.get(live["url"] + "state").json["muted"])
        with patch("clicktrack.time.time", return_value=now):
            self.assertEqual(self.client.put(path, json={**state, "audio": False}).status_code, 200)
        with patch("clicktrack.time.time", return_value=now + 1):
            self.assertTrue(public.get(live["url"] + "state").json["muted"])

    def test_public_presentation_validates_state_and_expires(self):
        from unittest.mock import patch
        import time

        now = time.time()
        self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [{"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}],
        })
        state = {"videoId": "abcdefghijk", "time": 1, "playing": False, "blank": False}
        for invalid in (
            {"videoId": "not-a-video"}, {"videoId": "zyxwvutsrqp"}, {"time": True},
            {"time": -1}, {"time": 86401}, {"time": float("nan")}, {"time": float("inf")},
            {"playing": "yes"}, {"blank": 0},
            {"offset": True}, {"offset": "0.5"}, {"offset": None}, {"offset": 10.1},
            {"offset": -10.1}, {"offset": float("nan")}, {"offset": float("inf")},
            {"audio": "true"}, {"audio": 1}, {"audio": None},
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/clicktrack/api/presentations", json={**state, **invalid}).status_code, 400)
        with patch("clicktrack.time.time", return_value=now):
            live = self.client.post("/clicktrack/api/presentations", json=state).json
        public = self.app.test_client()
        with patch("clicktrack.time.time", return_value=now + 43201):
            self.assertEqual(public.get(live["url"]).status_code, 404)
            self.assertEqual(public.get(live["url"] + "state").status_code, 404)
            self.assertEqual(self.client.put(f"/clicktrack/api/presentations/{live['id']}", json=state).status_code, 410)
            fresh = self.client.post("/clicktrack/api/presentations", json=state)
        self.assertEqual(fresh.status_code, 201)
        self.assertNotEqual(fresh.json["id"], live["id"])

    def test_live_urls_daily_replacement_and_legacy_compatibility(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from flask import url_for

        # London is already on the following day during British Summer Time.
        now = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc).timestamp()
        self.client.post("/live/api/video-drafts", json={
            "videoId": "abcdefghijk",
            "markers": [{"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}],
        })
        state = {"videoId": "abcdefghijk", "time": 12, "playing": True, "blank": False}
        with self.app.test_request_context():
            self.assertEqual(url_for("clicktrack.clicktrack_index"), "/live/")
        for path in ("/live/", "/clicktrack/"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/live").status_code, 308)
        self.assertEqual(self.client.get("/live").headers["Location"], "http://localhost/live/")
        public = self.app.test_client()
        self.assertEqual(public.get("/live/api/tracks").status_code, 401)
        with patch("clicktrack.time.time", return_value=now):
            with self.app.app_context():
                admin_id = self.models["User"].query.filter_by(username="test").one().id
            with self.client.session_transaction() as session:
                session["_user_id"] = str(admin_id)
                session["_fresh"] = True
            first = self.client.post("/live/api/presentations", json=state)
            self.assertEqual(first.status_code, 201)
            live = first.json
            self.assertEqual(live["url"], "/live/watch/2026-07-02/")
            for path in (live["url"], "/clicktrack/watch/2026-07-02/"):
                self.assertEqual(public.get(path).status_code, 200)
                self.assertEqual(public.get(path + "state").json["time"], 12)
            conflict = self.client.post("/live/api/presentations", json=state)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json["replaceId"], live["id"])
            for bad_id in (True, live["id"] + 1):
                self.assertEqual(self.client.post("/live/api/presentations", json={
                    **state, "replaceId": bad_id,
                }).status_code, 409)
            replaced = self.client.post("/live/api/presentations", json={
                **state, "time": 20, "replaceId": live["id"],
            })
            self.assertEqual(replaced.status_code, 201)
            self.assertEqual(replaced.json["url"], live["url"])
            self.assertNotEqual(replaced.json["id"], live["id"])
            self.assertEqual(self.client.put(f"/clicktrack/api/presentations/{live['id']}", json=state).status_code, 404)
            self.assertEqual(self.client.delete(f"/live/api/presentations/{live['id']}").status_code, 404)
            self.assertEqual(public.get(live["url"] + "state").json["time"], 20)
            self.assertEqual(self.client.post("/live/api/presentations", json={
                **state, "replaceId": live["id"],
            }).status_code, 409)
            with self.app.app_context():
                self.db.session.add(self.models["ClickTrackPresentation"](
                    user_id=self.models["User"].query.filter_by(username="test").one().id,
                    token="old-random-link", state=state,
                    updated_at=now, expires_at=now + 100,
                ))
                self.db.session.commit()
            self.assertEqual(public.get("/clicktrack/watch/old-random-link/").status_code, 200)
            self.assertEqual(public.get("/clicktrack/watch/old-random-link/state").status_code, 200)
            with self.app.app_context():
                other = self.models["User"](username="second-admin", email="second@example.invalid",
                                            password_hash="unused", role="admin")
                self.db.session.add(other)
                self.db.session.commit()
                other_id = other.id
            second = self.app.test_client()
            with second.session_transaction() as session:
                session["_user_id"] = str(other_id)
            second.post("/live/api/video-drafts", json={
                "videoId": "abcdefghijk",
                "markers": [{"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady"}],
            })
            self.assertEqual(second.post("/live/api/presentations", json=state).status_code, 409)
            other_live = second.post("/live/api/presentations", json={
                **state, "replaceId": replaced.json["id"],
            })
            self.assertEqual(other_live.status_code, 201)
            self.assertEqual(other_live.json["url"], live["url"])
            self.assertEqual(self.client.delete(f"/live/api/presentations/{other_live.json['id']}").status_code, 404)
        with patch("clicktrack.time.time", return_value=now + 86400):
            tomorrow = self.client.post("/live/api/presentations", json=state)
            self.assertEqual(tomorrow.json["url"], "/live/watch/2026-07-03/")

    def test_offset_and_calculator_persist(self):
        response = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoOffset": 25.5})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["videoOffset"], 25.5)
        saved = self.client.get("/clicktrack/api/tracks").json["tracks"][0]
        self.assertEqual(saved["videoOffset"], 25.5)
        self.assertEqual(saved["sections"][0]["calcEnd"], "0:45")

    def test_negative_offset_and_video_markers_persist(self):
        response = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoOffset": -6.25})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["videoOffset"], -6.25)
        marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [
                {**marker, "time": -6.25, "bar": "1", "label": "1"},
                {**marker, "time": 0, "bar": "4", "label": "2"},
                {**marker, "time": 8, "bar": "8", "label": "3"},
            ],
        })
        self.assertEqual(draft.status_code, 201)
        self.assertEqual([m["time"] for m in draft.json["markers"]], [-6.25, 0, 8])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft.json['id']}").json, draft.json)
        missing_zero = self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [draft.json["markers"][0], draft.json["markers"][2]],
        })
        self.assertEqual(missing_zero.status_code, 400)

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
        for value in [-86401, 86401, "0:25", True, None, float("inf"), float("nan")]:
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
        self.assertIn(b'Score bars skipped before the next hit', response.data)
        self.assertIn(b'id="builder-skip-help"', response.data)

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
            self.assertIn(b'id="quick-edit-perform"', response.data)

    def test_performance_video_and_draft_actions(self):
        page = self.client.get("/clicktrack/").data
        self.assertIn(b'id="practice-enable" aria-controls="practice-video" checked', page)
        self.assertLess(page.index(b'id="btn-fullscreen"'), page.index(b'id="rehearsal-panel"'))
        for action in (b"builder-perform", b"builder-perform-save", b"builder-perform-discard",
                       b"builder-perform-preview", b"preview-return"):
            self.assertIn(b'id="' + action + b'"', page)

    def test_performance_options_are_above_stage_without_duplicate_sections_list(self):
        for path in ("/clicktrack/", "/clicktrack/offline/"):
            with self.subTest(path=path):
                page = self.client.get(path).data
                self.assertLess(page.index(b'id="performance-menu"'), page.index(b'id="stage"'))
                self.assertNotIn(b'id="setlist"', page)

    def test_rehearsal_grid_headings_available_online_and_offline(self):
        for path in ("/clicktrack/", "/clicktrack/offline/"):
            with self.subTest(path=path):
                page = self.client.get(path).data
                header = page.split(b'class="rehearsal-header', 1)[1].split(b'id="rehearsal-list"', 1)[0]
                self.assertIn(b"Hit point", header)
                self.assertIn(b"Figure", header)
                self.assertIn(b"Bar", header)

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

    def test_restore_combined_backup_creates_independent_linked_pair(self):
        marker = {"time": 0, "bar": "175", "num": "3", "den": "4", "unit": "auto",
                  "shape": "timedPause", "bpm": "94", "dialogue": "Cue", "figure": "7",
                  "visualCue": True, "skip": "2", "label": "24A"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "title": "Original draft", "videoId": "abcdefghijk", "markers": [marker],
        }).json
        original = self.client.post("/clicktrack/api/tracks", json={
            **self.payload, "videoDraftId": draft["id"], "videoOffset": 12.5,
        }).json
        backup = {"format": "jackcapstaff-clicktrack", "version": 2, **original,
                  "videoDraft": {key: draft[key] for key in ("title", "videoId", "markers")}}
        restored = self.client.post("/clicktrack/api/tracks/restore-backup", json=backup)
        self.assertEqual(restored.status_code, 201)
        self.assertNotEqual(restored.json["id"], original["id"])
        self.assertNotEqual(restored.json["videoDraftId"], draft["id"])
        self.assertEqual(restored.json["sections"], original["sections"])
        self.assertEqual(restored.json["videoOffset"], 12.5)
        restored_draft = self.client.get(f"/clicktrack/api/video-drafts/{restored.json['videoDraftId']}").json
        self.assertEqual({key: restored_draft[key] for key in ("title", "videoId", "markers")}, backup["videoDraft"])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)

    def test_restore_invalid_backup_creates_nothing(self):
        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        backup = {"format": "jackcapstaff-clicktrack", "version": 2, **self.payload,
                  "videoDraft": {"title": "Draft", "videoId": "abcdefghijk", "markers": [marker]}}
        for invalid in (
            {**backup, "videoDraft": {**backup["videoDraft"], "videoId": "invalid"}},
            {**backup, "sections": [{"type": "timedPause", "pauseDuration": 0}]},
            {**backup, "version": 3},
            {**backup, "videoDraft": None},
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/clicktrack/api/tracks/restore-backup", json=invalid).status_code, 400)
                self.assertEqual(self.client.get("/clicktrack/api/tracks").json["tracks"], [])
                self.assertEqual(self.client.get("/clicktrack/api/video-drafts").json["drafts"], [])
        self.assertEqual(self.app.test_client().post("/clicktrack/api/tracks/restore-backup", json=backup).status_code, 401)

    def test_restore_rolls_back_if_commit_fails(self):
        from unittest.mock import patch
        from sqlalchemy.exc import SQLAlchemyError

        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        backup = {"format": "jackcapstaff-clicktrack", "version": 2, **self.payload,
                  "videoDraft": {"videoId": "abcdefghijk", "markers": [marker]}}
        with patch.object(self.db.session, "commit", side_effect=SQLAlchemyError("Database failure")):
            with self.assertRaises(SQLAlchemyError):
                self.client.post("/clicktrack/api/tracks/restore-backup", json=backup)
        self.assertEqual(self.client.get("/clicktrack/api/tracks").json["tracks"], [])
        self.assertEqual(self.client.get("/clicktrack/api/video-drafts").json["drafts"], [])

    def test_create_from_draft_reuses_unlinked_and_copies_shared(self):
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "title": "Incomplete", "videoId": "abcdefghijk", "markers": [
                {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady", "label": "1"},
            ],
        }).json
        url = "/clicktrack/api/tracks/from-video-draft"
        request = {"track": self.payload, "videoDraft": {**draft, "title": "Finished"}, "expectedDraft": draft}
        stale = self.client.post(url, json={**request, "expectedDraft": {**draft, "title": "Outdated"}})
        self.assertEqual(stale.status_code, 409)
        created = self.client.post(url, json=request)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["track"]["videoDraftId"], draft["id"])
        updated = created.json["videoDraft"]
        self.assertEqual(updated["title"], "Finished")
        unlinked_copy = self.client.put(f"/clicktrack/api/video-drafts/{draft['id']}/unlinked",
                                        json={"expectedDraft": updated, "videoDraft": draft})
        self.assertEqual(unlinked_copy.status_code, 200)
        self.assertNotEqual(unlinked_copy.json["id"], draft["id"])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, updated)
        copied = self.client.post(url, json={**request, "expectedDraft": updated})
        self.assertEqual(copied.status_code, 201)
        self.assertNotEqual(copied.json["track"]["videoDraftId"], draft["id"])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, updated)

    def test_video_link_checks_track_and_draft_without_modifying_either(self):
        track = self.client.post("/clicktrack/api/tracks", json=self.payload).json
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "title": "Cues", "videoId": "abcdefghijk", "markers": [
                {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto", "shape": "steady", "label": "1"},
            ],
        }).json
        url = f"/clicktrack/api/tracks/{track['id']}/video-link"
        request = {"expectedTrack": track, "expectedDraft": draft}
        self.assertEqual(self.client.put(url, json={**request, "expectedDraft": {**draft, "title": "Old"}}).status_code, 409)
        linked = self.client.put(url, json=request)
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.json["sections"], track["sections"])
        self.assertEqual(linked.json["videoDraftId"], draft["id"])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)
        self.assertEqual(self.client.put(url, json=request).status_code, 409)

    def test_video_editor_save_copies_shared_draft_and_updates_one_track(self):
        marker = {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto",
                  "shape": "steady", "label": "1"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "title": "Shared", "videoId": "abcdefghijk", "markers": [marker],
        }).json
        path = "/clicktrack/api/tracks"
        first = self.client.post(path, json={**self.payload, "videoDraftId": draft["id"]}).json
        second = self.client.post(path, json={**self.payload, "title": "Other", "videoDraftId": draft["id"]}).json
        new_draft = {"title": "Edited", "videoId": "abcdefghijk", "markers": [
            {**marker, "num": "3", "dialogue": "New cue"}]}
        new_track = {**self.payload, "title": "Edited track", "sections": [
            {**self.payload["sections"][0], "num": 3}]}
        response = self.client.put(f"{path}/{first['id']}/video-editor", json={
            "expectedTrack": first, "expectedDraft": draft, "videoDraft": new_draft, "track": new_track,
        })
        self.assertEqual(response.status_code, 200)
        saved = response.json
        self.assertNotEqual(saved["videoDraft"]["id"], draft["id"])
        self.assertEqual(saved["track"]["videoDraftId"], saved["videoDraft"]["id"])
        self.assertEqual(saved["track"]["sections"][0]["num"], 3)
        self.assertEqual(saved["videoDraft"]["markers"][0]["dialogue"], "New cue")
        tracks = self.client.get(path).json["tracks"]
        self.assertEqual(next(t for t in tracks if t["id"] == second["id"]), second)
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)

    def test_video_editor_saves_incomplete_draft_without_changing_playable_track(self):
        track = self.client.post("/clicktrack/api/tracks", json=self.payload).json
        path = f"/clicktrack/api/tracks/{track['id']}/video-editor"
        draft = {"title": "Incomplete", "videoId": "abcdefghijk", "markers": [
            {"time": 0, "bar": "", "num": "", "den": "4", "unit": "auto", "shape": "steady"},
        ]}
        response = self.client.put(path, json={
            "expectedTrack": track, "expectedDraft": None, "videoDraft": draft, "track": None,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["track"]["sections"], track["sections"])
        self.assertEqual(response.json["videoDraft"]["markers"][0]["num"], "")
        self.assertEqual(response.json["track"]["videoDraftId"], response.json["videoDraft"]["id"])
        second = self.client.put(path, json={
            "expectedTrack": response.json["track"], "expectedDraft": response.json["videoDraft"],
            "videoDraft": {**draft, "title": "Revised"}, "track": None,
        })
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json["videoDraft"]["id"], response.json["videoDraft"]["id"])
        self.assertEqual(len(self.client.get("/clicktrack/api/video-drafts").json["drafts"]), 1)

    def test_video_editor_rejects_stale_and_invalid_saves_atomically(self):
        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [marker],
        }).json
        track = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoDraftId": draft["id"]}).json
        path = f"/clicktrack/api/tracks/{track['id']}/video-editor"
        data = {"expectedTrack": track, "expectedDraft": draft, "videoDraft": draft,
                "track": {**self.payload, "title": "Changed"}}
        for invalid in (
            {**data, "expectedDraft": {**draft, "title": "Old"}},
            {**data, "expectedTrack": {**track, "title": "Old"}},
        ):
            response = self.client.put(path, json=invalid)
            self.assertEqual(response.status_code, 409)
            self.assertIn("changed", response.json["error"])
        bad = {**data, "videoDraft": {**draft, "videoId": "invalid"}}
        self.assertEqual(self.client.put(path, json=bad).status_code, 400)
        self.assertEqual(self.client.get("/clicktrack/api/tracks").json["tracks"], [track])
        self.assertEqual(self.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)
        self.assertEqual(self.app.test_client().put(path, json=data).status_code, 401)

    def test_video_editor_rolls_back_copy_when_commit_fails(self):
        from unittest.mock import patch
        from sqlalchemy.exc import SQLAlchemyError

        marker = {"time": 0, "den": "4", "unit": "auto", "shape": "steady"}
        draft = self.client.post("/clicktrack/api/video-drafts", json={
            "videoId": "abcdefghijk", "markers": [marker],
        }).json
        track = self.client.post("/clicktrack/api/tracks", json={**self.payload, "videoDraftId": draft["id"]}).json
        self.client.post("/clicktrack/api/tracks", json={**self.payload, "title": "Shared", "videoDraftId": draft["id"]})
        data = {"expectedTrack": track, "expectedDraft": draft, "videoDraft": draft,
                "track": {**self.payload, "title": "Changed"}}
        with patch.object(self.db.session, "commit", side_effect=SQLAlchemyError("Database failure")):
            with self.assertRaises(SQLAlchemyError):
                self.client.put(f"/clicktrack/api/tracks/{track['id']}/video-editor", json=data)
        tracks = self.client.get("/clicktrack/api/tracks").json["tracks"]
        self.assertEqual(next(t for t in tracks if t["id"] == track["id"]), track)
        self.assertEqual(len(self.client.get("/clicktrack/api/video-drafts").json["drafts"]), 1)

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

    def test_timed_pause_draft_and_track_round_trip(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "bar": "175", "num": "3", "den": "4", "unit": "auto",
                  "shape": "timedPause", "bpm": "", "dialogue": "", "figure": ""}
        draft = self.client.post(path, json={"videoId": "abcdefghijk", "markers": [marker]})
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(self.client.get(f"{path}/{draft.json['id']}").json["markers"][0]["shape"], "timedPause")
        payload = {**self.payload, "videoDraftId": draft.json["id"], "sections": [
            {"type": "timedPause", "pauseDuration": 2.123, "startBar": 175, "bars": 1},
            {"type": "click", "startBar": 176, "bpm": 94},
        ]}
        saved = self.client.post("/clicktrack/api/tracks", json=payload)
        self.assertEqual(saved.status_code, 201)
        reloaded = self.client.get("/clicktrack/api/tracks").json["tracks"][0]
        self.assertEqual(reloaded["sections"][0]["type"], "timedPause")
        self.assertEqual(reloaded["sections"][0]["pauseDuration"], 2.123)
        self.assertEqual(reloaded["sections"][1]["bpm"], 94)
        for duration in (0, -1, "invalid", None):
            with self.subTest(duration=duration):
                bad = {**payload, "sections": [{"type": "timedPause", "pauseDuration": duration}]}
                self.assertEqual(self.client.post("/clicktrack/api/tracks", json=bad).status_code, 400)

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

    def test_video_draft_ramp_anchors_round_trip(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "bar": "1", "den": "4", "unit": "auto", "shape": "rall",
                  "rampAnchor": "end", "endBpm": "96.51"}
        response = self.client.post(path, json={"videoId": "abcdefghijk", "markers": [marker]})
        self.assertEqual(response.status_code, 201)
        url = f"{path}/{response.json['id']}"
        saved = self.client.get(url).json["markers"][0]
        self.assertEqual((saved["rampAnchor"], saved["endBpm"]), ("end", "96.51"))
        for anchor in ("start", "end", "previous", "next"):
            result = self.client.put(url, json={"videoId": "abcdefghijk", "markers": [{**marker, "rampAnchor": anchor}]})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["markers"][0]["rampAnchor"], anchor)
        self.assertEqual(self.client.put(url, json={"videoId": "abcdefghijk", "markers": [
            {**marker, "rampAnchor": "invalid"},
        ]}).status_code, 400)

    def test_video_draft_metric_modulation_round_trip_and_validation(self):
        path = "/clicktrack/api/video-drafts"
        marker = {"time": 0, "bar": "1", "den": "4", "unit": "auto", "shape": "rall",
                  "rampAnchor": "next", "modulationOld": "1/8:triplet", "modulationNew": "1/4:quintuplet"}
        response = self.client.post(path, json={"videoId": "abcdefghijk", "markers": [marker]})
        self.assertEqual(response.status_code, 201)
        url = f"{path}/{response.json['id']}"
        saved = self.client.get(url).json["markers"][0]
        self.assertEqual((saved["modulationOld"], saved["modulationNew"]), ("1/8:triplet", "1/4:quintuplet"))
        for value in ("1/1:straight", "1/2:dotted", "1/4:triplet", "1/8:quintuplet", "1/16:septuplet"):
            result = self.client.put(url, json={"videoId": "abcdefghijk", "markers": [{**marker, "modulationOld": value}]})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["markers"][0]["modulationOld"], value)
        for changes in ({"modulationOld": "1/32:straight"}, {"modulationNew": "1/4:wrong"},
                        {"modulationOld": None}, {"modulationNew": 1}):
            self.assertEqual(self.client.put(url, json={"videoId": "abcdefghijk",
                "markers": [{**marker, **changes}]}).status_code, 400)
        missing = dict(marker)
        del missing["modulationNew"]
        self.assertEqual(self.client.put(url, json={"videoId": "abcdefghijk", "markers": [missing]}).status_code, 400)

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
            user = self.models["User"](username="other", email="other@example.invalid", password_hash="unused", role="admin")
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
