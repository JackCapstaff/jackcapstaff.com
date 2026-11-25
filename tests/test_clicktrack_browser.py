"""Scheduler checks using Chrome when the optional Playwright package is available."""
import importlib.util
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class ClickTrackBrowserTests(unittest.TestCase):
    def test_admin_publishes_track_for_read_only_performer(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": 0, "bar": "1", "label": "1"},
                    {**marker, "time": 20, "bar": "11", "label": "2"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Public performance", "videoDraftId": draft["id"],
                "sections": [{"startBar": 1, "bars": 10, "bpm": 120, "hitPoint": "1", "countIn": 0}],
            }).json
            with fixture.app.app_context():
                user = fixture.models["User"](username="performer", email="performer@example.invalid", password_hash="unused")
                fixture.db.session.add(user)
                fixture.db.session.commit()
                user_id = user.id
            performer_client = fixture.app.test_client()
            with performer_client.session_transaction() as session:
                session["_user_id"] = str(user_id)
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin_context = browser.new_context(viewport={"width": 1440, "height": 1100})
                performer_context = browser.new_context(viewport={"width": 1440, "height": 1100})
                mock_player = """
                  window.mockPlayers = [];
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.at = performance.now();
                    this.getDuration = () => 30;
                    this.getCurrentTime = () => this.time + (this.state === 1 ? (performance.now() - this.at) / 1000 : 0);
                    this.getPlayerState = () => this.state; this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; this.at = performance.now(); };
                    this.pauseVideo = () => { this.time = this.getCurrentTime(); this.state = 2; this.at = performance.now(); };
                    this.playVideo = () => { if (this.state !== 1) { this.state = 1; this.at = performance.now(); } };
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    window.mockPlayers.push(this);
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """
                errors, denied = [], []

                def route_request(route, client):
                    request = route.request
                    parsed = urlsplit(request.url)
                    response = client.open(parsed.path, method=request.method, data=request.post_data,
                                           content_type=request.headers.get("content-type"))
                    if client is performer_client and "/api/" in parsed.path and response.status_code >= 400:
                        denied.append((parsed.path, response.status_code))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                for context in (admin_context, performer_context):
                    context.add_init_script(mock_player)
                    context.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(
                        status=200, content_type="text/html", body="<html></html>"))
                admin_context.route("http://clicktrack.test/**", lambda route: route_request(route, fixture.client))
                performer_context.route("http://clicktrack.test/**", lambda route: route_request(route, performer_client))
                admin = admin_context.new_page()
                admin.on("pageerror", lambda error: errors.append(str(error)))
                admin.goto("http://clicktrack.test/clicktrack/")
                self.assertEqual(admin.title(), "Performance Studio · Jack Capstaff")
                self.assertEqual(admin.locator(".ct-brand").text_content(), "Performance Studio")
                admin.wait_for_function("document.querySelector('#track-select').options.length === 1")
                admin.click("button[data-view=edit]")
                self.assertEqual(admin.locator("#video-builder-title").text_content(), "Edit performance track with video")
                admin.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                admin.check("#track-public")
                admin.wait_for_function("document.querySelector('#track-public-status').textContent.startsWith('Public:')")
                self.assertTrue(fixture.client.get("/clicktrack/api/tracks").json["tracks"][0]["isPublic"])
                performer = performer_context.new_page()
                performer.on("pageerror", lambda error: errors.append(str(error)))
                performer.add_init_script(
                    f"localStorage.setItem('clicktrack.tracks.u{user_id}', JSON.stringify(["
                    "{id: 'local-stale', title: 'Old cached private track', _dirty: true, sections: [{}]}]));"
                )
                performer.goto("http://clicktrack.test/clicktrack/")
                performer.wait_for_function("document.querySelector('#track-select').options.length === 1")
                performer.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertEqual(performer.locator("#track-select").input_value(), str(track["id"]))
                for selector in ('[data-view="edit"]', "#btn-quick-edit", "#btn-practice-edit-video", "#btn-tap"):
                    self.assertTrue(performer.locator(selector).is_hidden(), selector)
                performer.locator("#track-actions > summary").click()
                self.assertTrue(performer.locator("#btn-pedal-setup").is_visible())
                for selector in ("#btn-new-track", "#btn-video-track", "#btn-dup-track", "#btn-import-track", "#btn-delete-track"):
                    self.assertTrue(performer.locator(selector).is_hidden(), selector)
                performer.locator("#track-actions > summary").click()
                performer.click("#btn-play")
                performer.wait_for_function("window.mockPlayers[0].state === 1")
                performer.click("#btn-play")
                performer.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                performer.click("#presentation-share")
                performer.wait_for_function("!document.querySelector('#presentation-share-link').hidden")
                performer.click("#presentation-share-stop")
                performer.wait_for_function("document.querySelector('#presentation-share-status').textContent.includes('stopped')")
                admin.uncheck("#track-public")
                admin.wait_for_function("document.querySelector('#track-public-status').textContent.startsWith('Private:')")
                performer.reload()
                performer.wait_for_function("document.querySelector('#save-status').textContent.includes('No public tracks')")
                self.assertEqual(performer.locator("#track-select option").count(), 0)
                self.assertTrue(performer.locator("#btn-play").is_disabled())
                self.assertEqual(denied, [])
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_public_presentation_follows_conductor_without_login(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": -2, "bar": "1", "label": "1"},
                    {**marker, "time": 0, "bar": "2", "label": "2"},
                    {**marker, "time": 20, "bar": "12", "label": "3"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Private conductor notes", "videoDraftId": draft["id"], "videoOffset": -2,
                "sections": [
                    {"startBar": 1, "bars": 1, "bpm": 120, "hitPoint": "1", "countIn": 0},
                    {"startBar": 2, "bars": 10, "bpm": 120, "hitPoint": "2", "countIn": 0},
                ],
            }).json
            public_client = fixture.app.test_client()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                conductor = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience = browser.new_context()
                mock_player = """
                  window.mockPlayers = [];
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.at = performance.now(); this.muted = true;
                    this.getDuration = () => 30;
                    this.getCurrentTime = () => this.time + (this.state === 1 ? (performance.now() - this.at) / 1000 : 0);
                    this.getPlayerState = () => this.state; this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; this.at = performance.now(); };
                    this.pauseVideo = () => { this.time = this.getCurrentTime(); this.state = 2; this.at = performance.now(); };
                    this.playVideo = () => { if (this.state !== 1) { this.state = 1; this.at = performance.now(); } };
                    this.mute = () => { this.muted = true; }; this.unMute = () => { this.muted = false; };
                    this.destroy = () => {};
                    window.mockPlayers.push(this);
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """
                errors = []
                requests = []
                network = {"offline": False}

                def route_request(route, client):
                    request = route.request
                    parsed = urlsplit(request.url)
                    if client is public_client and network["offline"] and parsed.path.endswith("/state"):
                        route.abort()
                        return
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    if "/api/presentations" in path:
                        requests.append((request.method, path))
                    response = client.open(path, method=request.method, data=request.post_data,
                                           content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                for context, client in ((conductor, fixture.client), (audience, public_client)):
                    context.add_init_script(mock_player)
                    context.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(
                        status=200, content_type="text/html", body="<html><body></body></html>"))
                conductor.route("http://clicktrack.test/**", lambda route: route_request(route, fixture.client))
                audience.route("http://clicktrack.test/**", lambda route: route_request(route, public_client))
                page = conductor.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.wait_for_function("!document.querySelector('#presentation-share').disabled")
                page.bring_to_front()
                page.click("#presentation-share")
                try:
                    page.wait_for_function("!document.querySelector('#presentation-share-link').hidden", timeout=5000)
                except Error:
                    self.fail(page.locator("#presentation-share-status").text_content() + repr(errors) + repr(requests))
                url = page.locator("#presentation-share-link").get_attribute("href")
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto(url)
                self.assertIsNone(viewer.evaluate("window.opener"))
                viewer.wait_for_function("window.mockPlayers.length === 1")
                self.assertTrue(viewer.locator("#presentation-player").evaluate("(el) => el.classList.contains('blank')"))
                self.assertNotIn("Private conductor notes", viewer.content())
                self.assertEqual(public_client.get("/clicktrack/api/tracks").status_code, 401)
                page.click("#btn-play")
                viewer.wait_for_function("window.mockPlayers[0].state === 1", timeout=6000)
                self.assertFalse(viewer.locator("#presentation-player").evaluate("(el) => el.classList.contains('blank')"))
                page.evaluate("window.mockPlayers[0].seekTo(8)")
                viewer.wait_for_function("window.mockPlayers[0].getCurrentTime() >= 8 && window.mockPlayers[0].getCurrentTime() < 10")
                page.click("#btn-play")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                viewer.wait_for_function("window.mockPlayers[0].state === 2")
                page.evaluate("window.mockPlayers[0].seekTo(4)")
                viewer.wait_for_function("Math.abs(window.mockPlayers[0].getCurrentTime() - 4) < 0.1")
                viewer.click("#presentation-sound")
                viewer.wait_for_function("window.mockPlayers[0].muted === false")
                self.assertEqual(page.evaluate("window.mockPlayers[0].state"), 2)
                network["offline"] = True
                viewer.wait_for_function("document.querySelector('#presentation-message').textContent.includes('interrupted')")
                self.assertTrue(viewer.locator("#presentation-player").evaluate("(el) => el.classList.contains('blank')"))
                network["offline"] = False
                viewer.wait_for_function("!document.querySelector('#presentation-player').classList.contains('blank')")
                viewer.reload()
                viewer.wait_for_function("window.mockPlayers.length === 1 && Math.abs(window.mockPlayers[0].getCurrentTime() - 4) < 0.1")
                self.assertEqual(viewer.evaluate("window.mockPlayers[0].state"), 2)
                page.click("#presentation-share-stop")
                viewer.wait_for_function("document.querySelector('#presentation-message').textContent.includes('ended')")
                self.assertEqual(public_client.get(urlsplit(url).path).status_code, 404)
                self.assertTrue(viewer.locator("#presentation-player").evaluate("(el) => el.classList.contains('blank')"))
                page.click("#presentation-share")
                page.wait_for_function("!document.querySelector('#presentation-share-link').hidden")
                new_url = page.locator("#presentation-share-link").get_attribute("href")
                self.assertNotEqual(url, new_url)
                page.uncheck("#practice-enable")
                for _ in range(50):
                    if public_client.get(urlsplit(new_url).path).status_code == 404:
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(public_client.get(urlsplit(new_url).path).status_code, 404)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_change_seven_second_lead_and_fine_tune_video(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": -14, "bar": "1", "label": "1"},
                    {**marker, "time": -7, "bar": "2", "label": "2"},
                    {**marker, "time": 0, "bar": "3", "label": "3"},
                    {**marker, "time": 8, "bar": "4", "label": "4"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Seven seconds", "videoDraftId": draft["id"], "videoOffset": -14,
                "sections": [
                    {"startBar": 1, "bars": 1, "bpm": 34.29, "hitPoint": "1", "calcStart": "-0:14.000", "calcEnd": "-0:07.000"},
                    {"startBar": 2, "bars": 1, "bpm": 34.29, "hitPoint": "2", "calcStart": "-0:07.000", "calcEnd": "0:00.000"},
                    {"startBar": 3, "bars": 1, "bpm": 30, "hitPoint": "3", "calcStart": "0:00.000", "calcEnd": "0:08.000"},
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("""
                  window.YT = {Player: function(frame, config) {
                    this.getDuration = () => 50; this.getCurrentTime = () => 0;
                    this.getPlayerState = () => 2; this.getPlaybackRate = () => 1;
                    this.seekTo = () => {}; this.pauseVideo = () => {}; this.playVideo = () => {};
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.select_option("#builder-markers", "1")
                self.assertEqual(page.locator("#builder-span-duration").input_value(), "7")
                page.locator("#builder-span-duration").fill("6.5")
                page.click("#builder-span-apply")
                page.wait_for_function("document.querySelector('#builder-span-status').textContent.includes('updated')")

                def wait_for_offset(offset):
                    for _ in range(100):
                        saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                        if abs(saved["videoOffset"] - offset) < 0.00001:
                            return saved
                        page.wait_for_timeout(100)
                    self.fail("Video offset was not saved: " + page.locator("#save-status").text_content())

                saved = wait_for_offset(-13.5)
                markers = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]
                self.assertEqual([m["time"] for m in markers], [-13.5, -6.5, 0, 8])
                self.assertAlmostEqual(saved["sections"][0]["bpm"], 34.29)
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 36.92)
                page.locator("#builder-video-adjust").fill("0.125")
                page.click("#builder-video-earlier")
                saved = wait_for_offset(-13.375)
                markers = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]
                self.assertEqual([m["time"] for m in markers], [-13.375, -6.375, 0, 0.125, 8.125])
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 36.92)
                page.click("#builder-video-later")
                saved = wait_for_offset(-13.5)
                self.assertEqual([m["time"] for m in fixture.client.get(
                    f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]], [-13.5, -6.5, 0, 8])
                page.locator("#builder-scale").fill("110")
                page.click("#builder-scale-preview")
                self.assertIn("-0:13.500 → -0:14.850", page.locator("#builder-scale-status").text_content())
                self.assertEqual([m["time"] for m in fixture.client.get(
                    f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]], [-13.5, -6.5, 0, 8])
                page.click("#builder-scale-apply")
                saved = wait_for_offset(-14.85)
                self.assertEqual([m["time"] for m in fixture.client.get(
                    f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]], [-14.85, -7.15, 0, 8.8])
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 33.57)
                page.locator("#builder-scale").fill("90")
                page.click("#builder-scale-preview")
                self.assertIn("-0:14.850 → -0:13.365", page.locator("#builder-scale-status").text_content())
                page.click("#builder-scale-apply")
                saved = wait_for_offset(-13.365)
                self.assertEqual([m["time"] for m in fixture.client.get(
                    f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]], [-13.365, -6.435, 0, 7.92])
                page.locator("#builder-scale").fill("1000")
                page.click("#builder-scale-preview")
                self.assertIn("beyond the video", page.locator("#builder-scale-status").text_content())
                self.assertTrue(page.locator("#builder-scale-apply").is_disabled())
                page.locator("#builder-scale").fill("0")
                page.click("#builder-scale-preview")
                self.assertTrue(page.locator("#builder-scale-apply").is_disabled())
                page.reload()
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                self.assertIn("-0:13.365", page.locator("#builder-markers").inner_text())

                ramp_draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                    "videoId": "abcdefghijk", "markers": [
                        {**marker, "time": -8, "bar": "1", "label": "1", "shape": "rall", "rampAnchor": "end", "endBpm": "110"},
                        {**marker, "time": 0, "bar": "5", "label": "2", "shape": "accel", "rampAnchor": "start", "bpm": "100"},
                        {**marker, "time": 8, "bar": "9", "label": "3", "shape": "timedPause"},
                        {**marker, "time": 11, "bar": "10", "label": "4"},
                        {**marker, "time": 19, "bar": "14", "label": "5"},
                    ],
                }).json
                ramp_track = fixture.client.post("/clicktrack/api/tracks", json={
                    "title": "Ramp and pause", "videoDraftId": ramp_draft["id"], "videoOffset": -8,
                    "sections": [
                        {"startBar": 1, "bars": 4, "bpm": 130, "bpmEnd": 110, "rampMode": "linearTime", "hitPoint": "1"},
                        {"startBar": 5, "bars": 4, "bpm": 100, "bpmEnd": 140, "rampMode": "linearTime", "hitPoint": "2"},
                        {"type": "timedPause", "startBar": 9, "bars": 1, "pauseDuration": 3, "hitPoint": "3"},
                        {"startBar": 10, "bars": 4, "bpm": 120, "hitPoint": "4"},
                    ],
                }).json
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 2")
                page.select_option("#track-select", str(ramp_track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.locator("#builder-scale").fill("110")
                page.click("#builder-scale-preview")
                page.click("#builder-scale-apply")
                for _ in range(100):
                    ramp_saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == ramp_track["id"])
                    if abs(ramp_saved["videoOffset"] + 8.8) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(ramp_saved["videoOffset"], -8.8)
                markers = fixture.client.get(f"/clicktrack/api/video-drafts/{ramp_saved['videoDraftId']}").json["markers"]
                self.assertEqual([m["time"] for m in markers], [-8.8, 0, 8.8, 12.1, 20.9])
                self.assertEqual(markers[0]["endBpm"], "100")
                self.assertEqual(markers[1]["bpm"], "90.91")
                self.assertAlmostEqual(ramp_saved["sections"][0]["bpm"], 118.18)
                self.assertAlmostEqual(ramp_saved["sections"][0]["bpmEnd"], 100)
                self.assertAlmostEqual(ramp_saved["sections"][1]["bpm"], 90.91)
                self.assertAlmostEqual(ramp_saved["sections"][1]["bpmEnd"], 127.27)
                self.assertAlmostEqual(ramp_saved["sections"][2]["pauseDuration"], 3.3)
                page.locator("#builder-scale").fill("90")
                page.click("#builder-scale-preview")
                page.locator("#builder-dialogue").fill("Updated cue")
                page.locator("#builder-dialogue").dispatch_event("change")
                page.click("#builder-scale-apply")
                self.assertIn("draft changed", page.locator("#builder-scale-status").text_content())
                page.locator("#builder-scale").fill("0.001")
                page.click("#builder-scale-preview")
                self.assertTrue(page.locator("#builder-scale-apply").is_disabled())
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_ripple_hit_point_times_preserve_later_spacing_and_save(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": i * 8, "bar": str(1 + i * 4), "label": str(i + 1)}
                    for i in range(5)
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Ripple timing", "videoDraftId": draft["id"],
                "sections": [
                    {"startBar": 1 + i * 4, "bars": 4, "bpm": 120, "hitPoint": str(i + 1), "countIn": 0}
                    for i in range(4)
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("""
                  window.YT = {Player: function(frame, config) {
                    window.mockVideo = this; this.time = 0;
                    this.getDuration = () => 60; this.getCurrentTime = () => this.time;
                    this.getPlayerState = () => 2; this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; };
                    this.pauseVideo = () => {}; this.playVideo = () => {};
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    response = fixture.client.open(parsed.path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(
                    status=200, content_type="text/html", body="<html></html>"))
                page.goto("http://clicktrack.test/clicktrack/")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")

                def saved_times():
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    points = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]
                    return saved, [point["time"] for point in points]

                def wait_for_times(expected):
                    for _ in range(100):
                        saved, times = saved_times()
                        if times == expected:
                            return saved
                        page.wait_for_timeout(100)
                    self.fail(f"Expected {expected}, got {times}: " + page.locator("#save-status").text_content())

                def edit_time(value):
                    page.locator("#builder-time").fill(value)
                    page.locator("#builder-time").dispatch_event("change")

                page.select_option("#builder-markers", "0")
                self.assertTrue(page.locator("#builder-time").evaluate("(el) => el.readOnly"))
                page.select_option("#builder-markers", "2")
                self.assertEqual(page.locator("#builder-time-mode").input_value(), "single")
                edit_time("0:16.500")
                wait_for_times([0, 8, 16.5, 24, 32])
                edit_time("0:16.000")
                wait_for_times([0, 8, 16, 24, 32])
                page.select_option("#builder-time-mode", "ripple")
                edit_time("0:16.500")
                saved = wait_for_times([0, 8, 16.5, 24.5, 32.5])
                self.assertEqual(saved["sections"][0]["bpm"], 120)
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 112.94)
                self.assertEqual([s["bpm"] for s in saved["sections"][2:]], [120, 120])
                self.assertIn("0.5 seconds later", page.locator("#builder-review-status").text_content())
                edit_time("0:15.750")
                wait_for_times([0, 8, 15.75, 23.75, 31.75])
                self.assertIn("0.75 seconds earlier", page.locator("#builder-review-status").text_content())
                for invalid in ("0:07.500", "0:55.000", "0:08.001"):
                    edit_time(invalid)
                    self.assertIn("Could not ripple", page.locator("#builder-review-status").text_content())
                    self.assertEqual(page.locator("#builder-time").input_value(), "0:15.750")
                    self.assertEqual(saved_times()[1], [0, 8, 15.75, 23.75, 31.75])
                # Ripple may pass the old next point: that point moves too.
                edit_time("0:25.000")
                wait_for_times([0, 8, 25, 33, 41])
                page.click("#builder-nudge-forward")
                wait_for_times([0, 8, 25.1, 33.1, 41.1])
                page.evaluate("window.mockVideo.time = 26")
                page.click("#builder-retime")
                wait_for_times([0, 8, 26, 34, 42])
                button = page.locator(".builder-timeline-point").nth(2)
                button.focus()
                button.press("ArrowLeft")
                wait_for_times([0, 8, 25.9, 33.9, 41.9])
                button.scroll_into_view_if_needed()
                box = button.bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] / 2 + 10, box["y"] + box["height"] / 2, steps=4)
                page.mouse.up()
                page.wait_for_function("document.querySelector('#builder-time').value !== '0:25.900'")
                dragged_time = page.locator("#builder-time").input_value()
                minutes, seconds = dragged_time.split(":")
                moved = round(float(minutes) * 60 + float(seconds), 3)
                wait_for_times([0, 8, moved, round(moved + 8, 3), round(moved + 16, 3)])
                page.reload()
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.select_option("#builder-markers", "2")
                self.assertEqual(page.locator("#builder-time").input_value(), dragged_time)
                self.assertEqual(page.locator("#builder-time-mode").input_value(), "single")

                negative_draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                    "videoId": "abcdefghijk", "markers": [
                        {**marker, "time": -8, "bar": "1", "label": "1"},
                        {**marker, "time": 0, "bar": "", "label": "0"},
                        {**marker, "time": 8, "bar": "9", "label": "2"},
                        {**marker, "time": 16, "bar": "13", "label": "3"},
                    ],
                }).json
                track = fixture.client.post("/clicktrack/api/tracks", json={
                    "title": "Negative ripple", "videoDraftId": negative_draft["id"], "videoOffset": -8,
                    "sections": [
                        {"startBar": 1, "bars": 8, "bpm": 120, "hitPoint": "1"},
                        {"startBar": 9, "bars": 4, "bpm": 120, "hitPoint": "2"},
                    ],
                }).json
                page.reload()
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.select_option("#builder-markers", "0")
                page.select_option("#builder-time-mode", "ripple")
                edit_time("-0:07.500")
                self.assertEqual(wait_for_times([-7.5, 0, 8.5, 16.5])["videoOffset"], -7.5)
                edit_time("0:00.500")
                self.assertEqual(wait_for_times([0, 0.5, 16.5, 24.5])["videoOffset"], 0.5)
                edit_time("0:00.000")
                self.assertEqual(wait_for_times([0, 16, 24])["videoOffset"], 0)
                self.assertTrue(page.locator("#builder-time").evaluate("(el) => el.readOnly"))
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_performance_video_controls_follow_both_directions(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": 0, "bar": "", "label": "0"},
                    {**marker, "time": 1, "bar": "1", "label": "1"},
                    {**marker, "time": 5, "bar": "3", "label": "2"},
                    {**marker, "time": 9, "bar": "5", "label": "3"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Video transport", "videoDraftId": draft["id"], "videoOffset": 1,
                "sections": [{"startBar": 1, "bars": 2, "bpm": 120, "advance": "manual", "hitPoint": "1", "countIn": 0},
                             {"startBar": 3, "bars": 2, "bpm": 120, "hitPoint": "2", "countIn": 0},
                             {"type": "timedPause", "startBar": 5, "bars": 1, "pauseDuration": 4},
                             {"startBar": 6, "bars": 2, "bpm": 120, "bpmEnd": 60, "click": "sub2"}],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context()
                context.add_init_script("""
                  window.mockPlayers = [];
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.at = performance.now(); this.seeks = [];
                    this.getDuration = () => 20;
                    this.getCurrentTime = () => this.time + (this.state === 1 ? (performance.now() - this.at) / 1000 : 0);
                    this.getPlayerState = () => this.state;
                    this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; this.at = performance.now(); this.seeks.push(time); };
                    this.pauseVideo = () => { this.time = this.getCurrentTime(); this.state = 2; this.at = performance.now(); };
                    this.playVideo = () => { if (this.state !== 1) { this.state = 1; this.at = performance.now(); } };
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    window.mockPlayers.push(this);
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                context.route("http://clicktrack.test/**", route_request)
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.wait_for_function("!document.querySelector('#presentation-open').disabled")
                with page.expect_popup() as popup_info:
                    page.click("#presentation-open")
                popup = popup_info.value
                page.wait_for_function("document.querySelector('#presentation-status').textContent.includes('Presentation ready')")
                page.wait_for_timeout(1100)
                page.evaluate("window.mockPlayers[0].seekTo(0)")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                page.wait_for_timeout(1100)
                page.evaluate("window.mockPlayers[0].playVideo()")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Film intro')")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '1'")
                page.wait_for_timeout(1100)
                page.evaluate("window.mockPlayers[0].pauseVideo()")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                popup.wait_for_function("window.mockPlayers[0].state === 2")
                page.evaluate("window.mockPlayers[0].seekTo(6.3)")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '3'")
                self.assertIn("Paused", page.locator("#d-clock-status").text_content())
                self.assertIn("estimated", page.locator("#practice-status").text_content())
                popup.wait_for_function("Math.abs(window.mockPlayers[0].getCurrentTime() - 6.3) < 0.3")
                page.evaluate("window.mockPlayers[0].seekTo(6.7)")
                popup.wait_for_function("Math.abs(window.mockPlayers[0].getCurrentTime() - 6.7) < 0.15")
                self.assertEqual(page.locator("#d-beats .on").get_attribute("data-u"), "3")
                page.evaluate("window.mockPlayers[0].playVideo()")
                page.wait_for_function("!document.querySelector('#d-clock-status').textContent.includes('Paused')")
                popup.wait_for_function("window.mockPlayers[0].state === 1")
                page.evaluate("window.mockPlayers[0].seekTo(1.2)")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '1'")
                self.assertNotIn("Paused", page.locator("#d-clock-status").text_content())
                page.evaluate("window.mockPlayers[0].pauseVideo()")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                page.evaluate("window.mockPlayers[0].seekTo(10)")
                try:
                    page.wait_for_function("document.querySelector('#d-barof').textContent.includes('timed pause')", timeout=5000)
                except Error:
                    self.fail(repr(errors) + repr(page.evaluate("""() => ({
                        status: document.querySelector('#practice-status').textContent,
                        clock: document.querySelector('#d-clock-status').textContent,
                        bar: document.querySelector('#d-barof').textContent,
                        player: window.mockPlayers[0].getCurrentTime(),
                        state: window.mockPlayers[0].state,
                        seeks: window.mockPlayers[0].seeks
                    })""")))
                self.assertIn("silent", page.locator("#d-fermata-label").text_content())
                self.assertTrue(page.locator("#d-countin").is_hidden())
                page.evaluate("window.mockPlayers[0].seekTo(11.25)")
                page.wait_for_function("!document.querySelector('#d-countin').hidden")
                page.evaluate("window.mockPlayers[0].seekTo(13.1)")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '6'")
                self.assertEqual(page.locator("#d-beats .on").get_attribute("data-u"), "0")
                page.evaluate("window.mockPlayers[0].seekTo(8.9)")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '4'")
                page.evaluate("window.mockPlayers[0].playVideo()")
                page.wait_for_function("document.querySelector('#d-barof').textContent.includes('timed pause')", timeout=1600)
                page.evaluate("window.mockPlayers[0].pauseVideo()")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                page.evaluate("window.mockPlayers[0].seekTo(40)")
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('outside the planned music')")
                context.close()
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_replace_unlinked_draft_then_link_for_performance(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Original", "videoId": "abcdefghijk",
                "markers": [{**marker, "time": 0, "bar": "1", "label": "1"},
                            {**marker, "time": 8, "bar": "5", "label": "2"}],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Unlinked", "sections": [
                    {"startBar": 1, "bars": 4, "bpm": 120, "hitPoint": "1"},
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context()
                context.add_init_script("""
                  window.YT = {Player: function(frame, config) {
                    this.getDuration = () => 20; this.getCurrentTime = () => 0;
                    this.getPlayerState = () => 2; this.getPlaybackRate = () => 1;
                    this.seekTo = () => {}; this.pauseVideo = () => {}; this.playVideo = () => {};
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                context.route("http://clicktrack.test/**", route_request)
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                self.assertTrue(page.locator("#practice-no-video").is_visible())
                page.click("#btn-practice-edit-video")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Open your saved')")
                page.locator("#builder-legacy-drafts > summary").click()
                page.select_option("#builder-drafts", str(draft["id"]))
                page.click("#builder-open-draft")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened saved draft')")
                page.wait_for_function("!document.querySelector('#builder-add').disabled")
                page.locator("#builder-youtube-url").fill("https://youtu.be/zyxwvutsrqp")
                page.click("#builder-replace-youtube")
                self.assertIn("not saved or linked", page.locator("#builder-draft-status").text_content())
                page.click("#builder-link-draft")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if saved["videoDraftId"]:
                        saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                        if saved_draft["videoId"] == "zyxwvutsrqp":
                            break
                    page.wait_for_timeout(100)
                self.assertEqual(saved_draft["videoId"], "zyxwvutsrqp")
                self.assertEqual([m["bar"] for m in saved_draft["markers"]], ["1", "5"])
                page.click("button[data-view=perform]")
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertTrue(page.locator("#practice-no-video").is_hidden())
                self.assertIn("zyxwvutsrqp", page.locator("#practice-player iframe").get_attribute("src"))
                page.reload()
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertIn("zyxwvutsrqp", page.locator("#practice-player iframe").get_attribute("src"))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.wait_for_function("!document.querySelector('#builder-add').disabled")
                page.locator("#builder-youtube-url").fill("https://youtu.be/123456789ab")
                page.click("#builder-replace-youtube")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                    if saved_draft["videoId"] == "123456789ab":
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(saved_draft["videoId"], "123456789ab")
                page.click("button[data-view=perform]")
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertIn("123456789ab", page.locator("#practice-player iframe").get_attribute("src"))
                page.wait_for_function("document.querySelector('#save-status').textContent.includes('Saved')")
                page.locator("#track-actions summary").click()
                page.click("#btn-dup-track")
                for _ in range(100):
                    copies = [t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                              if t["title"] == "Unlinked (copy)"]
                    if copies:
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0]["videoDraftId"], saved["videoDraftId"])
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertIn("123456789ab", page.locator("#practice-player iframe").get_attribute("src"))
                page.reload()
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertIn("123456789ab", page.locator("#practice-player iframe").get_attribute("src"))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.locator("#builder-youtube-url").fill("https://youtu.be/zyxwvutsrqp")
                page.click("#builder-replace-youtube")
                for _ in range(100):
                    copied = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                  if t["id"] == copies[0]["id"])
                    if copied["videoDraftId"] != saved["videoDraftId"]:
                        break
                    page.wait_for_timeout(100)
                self.assertNotEqual(copied["videoDraftId"], saved["videoDraftId"])
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{copied['videoDraftId']}").json["videoId"], "zyxwvutsrqp")
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["videoId"], "123456789ab")
                page.click("button[data-view=perform]")
                page.wait_for_function("document.querySelector('#practice-status').textContent.includes('Ready')")
                self.assertIn("zyxwvutsrqp", page.locator("#practice-player iframe").get_attribute("src"))
                context.close()
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_snap_video_start_after_music_and_countin(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Lead-in", "videoId": "abcdefghijk",
                "markers": [{**marker, "time": 0, "bar": "", "label": "0"},
                            {**marker, "time": 1, "bar": "1", "label": "1"},
                            {**marker, "time": 2.2, "bar": "2", "label": "2", "num": "6", "den": "8"},
                            {**marker, "time": 2.8, "bar": "4", "label": "3"},
                            {**marker, "time": 4, "bar": "6", "label": "4"}],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Lead-in", "videoDraftId": draft["id"], "videoOffset": 1,
                "sections": [{"startBar": 1, "bars": 1, "bpm": 200, "hitPoint": "1",
                              "calcStart": "0:01.000", "calcEnd": "0:02.200", "countIn": 1, "click": "sub2"},
                             {"startBar": 2, "bars": 2, "bpm": 400, "hitPoint": "2", "num": 6, "den": 8,
                              "calcStart": "0:02.200", "calcEnd": "0:02.800"},
                             {"startBar": 4, "bars": 2, "bpm": 400, "hitPoint": "3",
                              "calcStart": "0:02.800", "calcEnd": "0:04.000"}],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context()
                context.add_init_script("""
                  window.mockPlayers = [];
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.seeks = [];
                    this.getDuration = () => 20;
                    this.getCurrentTime = () => this.time;
                    this.getPlayerState = () => this.state;
                    this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; this.seeks.push(time); };
                    this.pauseVideo = () => { this.state = 2; };
                    this.playVideo = () => { this.state = 1; };
                    this.mute = () => {}; this.unMute = () => {}; this.destroy = () => {};
                    window.mockPlayers.push(this);
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                context.route("http://clicktrack.test/**", route_request)
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-markers').options.length === 5")
                page.select_option("#builder-markers", "3")
                page.click("#builder-snap-video")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.8) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.8)
                self.assertEqual(saved["sections"][0]["click"], "sub2")
                self.assertEqual(saved["sections"][0]["countIn"], 1)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual([m["time"] for m in saved_draft["markers"]], [-1.8, -0.6, 0, 1.2])
                self.assertEqual([m["bar"] for m in saved_draft["markers"]], ["1", "2", "4", "6"])
                page.check("#builder-click-preview")
                page.select_option("#builder-markers", "0")
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-metro-barof').textContent === 'count-in'")
                page.wait_for_function("document.querySelector('#builder-review-status').textContent.includes('Paused just before hit point 2')", timeout=7000)
                self.assertEqual(page.evaluate("window.mockPlayers.at(-1).state"), 2)
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-review-status').textContent.includes('Paused just before hit point 3')", timeout=4000)
                self.assertEqual(page.evaluate("window.mockPlayers.at(-1).state"), 2)
                page.click("#builder-play")
                page.wait_for_function("window.mockPlayers.at(-1).state === 1", timeout=4000)
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.wait_for_function("!document.querySelector('#presentation-open').disabled")
                with page.expect_popup() as popup_info:
                    page.click("#presentation-open")
                popup = popup_info.value
                page.wait_for_function("document.querySelector('#presentation-status').textContent.includes('Presentation ready')")
                page.click("#practice-from-start")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Count-in')")
                self.assertEqual(page.evaluate("window.mockPlayers[0].state"), 2)
                popup.wait_for_function("document.querySelector('#presentation-player').classList.contains('blank')")
                page.click("#btn-play")
                page.wait_for_function("document.querySelector('#d-clock-status').textContent.includes('Paused')")
                page.wait_for_timeout(400)
                self.assertEqual(page.evaluate("window.mockPlayers[0].state"), 2)
                self.assertEqual(popup.evaluate("window.mockPlayers[0].state"), 2)
                page.click("#btn-play")
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '2'")
                self.assertEqual(page.evaluate("window.mockPlayers[0].state"), 2)
                self.assertEqual(popup.evaluate("window.mockPlayers[0].state"), 2)
                page.wait_for_function("window.mockPlayers[0].state === 1", timeout=6000)
                popup.wait_for_function("window.mockPlayers[0].state === 1", timeout=3000)
                popup.wait_for_function("!document.querySelector('#presentation-player').classList.contains('blank')")
                self.assertEqual(page.locator("#d-bar").text_content().strip(), "4")
                page.click("#btn-stop")
                page.locator("#rehearsal-panel > summary").click()
                page.locator("#rehearsal-bar").fill("3")
                page.locator("#rehearsal-bar-form button[type=submit]").click()
                page.select_option("#rehearsal-count-in", "0")
                page.locator("#rehearsal-form button[type=submit]").click()
                page.wait_for_function("document.querySelector('#d-bar').textContent.trim() === '3'")
                popup.wait_for_function("document.querySelector('#presentation-player').classList.contains('blank')")
                page.wait_for_function("window.mockPlayers[0].state === 1", timeout=4000)
                popup.wait_for_function("!document.querySelector('#presentation-player').classList.contains('blank')")
                page.click("#btn-stop")
                page.locator("#rehearsal-list .rehearsal-point").nth(2).click()
                page.select_option("#rehearsal-count-in", "0")
                page.locator("#rehearsal-form button[type=submit]").click()
                page.wait_for_function("window.mockPlayers[0].state === 1")
                self.assertEqual(page.locator("#d-bar").text_content().strip(), "4")
                page.click("#btn-stop")
                page.click("button[data-view=edit]")
                page.wait_for_function("!document.querySelector('#view-video').hidden")
                page.select_option("#builder-markers", "0")
                page.click("#builder-nudge-back")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.9) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.9)
                page.locator("#builder-shift").fill("0.3")
                page.click("#builder-shift-preview")
                page.click("#builder-shift-apply")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.6) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.6)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual([m["time"] for m in saved_draft["markers"]], [-1.6, -0.3, 0, 0.3, 1.5])
                self.assertEqual([s["hitPoint"] for s in saved["sections"]], ["1", "2", "3"])
                self.assertEqual(saved["sections"][0]["click"], "sub2")
                page.select_option("#builder-markers", "0")
                self.assertFalse(page.locator("#builder-skip").is_disabled())
                with page.expect_download() as downloaded:
                    page.click("#builder-export-csv")
                import csv
                with open(downloaded.value.path(), encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(rows[0]["time"], "-1.6")
                page.check("#builder-click-preview")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('ready')")
                page.select_option("#builder-markers", "0")
                self.assertEqual(page.locator("#builder-play").text_content(), "Start from hit point")
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-play').textContent === 'Pause'")
                self.assertEqual(page.evaluate("window.mockPlayers.at(-1).state"), 2)
                page.click("#builder-play")
                self.assertEqual(page.locator("#builder-play").text_content(), "Resume")
                page.wait_for_timeout(300)
                self.assertEqual(page.evaluate("window.mockPlayers.at(-1).state"), 2)
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-review-status').textContent.includes('Paused just before hit point 2')", timeout=6000)
                self.assertEqual(page.evaluate("window.mockPlayers.at(-1).state"), 2)
                page.click("#builder-play")
                page.wait_for_function("window.mockPlayers.at(-1).state === 1", timeout=4000)
                page.click("#builder-play")
                page.select_option("#builder-markers", "1")
                self.assertEqual(page.locator("#builder-span-duration").input_value(), "0.6")
                page.locator("#builder-span-duration").fill("0.1")
                page.click("#builder-span-apply")
                self.assertIn("outside the 10–400 BPM range", page.locator("#builder-span-status").text_content())
                self.assertEqual([m["time"] for m in fixture.client.get(
                    f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"]], [-1.6, -0.3, 0, 0.3, 1.5])
                page.locator("#builder-span-duration").fill("0.65")
                page.click("#builder-span-apply")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.65) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.65)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual([m["time"] for m in saved_draft["markers"]], [-1.65, -0.35, 0, 0.3, 1.5])
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 369.23, places=2)
                page.locator("#builder-span-duration").fill("0.62")
                page.click("#builder-span-apply")
                page.wait_for_function("document.querySelector('#builder-span-status').textContent.includes('updated')")
                page.locator("#builder-video-adjust").fill("0.1")
                page.click("#builder-video-earlier")
                page.wait_for_function("document.querySelector('#builder-shift-status').textContent.includes('earlier')")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.52) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.52)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual([m["time"] for m in saved_draft["markers"]], [-1.52, -0.22, 0, 0.4, 1.6])
                self.assertAlmostEqual(saved["sections"][1]["bpm"], 387.1, places=2)
                page.click("#builder-video-later")
                for _ in range(100):
                    saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"] if t["id"] == track["id"])
                    if abs(saved["videoOffset"] + 1.62) < 0.00001:
                        break
                    page.wait_for_timeout(100)
                self.assertAlmostEqual(saved["videoOffset"], -1.62)
                page.reload()
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                self.assertIn("-0:01.620", page.locator("#builder-markers").inner_text())
                context.close()
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_presentation_window_and_performance_layout(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Presentation", "videoId": "abcdefghijk",
                "markers": [{**marker, "time": 0, "bar": "1", "label": "1"},
                            {**marker, "time": 8, "bar": "5", "label": "2"}],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Presentation", "videoDraftId": draft["id"],
                "sections": [{"startBar": 1, "bars": 4, "bpm": 120, "hitPoint": "1"}],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context(viewport={"width": 1280, "height": 720})
                context.add_init_script("""
                  window.mockPlayers = [];
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.muted = true; this.seeks = [];
                    this.getDuration = () => 20;
                    this.getCurrentTime = () => this.time;
                    this.getPlayerState = () => this.state;
                    this.getPlaybackRate = () => 1;
                    this.seekTo = time => { this.time = time; this.seeks.push(time); };
                    this.pauseVideo = () => { this.state = 2; };
                    this.playVideo = () => { this.state = 1; };
                    this.mute = () => { this.muted = true; };
                    this.unMute = () => { this.muted = false; };
                    this.destroy = () => {};
                    window.mockPlayers.push(this);
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                context.route("http://clicktrack.test/**", route_request)
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                page = context.new_page()
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.wait_for_function("!document.querySelector('#presentation-open').disabled")
                for width, height in ((1280, 720), (375, 812)):
                    page.set_viewport_size({"width": width, "height": height})
                    for fullscreen in (False, True):
                        page.evaluate("(on) => document.body.classList.toggle('performance-mode', on)", fullscreen)
                        page.evaluate("document.getElementById('rehearsal-panel').open = false")
                        self.assertTrue(page.locator("#rehearsal-panel > summary").is_visible())
                        self.assertEqual(page.locator("#rehearsal-panel").evaluate("(el) => getComputedStyle(el).position"), "fixed")
                        page.locator("#rehearsal-panel > summary").click()
                        page.wait_for_function("document.getElementById('rehearsal-panel').open")
                        self.assertTrue(page.locator("#rehearsal-bar").is_visible(),
                                        f"width={width} fullscreen={fullscreen} " + page.locator("#rehearsal-panel").evaluate("(el) => el.outerHTML.slice(0,200)"))
                        page.wait_for_timeout(200)
                        page.locator("#rehearsal-panel > summary").click()
                        page.wait_for_function("!document.getElementById('rehearsal-panel').open")
                        sizes = page.evaluate("""() => ['d-bar','d-meter'].map(id =>
                          parseFloat(getComputedStyle(document.getElementById(id)).fontSize))""")
                        self.assertGreater(sizes[0], sizes[1])
                page.evaluate("document.body.classList.remove('performance-mode')")
                page.set_viewport_size({"width": 1280, "height": 720})
                with page.expect_popup() as popup_info:
                    page.click("#presentation-open")
                popup = popup_info.value
                page.wait_for_function("document.querySelector('#presentation-status').textContent.includes('Presentation ready')")
                self.assertEqual(popup.locator("#presentation-player iframe").count(), 1)
                self.assertEqual(popup.locator("#practice-visual-cues").count(), 0)
                page.check("#practice-sound")
                page.wait_for_function("window.mockPlayers[0].muted === false")
                page.select_option("#presentation-audio", "audience")
                page.wait_for_function("window.mockPlayers[0].muted === true")
                popup.wait_for_function("window.mockPlayers[0].muted === false")
                page.click("#btn-play")
                popup.wait_for_function("window.mockPlayers[0].state === 1")
                page.click("#btn-play")
                popup.wait_for_function("window.mockPlayers[0].state === 2")
                page.click("#practice-resync")
                popup.wait_for_function("window.mockPlayers[0].seeks.length >= 2")
                popup.close()
                page.wait_for_function("document.querySelector('#presentation-status').textContent.includes('closed')")
                page.wait_for_function("window.mockPlayers[0].muted === false")
                with page.expect_popup() as popup_info:
                    page.click("#presentation-open")
                popup = popup_info.value
                page.wait_for_function("document.querySelector('#presentation-status').textContent.includes('Presentation ready')")
                with popup.expect_event("close"):
                    page.uncheck("#practice-enable")
                context.close()
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_bar_change_updates_both_adjacent_sections(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Bars", "videoId": "abcdefghijk",
                "markers": [{**marker, "time": time, "bar": str(bar), "label": str(label)}
                            for time, bar, label in [(0, 1, 1), (4, 3, 2), (8, 5, 3)]],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Bars", "videoDraftId": draft["id"], "sections": [
                    {"startBar": 1, "bars": 2, "bpm": 120, "hitPoint": "1",
                     "calcStart": "0:00.000", "calcEnd": "0:04.000", "name": "First", "click": "sub2"},
                    {"startBar": 3, "bars": 2, "bpm": 120, "hitPoint": "2",
                     "calcStart": "0:04.000", "calcEnd": "0:08.000", "name": "Second", "countIn": 2},
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("window.YT = {Player: function () { this.destroy = () => {}; }};")

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                dialogs = []
                page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                page.select_option("#builder-markers", "1")
                page.locator("#builder-bar").fill("2")
                page.locator("#builder-bar").dispatch_event("change")
                for _ in range(80):
                    saved = fixture.client.get("/clicktrack/api/tracks").json["tracks"][0]
                    if saved["sections"][1]["startBar"] == 2:
                        break
                    page.wait_for_timeout(100)
                self.assertEqual([(s["startBar"], s["bars"], s["bpm"]) for s in saved["sections"]],
                                 [(1, 1, 60), (2, 3, 180)])
                self.assertEqual([s["name"] for s in saved["sections"]], ["First", "Second"])
                self.assertEqual(saved["sections"][0]["click"], "sub2")
                self.assertEqual(saved["sections"][1]["countIn"], 2)
                self.assertFalse(dialogs)
                self.assertEqual(page.locator('#sections [data-f="startBar"]').nth(1).input_value(), "2")
                page.locator("#builder-bar").blur()
                page.wait_for_function("document.querySelector('#builder-selected-section-editor [data-f=startBar]')?.value === '2'")
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual(saved_draft["markers"][1]["bar"], "2")
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_hit_point_list_follows_playback_without_interrupting_edits(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "steady"}
            markers = [{**marker, "time": i * 2, "bar": str(i + 1), "label": str(i + 1)}
                       for i in range(13)]
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Cues", "videoId": "abcdefghijk", "markers": markers,
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Following", "videoDraftId": draft["id"], "sections": [
                    {"startBar": i + 1, "bars": 1, "bpm": 120, "hitPoint": str(i + 1)}
                    for i in range(12)
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("""
                  window.mockTime = 0; window.mockState = 2;
                  window.YT = {Player: function(frame, config) {
                    this.getDuration = () => 40;
                    this.getCurrentTime = () => window.mockTime;
                    this.getPlayerState = () => window.mockState;
                    this.getPlaybackRate = () => 1;
                    this.seekTo = time => { window.mockTime = time; };
                    this.pauseVideo = () => { window.mockState = 2; };
                    this.playVideo = () => { window.mockState = 1; };
                    this.mute = () => {};
                    this.unMute = () => {};
                    this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.wait_for_function("!document.querySelector('#builder-play').disabled")
                page.uncheck("#builder-review")
                page.click("#builder-play")
                page.evaluate("window.mockTime = 16.1")
                page.wait_for_function("document.querySelector('#builder-markers').value === '8'")
                self.assertGreater(page.locator("#builder-markers").evaluate("(el) => el.scrollTop"), 0)
                self.assertIn("Hit point 9", page.locator("#builder-marker-title").text_content())
                self.assertEqual(page.locator('#builder-selected-section-editor [data-f="hitPoint"]').input_value(), "9")
                page.locator("#builder-dialogue").fill("Unsaved input")
                page.evaluate("window.mockTime = 18.1")
                page.wait_for_timeout(200)
                self.assertEqual(page.locator("#builder-markers").input_value(), "8")
                self.assertEqual(page.locator("#builder-dialogue").input_value(), "Unsaved input")
                page.locator("#builder-dialogue").blur()
                page.wait_for_function("document.querySelector('#builder-markers').value === '9'")
                page.evaluate("window.mockTime = 4.1")
                page.wait_for_function("document.querySelector('#builder-markers').value === '2'")
                page.click("#builder-play")
                page.evaluate("window.mockTime = 20.1")
                page.wait_for_timeout(200)
                self.assertEqual(page.locator("#builder-markers").input_value(), "2")
                page.evaluate("window.mockTime = 4.1")
                page.check("#builder-review")
                page.click("#builder-play")
                page.locator("#builder-dialogue").fill("Edit before pause")
                page.evaluate("window.mockTime = 5.89")
                page.wait_for_function("window.mockState === 2 && document.querySelector('#builder-markers').value === '3'")
                self.assertIn("Paused just before hit point 4", page.locator("#builder-review-status").text_content())
                page.select_option("#builder-markers", "2")
                self.assertEqual(page.locator("#builder-dialogue").input_value(), "Edit before pause")
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_ramp_meter_anchor_and_hit_point_labels(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "shape": "rall", "bpm": "96.51", "label": "2"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Ramp", "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": 0, "bar": "21"},
                    {**marker, "time": 2.953, "bar": "22", "label": "2A", "shape": "steady", "bpm": ""},
                    {**marker, "time": 4.953, "bar": "23", "label": "3", "shape": "steady", "bpm": ""},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Ramp track", "videoDraftId": draft["id"], "sections": [
                    {"startBar": 21, "bars": 1, "num": 4, "den": 4, "bpm": 96.51,
                     "bpmEnd": 66.04, "rampMode": "linearTime", "hitPoint": "2"},
                    {"startBar": 22, "bars": 1, "num": 4, "den": 4, "bpm": 120, "hitPoint": "2A"},
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("window.YT = {Player: function () { this.destroy = () => {}; }};")
                decisions = {"accept": False, "prompts": []}

                def dialog_handler(dialog):
                    decisions["prompts"].append(dialog.message)
                    dialog.accept() if decisions["accept"] else dialog.dismiss()

                page.on("dialog", dialog_handler)

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                def wait_saved(predicate):
                    for _ in range(100):
                        saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                     if t["id"] == track["id"])
                        if predicate(saved):
                            return saved
                        page.wait_for_timeout(100)
                    self.fail("Track did not save: " + page.locator("#save-status").text_content())

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                self.assertEqual(page.locator("#sections .sec > .num").all_text_contents(), ["2", "2A"])
                self.assertIn("Hit point 2A", page.locator("#editor-jump-section option").nth(2).text_content())
                page.select_option("#editor-jump-section", "1")
                page.click("#editor-jump-button")
                self.assertEqual(page.evaluate("document.activeElement.dataset.i"), "1")
                self.assertEqual(page.evaluate("document.activeElement.dataset.f"), "name")
                self.assertTrue(page.locator("#view-perform").is_hidden())
                self.assertEqual(page.locator("#builder-review").evaluate("(el) => el.closest('#builder-transport') !== null"), True)
                page.select_option("#builder-rampAnchor", "end")
                page.locator("#builder-endBpm").fill("96.51")
                page.locator("#builder-endBpm").dispatch_event("change")
                page.locator("#builder-num").fill("5")
                page.locator("#builder-num").dispatch_event("change")
                saved = wait_saved(lambda t: t["sections"][0]["num"] == 5)
                self.assertEqual((saved["sections"][0]["bpm"], saved["sections"][0]["bpmEnd"]), (106.67, 96.51))
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual(saved_draft["markers"][0]["rampAnchor"], "end")
                self.assertEqual(saved_draft["markers"][0]["endBpm"], "96.51")
                page.select_option("#builder-rampAnchor", "start")
                page.wait_for_function("document.querySelector('#save-status').textContent.includes('playable track was not updated')")
                self.assertTrue(any("accelerando" in p for p in decisions["prompts"]))
                self.assertEqual(wait_saved(lambda t: t["sections"][0]["num"] == 5)["sections"][0]["bpm"], 106.67)
                decisions["accept"] = True
                page.locator("#builder-num").fill("6")
                page.locator("#builder-num").dispatch_event("change")
                saved = wait_saved(lambda t: t["sections"][0]["num"] == 6)
                self.assertEqual(saved["sections"][0]["bpm"], 96.51)
                self.assertGreater(saved["sections"][0]["bpmEnd"], 96.51)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual(saved_draft["markers"][0]["shape"], "accel")
                page.locator('#sections [data-i="0"][data-f="num"]').fill("5")
                page.locator('#sections [data-i="0"][data-f="num"]').dispatch_event("change")
                saved = wait_saved(lambda t: t["sections"][0]["num"] == 5)
                self.assertEqual((saved["sections"][0]["bpm"], saved["sections"][0]["bpmEnd"]), (96.51, 106.67))
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual(saved_draft["markers"][0]["num"], "5")
                page.select_option("#builder-rampAnchor", "next")
                saved = wait_saved(lambda t: t["sections"][0]["bpmEnd"] == 120)
                self.assertEqual(saved["sections"][0]["bpm"], 83.18)
                self.assertTrue(page.locator("#builder-modulation-enabled").is_visible())
                page.check("#builder-modulation-enabled")
                page.select_option("#builder-modulationOld", "1/8:straight")
                saved = wait_saved(lambda t: t["sections"][0]["bpmEnd"] == 60)
                self.assertEqual(saved["sections"][0]["bpm"], 143.18)
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertEqual((saved_draft["markers"][0]["modulationOld"], saved_draft["markers"][0]["modulationNew"]),
                                 ("1/8:straight", "1/4:straight"))
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                self.assertEqual(page.locator("#builder-rampAnchor").input_value(), "next")
                self.assertTrue(page.locator("#builder-modulation-enabled").is_checked())
                self.assertEqual(page.locator("#builder-modulationOld").input_value(), "1/8:straight")
                self.assertEqual(page.locator("#builder-modulationNew").input_value(), "1/4:straight")
                with page.expect_download() as downloaded:
                    page.click("#builder-export-csv")
                import csv
                with open(downloaded.value.path(), encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual((rows[0]["modulationOld"], rows[0]["modulationNew"]),
                                 ("1/8:straight", "1/4:straight"))
                page.click("button[data-view=perform]")
                self.assertEqual(page.locator("#d-secnum").text_content(), "Hit point 2")
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.uncheck("#builder-modulation-enabled")
                saved = wait_saved(lambda t: t["sections"][0]["bpmEnd"] == 120)
                restored_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json
                self.assertNotIn("modulationOld", restored_draft["markers"][0])
                self.assertNotIn("modulationNew", restored_draft["markers"][0])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_timed_pause_subdivisions_save_in_linked_and_quick_editors(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"num": "4", "den": "4", "unit": "auto", "label": "1"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Pause cues", "videoId": "abcdefghijk", "markers": [
                    {**marker, "time": 0, "bar": "1", "shape": "timedPause"},
                    {**marker, "time": 3, "bar": "2", "shape": "steady", "label": "2"},
                    {**marker, "time": 5, "bar": "3", "shape": "steady", "label": "3"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Pause track", "videoDraftId": draft["id"], "sections": [
                    {"type": "timedPause", "pauseDuration": 3, "startBar": 1, "bars": 1, "hitPoint": "1"},
                    {"type": "click", "startBar": 2, "bars": 1, "bpm": 120, "hitPoint": "2"},
                ],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("window.YT = {Player: function () { this.destroy = () => {}; }};")

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                def wait_for_subdivision(value):
                    for _ in range(80):
                        saved = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                     if t["id"] == track["id"])
                        if saved["sections"][0]["pauseSubdivision"] == value:
                            return saved
                        page.wait_for_timeout(100)
                    self.fail("Timed-pause subdivision did not save: " + page.locator("#save-status").text_content())

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                page.wait_for_function("document.querySelector('#builder-selected-section-editor [data-f=pauseSubdivision]') !== null")
                page.select_option('#builder-selected-section-editor [data-i="0"][data-f="pauseSubdivision"]', "3")
                self.assertEqual(page.locator('#sections [data-i="0"][data-f="pauseSubdivision"]').input_value(), "3")
                saved = wait_for_subdivision(3)
                self.assertEqual(saved["sections"][0]["pauseDuration"], 3)
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.click("#btn-quick-edit")
                control = page.locator('#quick-edit-section [data-f="pauseSubdivision"]')
                self.assertEqual(control.input_value(), "3")
                control.select_option("2")
                wait_for_subdivision(2)
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_new_video_draft_recovers_after_save_failure_and_creates_track(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                page.add_init_script("""
                  window.mockTime = 0; window.mockState = 2;
                  window.YT = {Player: function(frame, config) {
                    this.getDuration = () => 120;
                    this.getCurrentTime = () => window.mockTime;
                    this.getPlayerState = () => window.mockState;
                    this.getPlaybackRate = () => 1;
                    this.seekTo = time => { window.mockTime = time; };
                    this.pauseVideo = () => { window.mockState = 2; };
                    this.playVideo = () => { window.mockState = 1; };
                    this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """)
                failures = {"remaining": 1, "attempts": 0}

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    if parsed.path == "/clicktrack/api/video-drafts" and request.method == "POST":
                        failures["attempts"] += 1
                        if failures["remaining"]:
                            failures["remaining"] -= 1
                            route.fulfill(status=503, content_type="application/json", body='{"error":"Unavailable"}')
                            return
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length > 0")
                original_tracks = fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                page.click("#track-actions summary")
                page.click("#btn-video-track")
                page.locator("#builder-youtube-url").fill("https://youtu.be/abcdefghijk")
                page.click("#builder-load-youtube")
                page.wait_for_function("!document.querySelector('#builder-add').disabled")
                page.locator("#builder-bar").fill("1")
                page.locator("#builder-bar").dispatch_event("change")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Could not save')")
                page.wait_for_timeout(1600)
                self.assertEqual(failures["attempts"], 1)
                page.locator("#builder-title").fill("New video track")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Incomplete video draft saved')")
                drafts = fixture.client.get("/clicktrack/api/video-drafts").json["drafts"]
                self.assertEqual(len(drafts), 1)
                draft_id = drafts[0]["id"]
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length > 0")
                page.click("#track-actions summary")
                page.click("#btn-video-track")
                page.click("#builder-legacy-drafts > summary")
                page.wait_for_function("document.querySelector('#builder-drafts').options.length > 1")
                page.select_option("#builder-drafts", str(draft_id))
                page.click("#builder-open-draft")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened saved draft')")
                page.wait_for_function("!document.querySelector('#builder-add').disabled")
                page.locator("#builder-title").fill("Recovered video track")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Incomplete video draft saved')")
                self.assertEqual(len(fixture.client.get("/clicktrack/api/video-drafts").json["drafts"]), 1)
                page.evaluate("window.mockTime = 2")
                page.click("#builder-add")
                page.locator("#builder-bar").fill("2")
                page.locator("#builder-bar").dispatch_event("change")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Track created and linked')")
                tracks = fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                self.assertEqual(len(tracks), len(original_tracks) + 1)
                track = next(item for item in tracks if item["title"] == "Recovered video track")
                self.assertEqual(track["videoDraftId"], draft_id)
                self.assertEqual(track["sections"][0]["bpm"], 120)
                self.assertEqual(len(fixture.client.get("/clicktrack/api/video-drafts").json["drafts"]), 1)
                self.assertTrue(page.locator("#builder-track-editor #sections").is_visible())
                self.assertEqual(page.locator("#builder-marker #builder-nudge-forward").count(), 1)
                self.assertTrue(page.locator("#builder-nudge-forward").is_enabled())
                page.click("#builder-nudge-forward")
                self.assertEqual(page.locator("#builder-time").input_value(), "0:02.100")
                page.click("#builder-nudge-back")
                self.assertEqual(page.locator("#builder-time").input_value(), "0:02.000")
                page.locator("#builder-dialogue").fill('=SUM(1,2)\n"cue"')
                page.locator("#builder-dialogue").dispatch_event("change")
                with page.expect_download() as downloaded:
                    page.click("#builder-export-csv")
                import csv
                with open(downloaded.value.path(), encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[1]["time"], "2")
                self.assertEqual(rows[1]["bar"], "2")
                self.assertEqual(rows[1]["dialogue"], '\'=SUM(1,2)\n"cue"')
                self.assertEqual(set(rows[1]), {"label", "time", "bar", "skip", "num", "den", "unit", "shape",
                                                "bpm", "rampAnchor", "endBpm", "modulationOld", "modulationNew",
                                                "dialogue", "figure", "visualCue"})
                page.select_option("#builder-markers", "0")
                self.assertTrue(page.locator("#builder-nudge-forward").is_disabled())
                self.assertTrue(page.locator("#builder-nudge-back").is_disabled())
                page.click("#builder-play")
                page.evaluate("window.mockTime = 1.95")
                page.wait_for_function("document.querySelector('#builder-review-status').textContent.includes('Paused just before')")
                self.assertAlmostEqual(page.evaluate("window.mockTime"), 1.88)
                self.assertEqual(page.evaluate("window.mockState"), 2)
                page.wait_for_function("document.querySelector('#builder-play').textContent === 'Play video'")
                page.click("#builder-play")
                page.evaluate("window.mockTime = 2.01")
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate("window.mockState"), 1)
                page.uncheck("#builder-review")
                page.evaluate("window.mockTime = 1.5")
                page.wait_for_timeout(100)
                page.evaluate("window.mockTime = 2.1")
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate("window.mockState"), 1)
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_page_shows_live_linked_track_editor(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"time": 0, "bar": "1", "num": "4", "den": "4", "unit": "auto",
                      "shape": "steady", "label": "1", "dialogue": "Original"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Cues", "videoId": "abcdefghijk", "markers": [
                    marker, {**marker, "time": 2, "bar": "2", "label": "2"},
                ],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Linked", "videoDraftId": draft["id"], "sections": [
                    {"startBar": 1, "bars": 1, "num": 4, "den": 4,
                     "notes": "Original", "hitPoint": "1", "compoundClickDefaulted": True},
                ],
            }).json
            other = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Other", "sections": [{"name": "Other section"}],
            }).json
            shared = fixture.client.post("/clicktrack/api/tracks", json={
                "title": "Shared", "videoDraftId": draft["id"], "sections": [{"name": "Shared section"}],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.YT = {Player: function () { this.destroy = () => {}; }};")

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.route("https://www.youtube.com/**", lambda route: route.abort())
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 3")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                try:
                    page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')", timeout=5000)
                except Error:
                    self.fail("Video editor failed to open: " + page.locator("#builder-draft-status").text_content() + " errors: " + repr(errors))
                self.assertTrue(page.locator("#builder-track-editor #sections").is_visible())
                page.wait_for_function("document.querySelector('#builder-selected-section-editor [data-f=click]') !== null")
                full_fields = page.locator('#sections [data-row="0"] [data-f]').evaluate_all("(els) => els.map(el => el.dataset.f)")
                selected_fields = page.locator("#builder-selected-section-editor [data-f]").evaluate_all("(els) => els.map(el => el.dataset.f)")
                self.assertEqual(selected_fields, full_fields)
                self.assertEqual(page.locator("#builder-marker #builder-selected-section-editor").count(), 1)
                for field in ("type", "hitPoint", "figure", "visualCue", "beatUnit", "num", "den", "startBar", "notes"):
                    self.assertTrue(page.locator(f'#builder-selected-section-editor [data-f="{field}"]').is_hidden(), field)
                self.assertTrue(page.locator("#builder-figure").is_visible())
                self.assertTrue(page.locator('#builder-selected-section-editor [data-f="grouping"]').is_visible())
                self.assertTrue(page.locator('#builder-selected-section-editor [data-f="bpm"]').is_disabled())
                page.click("#builder-jump-section")
                self.assertEqual(page.evaluate("document.activeElement.dataset.f"), "name")
                self.assertEqual(page.evaluate("document.activeElement.closest('[data-row]').dataset.row"), "0")
                page.click("#builder-back-to-top")
                self.assertEqual(page.evaluate("document.activeElement.parentElement.id"), "builder-video-panel")
                self.assertEqual(page.evaluate("document.activeElement.tagName"), "SUMMARY")
                page.click("#builder-video-panel > summary")
                self.assertFalse(page.locator("#builder-markers").is_visible())
                self.assertTrue(page.locator("#builder-track-editor #sections").is_visible())
                def wait_for_track(predicate):
                    for _ in range(60):
                        saved_tracks = fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                        saved_track = next(t for t in saved_tracks if t["id"] == track["id"])
                        if predicate(saved_track):
                            return saved_track
                        page.wait_for_timeout(100)
                    self.fail("Track was not saved: " + page.locator("#save-status").text_content())
                page.click("#builder-video-panel > summary")
                page.locator('#builder-selected-section-editor [data-f="grouping"]').fill("2+2")
                self.assertEqual(page.locator('#sections [data-f="grouping"]').input_value(), "2+2")
                wait_for_track(lambda t: t["sections"][0]["grouping"] == "2+2")
                page.locator('#sections [data-f="click"]').select_option("sub2")
                self.assertEqual(page.locator('#builder-selected-section-editor [data-f="click"]').input_value(), "sub2")
                wait_for_track(lambda t: t["sections"][0]["click"] == "sub2")
                page.locator('#builder-selected-section-editor [data-f="visualEndCue"]').check()
                saved = wait_for_track(lambda t: t["sections"][0]["visualEndCue"])
                self.assertTrue(fixture.client.get(f"/clicktrack/api/video-drafts/{saved['videoDraftId']}").json["markers"][1]["visualCue"])
                page.select_option("#builder-markers", "1")
                self.assertIn("opening hit point", page.locator("#builder-selected-section-status").text_content())
                self.assertEqual(page.locator("#builder-selected-section-editor [data-f]").count(), 0)
                self.assertTrue(page.locator("#builder-jump-section").is_disabled())
                page.select_option("#builder-markers", "0")
                page.locator('#builder-selected-section-editor [data-f="grouping"]').fill("")
                wait_for_track(lambda t: t["sections"][0]["grouping"] == "")
                page.click("#builder-video-panel > summary")
                page.locator('#sections [data-f="notes"]').fill("Edited on video page")
                saved_track = wait_for_track(lambda t: t["sections"][0]["notes"] == "Edited on video page")
                self.assertEqual(saved_track["sections"][0]["notes"], "Edited on video page")
                saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved_track['videoDraftId']}").json
                self.assertEqual(saved_draft["markers"][0]["dialogue"], "Edited on video page")
                self.assertNotEqual(saved_track["videoDraftId"], draft["id"])
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)
                self.assertEqual(next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                      if t["id"] == shared["id"]), shared)
                page.click("#builder-video-panel > summary")
                page.locator("#builder-num").fill("")
                page.locator("#builder-num").dispatch_event("change")
                for _ in range(60):
                    saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved_track['videoDraftId']}").json
                    if saved_draft["markers"][0]["num"] == "":
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(saved_draft["markers"][0]["num"], "")
                self.assertEqual(next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                      if t["id"] == track["id"])["sections"][0]["num"], 4)
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 3")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("!document.querySelector('#view-video').hidden && document.querySelector('#builder-num').value === ''")
                page.locator("#builder-num").fill("3")
                page.locator("#builder-num").dispatch_event("change")
                saved_track = wait_for_track(lambda t: t["sections"][0]["num"] == 3)
                self.assertEqual(saved_track["sections"][0]["bpm"], 90)
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{saved_track['videoDraftId']}").json["markers"][0]["num"], "3")
                page.click("button[data-view=perform]")
                page.click("#btn-quick-edit")
                page.locator('#quick-edit-section [data-f="num"]').fill("2")
                page.locator('#quick-edit-section [data-f="num"]').dispatch_event("change")
                saved_track = wait_for_track(lambda t: t["sections"][0]["num"] == 2)
                self.assertEqual(saved_track["sections"][0]["bpm"], 60)
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{saved_track['videoDraftId']}").json["markers"][0]["num"], "2")
                page.click("#quick-edit-done")
                page.click("button[data-view=edit]")
                page.wait_for_function("!document.querySelector('#view-video').hidden")
                decisions = {"accept": False, "questions": []}

                def decide(dialog):
                    decisions["questions"].append(dialog.message)
                    dialog.accept() if decisions["accept"] else dialog.dismiss()

                page.on("dialog", decide)
                page.locator("#builder-bar").fill("2")
                page.locator("#builder-bar").dispatch_event("change")
                for _ in range(60):
                    saved_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{saved_track['videoDraftId']}").json
                    if saved_draft["markers"][0]["bar"] == "2":
                        break
                    page.wait_for_timeout(100)
                self.assertEqual(saved_draft["markers"][0]["bar"], "2",
                                 "Save status: " + page.locator("#save-status").text_content() + " errors: " + repr(errors))
                self.assertEqual(next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                      if t["id"] == track["id"])["sections"][0]["startBar"], 1)
                page.select_option("#builder-markers", "1")
                page.locator("#builder-bar").fill("3")
                page.locator("#builder-bar").dispatch_event("change")
                saved_track = wait_for_track(lambda t: t["sections"][0]["startBar"] == 2)
                self.assertEqual(saved_track["sections"][0]["click"], "sub2")
                self.assertEqual(saved_track["sections"][0]["notes"], "Edited on video page")
                self.assertFalse(decisions["questions"])
                self.assertEqual(page.locator('#sections [data-f="startBar"]').input_value(), "2")
                page.select_option("#builder-markers", "0")
                page.wait_for_function("document.querySelector('#builder-selected-section-editor [data-f=startBar]')?.value === '2'")
                page.reload()
                page.wait_for_function("document.querySelector('#track-select').options.length === 3")
                page.select_option("#track-select", str(track["id"]))
                page.click("button[data-view=edit]")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened linked draft')")
                self.assertEqual(page.locator('#sections [data-f="startBar"]').input_value(), "2")
                page.select_option("#builder-markers", "0")
                page.locator("#builder-dialogue").fill("New cue after warning")
                page.locator("#builder-dialogue").dispatch_event("change")
                saved_track = wait_for_track(lambda t: t["sections"][0]["notes"] == "New cue after warning")
                self.assertEqual(saved_track["sections"][0]["notes"], "New cue after warning")
                decisions["accept"] = True
                page.select_option("#track-select", str(other["id"]))
                self.assertTrue(page.locator("#builder-track-editor").is_hidden())
                page.click("button[data-view=edit]")
                self.assertEqual(page.locator('#sections [data-f="name"]').first.input_value(), "Other section")
                self.assertEqual(page.locator("#editor-jump-section").input_value(), "")
                self.assertTrue(page.locator("#editor-jump-button").is_disabled())
                self.assertIn("Other section", page.locator("#editor-jump-section option").nth(1).text_content())
                page.select_option("#editor-jump-section", "0")
                page.click("#editor-jump-button")
                self.assertEqual(page.evaluate("document.activeElement.dataset.i"), "0")
                page.click("#btn-edit-video")
                page.wait_for_function("!document.querySelector('#view-video').hidden && document.querySelector('#builder-draft-status').textContent.includes('Open your saved')")
                page.click("#builder-legacy-drafts > summary")
                page.select_option("#builder-drafts", str(draft["id"]))
                page.click("#builder-open-draft")
                page.wait_for_function("document.querySelector('#builder-draft-status').textContent.includes('Opened saved draft')")
                page.click("#builder-link-draft")
                for _ in range(80):
                    linked = next(t for t in fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                                  if t["id"] == other["id"])
                    if linked["videoDraftId"] and linked["videoDraftId"] != draft["id"]:
                        break
                    page.wait_for_timeout(100)
                self.assertIsNotNone(linked["videoDraftId"])
                self.assertNotEqual(linked["videoDraftId"], draft["id"])
                self.assertEqual(fixture.client.get(f"/clicktrack/api/video-drafts/{draft['id']}").json, draft)
                page.click("button[data-view=perform]")
                page.click("#btn-quick-edit")
                self.assertEqual(page.locator('#quick-edit-section [data-f="name"]').input_value(), "Other section")
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_track_conflict_describes_values_and_respects_choice(self):
        from playwright.sync_api import Error, sync_playwright

        source = (Path(__file__).resolve().parents[1] / "assets" / "js" / "clicktrack.js").read_text(encoding="utf-8")
        source = source.replace("  init();\n})();", """
          window.conflictTest = (accept) => {
            builder.markers = [
              {...builderMarker(0), bar:'1', num:'3', den:'4', unit:'auto', dialogue:'Draft cue', label:'1'},
              {...builderMarker(4), bar:'2', num:'4', den:'4', unit:'auto', label:'2'},
            ];
            const track = {sections:[normSection({
              startBar:1, bars:1, num:4, den:4, hitPoint:'1', notes:'Track cue',
            })]};
            let question = '';
            window.confirm = text => { question = text; return accept; };
            selectVideoMarker = () => {};
            renderVideoPlan = () => {};
            const result = inheritTrackSettings(track);
            return {question, result, marker:builder.markers[0]};
          };
        })();""")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chrome")
            except Error as error:
                self.skipTest(f"Chrome is unavailable: {error}")
            page = browser.new_page()
            page.route("http://clicktrack.test/", lambda route: route.fulfill(status=200, body="<html></html>", content_type="text/html"))
            page.goto("http://clicktrack.test/")
            page.add_script_tag(content=source)
            declined = page.evaluate("window.conflictTest(false)")
            self.assertIn("Bar 1 (hit point 1)", declined["question"])
            self.assertIn("Meter numerator: draft 3 → track 4", declined["question"])
            self.assertIn("Cue text: draft Draft cue → track Track cue", declined["question"])
            self.assertEqual(declined["marker"]["num"], "3")
            self.assertEqual(declined["marker"]["dialogue"], "Draft cue")
            accepted = page.evaluate("window.conflictTest(true)")
            self.assertEqual(accepted["marker"]["num"], "4")
            self.assertEqual(accepted["marker"]["dialogue"], "Track cue")
            self.assertEqual(accepted["marker"]["time"], 0)
            browser.close()

    def test_combined_backup_export_and_restore(self):
        import json
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            marker = {"time": 0, "bar": "1", "num": "3", "den": "4", "unit": "auto",
                      "shape": "timedPause", "bpm": "94", "dialogue": "Cue", "figure": "7",
                      "visualCue": True, "skip": "2", "label": "24A",
                      "modulationOld": "1/8:triplet", "modulationNew": "1/4:septuplet"}
            draft = fixture.client.post("/clicktrack/api/video-drafts", json={
                "title": "Snowman cues", "videoId": "abcdefghijk", "markers": [marker],
            }).json
            track = fixture.client.post("/clicktrack/api/tracks", json={
                **fixture.payload, "videoDraftId": draft["id"], "videoOffset": 12.5,
                "sections": [{**fixture.payload["sections"][0], "compoundClickDefaulted": True}],
            }).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page(accept_downloads=True)
                fail_draft = {"enabled": False}

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    if fail_draft["enabled"] and parsed.path == f"/clicktrack/api/video-drafts/{draft['id']}":
                        route.fulfill(status=503, content_type="application/json", body='{"error":"Unavailable"}')
                        return
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                page.route("http://clicktrack.test/**", route_request)
                page.goto("http://clicktrack.test/clicktrack/")
                page.wait_for_function("document.querySelector('#track-select').options.length === 1")
                page.click("#track-actions summary")
                with page.expect_download() as downloaded:
                    page.click("#btn-export-track")
                data = json.loads(Path(downloaded.value.path()).read_text(encoding="utf-8"))
                self.assertEqual(data["version"], 2)
                self.assertEqual(data["sections"], track["sections"])
                self.assertEqual(data["videoOffset"], 12.5)
                self.assertEqual(data["videoDraft"], {key: draft[key] for key in ("title", "videoId", "markers")})
                page.locator("#import-file").set_input_files({
                    "name": "backup.json", "mimeType": "application/json", "buffer": json.dumps(data).encode(),
                })
                page.wait_for_function("document.querySelector('#toast').textContent.includes('Restored a new track')")
                tracks = fixture.client.get("/clicktrack/api/tracks").json["tracks"]
                restored = next(item for item in tracks if item["id"] != track["id"])
                self.assertEqual(restored["sections"], track["sections"])
                self.assertNotEqual(restored["videoDraftId"], draft["id"])
                restored_draft = fixture.client.get(f"/clicktrack/api/video-drafts/{restored['videoDraftId']}").json
                self.assertEqual({key: restored_draft[key] for key in ("title", "videoId", "markers")}, data["videoDraft"])
                page.select_option("#track-select", str(track["id"]))
                fail_draft["enabled"] = True
                page.click("#track-actions summary")
                page.click("#btn-export-track")
                page.wait_for_function("document.querySelector('#toast').textContent.includes('Could not export backup')")
                self.assertIn("Unavailable", page.locator("#toast").text_content())
                page.locator("#import-file").set_input_files({
                    "name": "old.json", "mimeType": "application/json",
                    "buffer": json.dumps({"format": "jackcapstaff-clicktrack", "version": 1,
                                          "title": "Legacy", "sections": track["sections"]}).encode(),
                })
                page.wait_for_function("document.querySelector('#toast').textContent.includes('Imported 1 track')")
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_editor_preview_control(self):
        from urllib.parse import urlsplit
        from playwright.sync_api import Error, sync_playwright
        from test_clicktrack import ClickTrackStorageTests

        fixture = ClickTrackStorageTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    response = fixture.client.open(path, method=request.method, data=request.post_data,
                                                   content_type=request.headers.get("content-type"))
                    body = response.data
                    response.close()
                    if parsed.path.endswith("/js/clicktrack.js"):
                        source = body.decode("utf-8").replace("\r\n", "\n")
                        self.assertIn("  init();\n})();", source)
                        body = source.replace("  init();\n})();", """
                          const previewTestMedia = {
                            currentTime: 0, duration: 30, readyState: 4, paused: true,
                            seeking: false, playbackRate: 1, ended: false,
                            play() { this.paused = false; return Promise.resolve(); },
                            pause() { this.paused = true; }
                          };
                          window.previewTest = {
                            setup: () => {
                              builderMedia = () => previewTestMedia;
                              const m = {num:'4',den:'4',unit:'auto',bpm:'',dialogue:'',figure:'',skip:'0',shape:'steady'};
                              builder.markers = [{...m,time:0,bar:'1',label:'1'},
                                {...m,time:2,bar:'2',label:'2'}];
                              selectVideoMarker(0); setBuilderMediaControls(); renderVideoPlan();
                            },
                            setupPause: () => {
                              const m = {num:'3',den:'4',unit:'auto',bpm:'',dialogue:'',figure:'',skip:'0'};
                              builder.markers = [{...m,time:0,bar:'175',label:'1',shape:'timedPause'},
                                {...m,time:3,bar:'176',label:'2',shape:'steady'},
                                {...m,time:4.915,bar:'177',label:'3',shape:'steady'}];
                              selectVideoMarker(0); renderVideoPlan();
                            },
                            stage: (phase) => {
                              current = {videoOffset:0, sections:[
                                normSection({type:'timedPause',pauseDuration:3,bars:1,startBar:175}),
                                normSection({type:'click',bpm:94,bars:1,num:3,den:4,startBar:176})
                              ]};
                              renderStage({secIdx:0, phase, bar:0, tick:0, sub:0, beatsLeft:2});
                            },
                            figures: (secs) => {
                              current = {videoOffset:0, sections:secs.map(normSection)};
                              renderRehearsalList();
                            },
                            cueCountIn: () => {
                              current = {videoOffset:0, sections:[
                                normSection({type:'pause'}),
                                normSection({type:'click',countIn:1})
                              ]};
                              ctx = {currentTime:10};
                              E.nextTime = 10; E.phase = 'pause'; E.phaseStart = 10;
                              visQ.length = 0;
                              leaveCue(1);
                              playClick = () => {};
                              step();
                              renderStage(visQ[0]);
                            },
                            nextCueTick: () => { step(); renderStage(visQ[visQ.length - 1]); },
                            visual: value => { prefs.beatVisual = value; renderBeatVisual(); renderBuilderMetronome(); },
                            media: previewTestMedia,
                          };
                          init();
                        })();""").encode("utf-8")
                    route.fulfill(status=response.status_code, body=body,
                                  headers={"content-type": response.content_type})

                page.route("http://clicktrack.test/**", route_request)
                page.goto("http://clicktrack.test/clicktrack/")
                page.click("#track-actions summary")
                page.click("#btn-video-track")
                page.evaluate("window.previewTest.setup()")
                page.wait_for_function("document.querySelector('#builder-metro-bar').textContent === '1'")
                self.assertEqual(page.locator("#builder-metro-meter").inner_text(), "4/4")
                self.assertEqual(page.locator("#builder-metro-tempo").inner_text(), "♩=120")
                self.assertEqual(page.locator("#builder-metro-beats .beat.on").count(), 1)
                page.evaluate("window.previewTest.media.currentTime = 0.6")
                page.wait_for_function("document.querySelector('#builder-metro-beats .beat.on')?.dataset.u === '1'")
                page.evaluate("window.previewTest.media.currentTime = 2.1")
                page.wait_for_function("document.querySelector('#builder-metro-name').textContent.includes('Outside')")
                page.evaluate("window.previewTest.media.currentTime = 0")
                page.uncheck("#builder-review")
                page.check("#builder-click-preview")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('ready')")
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('preview on')")
                page.select_option("#builder-shape", "timedPause")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('unavailable')")
                page.wait_for_function("document.querySelector('#builder-metro-name').textContent.includes('unavailable')")
                page.select_option("#builder-shape", "steady")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('preview on')")
                page.evaluate("window.previewTest.setupPause()")
                page.wait_for_function("!document.querySelector('#builder-metro-fermata').hidden")
                page.evaluate("window.previewTest.media.currentTime = 2")
                page.wait_for_function("document.querySelector('#builder-metro-fermata-label').textContent === 'Count-in after pause'")
                self.assertFalse(page.locator("#builder-metro-fermata").is_hidden())
                for visual in ("circles", "pendulum", "both"):
                    page.evaluate("(visual) => window.previewTest.visual(visual)", visual)
                    self.assertTrue(page.locator("#builder-metro-fermata").is_visible())
                    if visual != "circles":
                        pendulum = page.locator("#builder-metro-pendulum").bounding_box()
                        fermata = page.locator("#builder-metro-fermata").bounding_box()
                        self.assertLessEqual(pendulum["x"] + pendulum["width"], fermata["x"])
                page.evaluate("window.previewTest.media.currentTime = 3.1")
                page.wait_for_function("document.querySelector('#builder-metro-fermata').hidden")
                page.evaluate("window.previewTest.media.currentTime = 0")
                page.wait_for_function("!document.querySelector('#builder-metro-fermata').hidden")
                page.click("button[data-view=edit]")
                self.assertFalse(page.locator("#builder-click-preview").is_checked())
                page.click("button[data-view=perform]")
                page.evaluate("window.previewTest.stage('timedPause')")
                self.assertFalse(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                self.assertEqual(page.locator("#d-fermata-label").text_content(), "Timed pause · silent")
                page.evaluate("window.previewTest.stage('timedCount')")
                self.assertFalse(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                self.assertEqual(page.locator("#d-fermata-label").text_content(), "Count-in after pause")
                for visual in ("circles", "pendulum", "both"):
                    page.evaluate("(visual) => window.previewTest.visual(visual)", visual)
                    for width in (1200, 390):
                        page.set_viewport_size({"width": width, "height": 800})
                        self.assertTrue(page.locator("#d-fermata").is_visible())
                        fermata = page.locator("#d-fermata").bounding_box()
                        countin = page.locator("#d-countin").bounding_box()
                        self.assertGreaterEqual(countin["y"], fermata["y"] + fermata["height"])
                        if visual != "circles":
                            pendulum = page.locator("#d-pendulum").bounding_box()
                            self.assertLessEqual(pendulum["x"] + pendulum["width"], fermata["x"])
                        page.evaluate("""() => {
                          document.body.classList.add('performance-mode');
                          document.querySelector('.perform-layout').classList.add('with-video');
                        }""")
                        self.assertEqual(page.locator("#d-countin").evaluate("(el) => getComputedStyle(el).position"), "static")
                        page.evaluate("""() => {
                          document.body.classList.remove('performance-mode');
                          document.querySelector('.perform-layout').classList.remove('with-video');
                        }""")
                page.evaluate("window.previewTest.stage('pause')")
                self.assertFalse(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                self.assertEqual(page.locator("#d-fermata-label").text_content(), "Waiting for pedal")
                page.evaluate("window.previewTest.stage('play')")
                self.assertTrue(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                page.evaluate("window.previewTest.cueCountIn()")
                self.assertFalse(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                self.assertEqual(page.locator("#d-fermata-label").text_content(), "Count-in after pause")
                for _ in range(4):
                    page.evaluate("window.previewTest.nextCueTick()")
                self.assertTrue(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                page.evaluate("window.previewTest.stage('countin')")
                self.assertTrue(page.locator("#d-fermata").evaluate("(el) => el.hidden"))
                page.evaluate("""() => window.previewTest.figures([
                  {startBar: 90, bars: 1, hitPoint: '1'},
                  {startBar: 100, bars: 1, hitPoint: '2', figure: '6'},
                  {startBar: 102, bars: 1, hitPoint: '3'},
                  {startBar: 105, bars: 1, hitPoint: '4'},
                  {startBar: 107, bars: 1, hitPoint: '5', figure: '7'},
                  {startBar: 110, bars: 1, hitPoint: '6'},
                  {type:'pause', name:'Cue'},
                ])""")
                rows = page.locator("#rehearsal-list .rehearsal-point")
                self.assertEqual(rows.nth(0).locator(".rehearsal-columns > span").nth(1).text_content(), "6 − 10")
                self.assertEqual(rows.nth(2).locator(".rehearsal-columns > span").nth(1).text_content(), "6 + 2")
                self.assertEqual(rows.nth(3).locator(".rehearsal-columns > span").nth(1).text_content(), "7 − 2")
                self.assertIn("2 bars before Figure 7", rows.nth(3).get_attribute("aria-label"))
                self.assertEqual(rows.nth(5).locator(".rehearsal-columns > span").nth(1).text_content(), "7 + 3")
                self.assertEqual(rows.nth(6).locator(".rehearsal-columns > span").nth(1).text_content(), "—")
                page.evaluate("""() => window.previewTest.figures([
                  {startBar: 1, bars: 2, figure: 'A'},
                  {startBar: 5, bars: 1},
                  {startBar: 9, bars: 1, figure: 'B'},
                  {startBar: 9, bars: 1},
                  {startBar: 9, bars: 1, figure: 'C'},
                  {startBar: 8, bars: 1},
                  {type:'pause', figure:'Cue figure'},
                ])""")
                self.assertEqual(rows.nth(1).locator(".rehearsal-columns > span").nth(1).text_content(), "A + 4")
                self.assertIn("4 bars after Figure A", rows.nth(1).get_attribute("aria-label"))
                self.assertEqual(rows.nth(3).locator(".rehearsal-columns > span").nth(1).text_content(), "B")
                self.assertEqual(rows.nth(5).locator(".rehearsal-columns > span").nth(1).text_content(), "A + 7")
                self.assertEqual(rows.nth(6).locator(".rehearsal-columns > span").nth(1).text_content(), "Cue figure")
                page.evaluate("window.previewTest.figures([{startBar:1,bars:1}])")
                self.assertEqual(rows.nth(0).locator(".rehearsal-columns > span").nth(1).text_content(), "—")
                self.assertEqual(errors, [])
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_review_pauses_before_hit_and_defers_click_until_resume(self):
        from playwright.sync_api import Error, sync_playwright

        source = (Path(__file__).resolve().parents[1] / "assets" / "js" / "clicktrack.js").read_text(encoding="utf-8")
        source = source.replace("  init();\n})();", """
          window.reviewHooks = {
            setup: () => {
              const media = {currentTime:0, paused:false, seeking:false, ended:false, readyState:4,
                duration:10, playbackRate:1, pause() { this.paused = true; }};
              const clicks = [];
              ctx = {currentTime:10, state:'running'};
              builderMedia = () => media;
              updateVideoPlayhead = () => {};
              selectVideoMarker = index => { builder.selected = index; };
              playClick = (time, level, section, audio) => {
                const source = {stop() {}, addEventListener() {}};
                audio.track(source);
                clicks.push({time, level});
              };
              builder.markers = [{time:0,label:'1'}, {time:2,label:'2',bar:'2'}, {time:4,label:'3',bar:'3'}];
              builder.reviewTime = 0;
              builder.reviewAnchor = null;
              builderClick.plan = [
                normSection({bpm:120,bars:1,num:4,den:4,calcStart:'0:00.000',calcEnd:'0:02.000'}),
                normSection({bpm:120,bars:1,num:4,den:4,calcStart:'0:02.000',calcEnd:'0:04.000'}),
              ];
              document.getElementById('builder-click-preview').checked = true;
              document.getElementById('builder-review').checked = true;
              return {media, clicks};
            },
            tick: builderClickTick, review: reviewVideoPlayback,
            clock: value => { ctx.currentTime = value; },
            anchor: () => builder.reviewAnchor, selected: () => builder.selected,
          };
        })();""")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chrome")
            except Error as error:
                self.skipTest(f"Chrome is unavailable: {error}")
            page = browser.new_page()
            page.route("http://clicktrack.test/", lambda route: route.fulfill(status=200, content_type="text/html",
                body='<input id="builder-click-preview" type="checkbox"><input id="builder-review" type="checkbox">'
                     '<div id="view-video"></div><div id="builder-click-status"></div><div id="builder-review-status"></div>'
                     '<div id="builder-marker"></div><div id="builder-selected-section-editor"></div>'
                     '<select id="builder-markers"></select>'))
            page.goto("http://clicktrack.test/")
            page.add_script_tag(content=source)
            page.evaluate("""() => {
              const t = window.reviewHooks, assert = (v, m) => { if (!v) throw Error(m); };
              const near = (a,b) => Math.abs(a-b) < 1e-8;
              const {media, clicks} = t.setup();
              media.currentTime = 1.95; t.clock(10); t.tick();
              assert(clicks.length === 0, 'lookahead must not schedule the upcoming downbeat');
              media.currentTime = 2.03; t.clock(10.08); t.tick();
              assert(clicks.length === 0, 'late polling must not play the downbeat before stopping');
              t.review();
              assert(media.paused && near(media.currentTime, 1.88), 'seek just before the hit even after overshoot');
              assert(t.selected() === 1 && near(t.anchor(), 2), 'select hit and protect its resume');
              media.paused = false; t.clock(11); t.tick();
              t.review();
              assert(!media.paused, 'resume does not immediately pause again');
              media.currentTime = 1.95; t.clock(11.07); t.tick();
              assert(clicks.length === 1 && near(clicks[0].time, 11.12), 'downbeat is scheduled after resume');
              media.currentTime = 2.01; t.clock(11.13); t.review();
              assert(!media.paused, 'crossing resumed hit does not pause again');
              document.getElementById('builder-review').checked = false;
              media.currentTime = 3.95; t.clock(13.07); t.tick(); t.review();
              assert(!media.paused, 'unchecked mode plays through hits');
            }""")
            browser.close()

    def test_video_editor_click_preview(self):
        from playwright.sync_api import Error, sync_playwright

        source = (Path(__file__).resolve().parents[1] / "assets" / "js" / "clicktrack.js").read_text(encoding="utf-8")
        original = "  init();\n})();"
        self.assertIn(original, source)
        source = source.replace(original, """
          window.previewHooks = {
            normal: normSection, events: builderClickEvents, preEvents: preRollEvents, state: builderMetronomeState,
            setup: (secs) => {
              const media = {currentTime:0, paused:false, seeking:false, readyState:4, playbackRate:1};
              const clicks = [];
              ctx = {currentTime:10, state:'running'};
              builderMedia = () => media;
              playClick = (time, level, section, audio) => {
                const source = {stopped:false, stop() { this.stopped = true; }, addEventListener() {}};
                audio.track(source);
                clicks.push({time, level, source});
              };
              builderClick.plan = secs;
              document.getElementById('builder-click-preview').checked = true;
              return {media, clicks};
            },
            tick: builderClickTick, clear: cancelBuilderClicks,
            clock: value => { ctx.currentTime = value; },
            invalid: () => updateBuilderClickPlan(null, 'Invalid hit points'),
          };
        })();""")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chrome")
            except Error as error:
                self.skipTest(f"Chrome is unavailable: {error}")
            page = browser.new_page()
            page.route("http://clicktrack.test/", lambda route: route.fulfill(status=200, content_type="text/html",
                body='<input id="builder-click-preview" type="checkbox"><input id="builder-review" type="checkbox"><div id="view-video"></div><div id="builder-click-status"></div>'))
            page.goto("http://clicktrack.test/")
            page.add_script_tag(content=source)
            page.evaluate("""() => {
              const t = window.previewHooks, near = (a,b) => Math.abs(a-b) < 1e-8;
              const assert = (ok, message) => { if (!ok) throw Error(message); };
              const first = t.normal({bpm:120, bars:1, num:4, den:4, calcStart:'0:00.000', calcEnd:'0:02.000'});
              const before = t.normal({bpm:120, bars:1, num:4, den:4, calcStart:'-0:02.000',calcEnd:'0:00.000'});
              const pre = Array.from(t.preEvents(before, -4, -2, [before]));
              assert(pre.length === 8 && near(pre[0].time, -4) && near(pre[4].time, -2),
                     'one count-in bar followed by the actual pre-video bar');
              const pause = t.normal({type:'timedPause', pauseDuration:3.001, bars:1, calcStart:'0:02.000', calcEnd:'0:05.001'});
              const next = t.normal({bpm:94, bars:1, num:3, den:4, calcStart:'0:05.001', calcEnd:'0:06.916'});
              const secs = [first, pause, next];
              assert(t.state(secs, -0.1) === null && t.state(secs, 6.916) === null, 'outside marked spans');
              assert(t.state(secs, 0.6).tick === 1 && t.state(secs, 0.6).bar === 0, 'current beat');
              const silence = t.state(secs, 2.1);
              assert(silence.tick === -1 && silence.bpm === 94 && silence.status.includes('silent'), 'timed silence');
              const count = t.state(secs, 5.001 - 120/94 + 0.001);
              assert(count.status.includes('2 beats') && count.tick === 1, 'timed count-in');
              assert(t.state(secs, 5.001).index === 2, 'following hit point');
              const events = Array.from(t.events(secs, 0));
              assert(events.length === 10, 'four regular, three count-in and three next beats');
              assert(near(events[4].time, 5.001 - 180/94), 'count-in timing');
              assert(near(events[7].time, 5.001), 'hit-point downbeat');
              assert(Array.from(t.events(secs, 5.001))[0].time === 5.001, 'seek to next section');
              const dividedPause = {...pause, pauseSubdivision:3};
              const dividedEvents = Array.from(t.events([first, dividedPause, next], 0));
              const countEvents = dividedEvents.filter(e => e.time >= 2 && e.time < 5.001);
              assert(countEvents.length === 9, 'preview count-in subdivisions');
              assert(countEvents.every((e, i) => e.level === (i % 3 ? 0 : 2)), 'preview subdivision levels');
              assert(near(countEvents[1].time - countEvents[0].time, 60/94/3), 'preview subdivision timing');
              assert(near(dividedEvents[13].time, 5.001), 'preview next downbeat unchanged');
              const compound = t.normal({bpm:60, bars:1, num:6, den:8, calcStart:'0:00.000', calcEnd:'0:02.000'});
              assert(Array.from(t.events([compound], 0)).length === 6, 'compound subdivisions');
              const ramp = t.normal({bpm:120,bpmEnd:60,rampMode:'linearTime',bars:1,num:4,den:4,
                calcStart:'0:00.000',calcEnd:'0:03.000'});
              const rampEvents = Array.from(t.events([ramp], 0));
              assert(near(rampEvents[1].time, 2 / (120 + Math.sqrt(120**2 + (60**2-120**2)/4)) * 60), 'ramp timing');
              const rampState = t.state([ramp], 1.5);
              assert(rampState.bpm < 120 && rampState.bpm > 60, 'ramp tempo display');
              const longSection = t.normal({bpm:120,bars:2,num:4,den:4,startBar:175,
                calcStart:'0:00.000',calcEnd:'0:04.000'});
              assert(t.state([longSection], 2.1).bar === 1, 'second bar');
              const {media, clicks} = t.setup(secs);
              t.tick();
              assert(clicks.length === 1 && near(clicks[0].time, 10), 'initial click');
              media.currentTime = 0.5; t.clock(10.5); t.tick();
              assert(clicks.length === 2 && near(clicks[1].time, 10.5), 'continues with video');
              media.paused = true; t.tick();
              media.currentTime = 5.001; media.paused = false; t.clock(11); t.tick();
              assert(clicks.length === 3 && near(clicks[2].time, 11), 'pause and seek resync');
              media.playbackRate = 2; media.currentTime = 5.001 + 60/94; t.clock(12); t.tick();
              assert(clicks.length === 4 && near(clicks[3].time, 12), 'playback speed resync');
              t.invalid();
              assert(document.getElementById('builder-click-status').textContent.includes('Invalid hit points'), 'invalid plan status');
              t.clear();
              const resumed = t.setup(secs);
              resumed.media.currentTime = 0.45;
              t.tick();
              assert(resumed.clicks.length === 1 && near(resumed.clicks[0].time, 10.05), 'lookahead click');
              resumed.media.paused = true;
              t.tick();
              assert(resumed.clicks[0].source.stopped, 'paused video cancels scheduled click');
            }""")

    def test_timed_pause_scheduling_and_video_plan(self):
        from playwright.sync_api import Error, sync_playwright

        source = (Path(__file__).resolve().parents[1] / "assets" / "js" / "clicktrack.js").read_text(encoding="utf-8")
        original = "  init();\n})();"
        self.assertIn(original, source)
        source = source.replace(original, """
          window.testHooks = {
            plan: timedPausePlan, normal: normSection, timeline: sectionTimeline,
            setTrack: (sections) => { current = {videoOffset: 12.123, sections}; },
            setMarkers: (markers) => { builder.markers = markers; videoReady = () => false; },
            videoPlan: planVideoTrack, merge: mergeVideoSections, describeRemoved: describeRemovedSections,
            run: (sections, duration) => {
              current = {videoOffset: 0, sections};
              ctx = {currentTime: 10};
              const clicks = [];
              playClick = (time, level) => clicks.push({time, level});
              visQ.length = 0;
              E.playing = true; E.suspended = false; E.queued = null; E.nextTime = 10;
              enterSection(0, true);
              for (let t = 10; t <= 10 + duration + 0.3; t += 0.01) {
                ctx.currentTime = t; scheduler();
              }
              return {clicks, events: visQ};
            },
            videoSync: (sections) => {
              current = {videoOffset: 12.123, sections};
              ctx = {currentTime: 10.5};
              E.playing = true; E.suspended = false;
              const calls = [];
              practice = {media: {seek: t => calls.push(['seek', t]),
                play: () => calls.push(['play']), pause: () => calls.push(['pause'])}};
              shown = {secIdx:0, phase:'timedPause', phaseStart:10};
              syncPracticeVideo();
              ctx.currentTime = 12;
              shown.phase = 'timedCount';
              syncPracticeVideo();
              const afterCountStart = calls.length;
              E.suspended = true;
              syncPracticeVideo();
              E.suspended = false;
              syncPracticeVideo();
              return {calls, afterCountStart};
            }
          };
        })();""")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chrome")
            except Error as error:
                self.skipTest(f"Chrome is unavailable: {error}")
            page = browser.new_page()
            page.route("http://clicktrack.test/", lambda route: route.fulfill(
                status=200, body="<html></html>", content_type="text/html"))
            page.goto("http://clicktrack.test/")
            page.add_script_tag(content=source)
            page.evaluate("""() => {
              const t = window.testHooks, eps = 1e-8;
              const assert = (ok, message) => { if (!ok) throw Error(message); };
              const pause = t.normal({type:'timedPause', pauseDuration:3.001, bars:1, startBar:175});
              const next = t.normal({type:'click', bpm:94, num:3, den:4, bars:2, startBar:176});
              const full = t.plan(pause, next);
              assert(full.count === 3 && Math.abs(full.silence - (3.001 - 180/94)) < eps, 'full bar');
              assert(t.plan({...pause, pauseDuration:1.4}, next).count === 2, 'partial bar');
              assert(t.plan(pause, {...next, num:6, den:8, beatUnit:'auto'}).count === 2, 'compound meter');
              for (const [section, following, message] of [
                [{...pause, pauseDuration:0.2}, next, 'too short'],
                [pause, null, 'followed'],
                [pause, {...pause}, 'followed'],
              ]) {
                let failed = false;
                try { t.plan(section, following); } catch (error) { failed = error.message.includes(message); }
                assert(failed, message);
              }
              const run = t.run([pause, next], 3.001);
              const ticks = run.events.filter(e => e.phase === 'timedCount');
              const downbeat = run.events.find(e => e.secIdx === 1 && e.phase === 'play');
              assert(ticks.length === 3 && run.clicks.length >= 4, 'scheduled count-in');
              assert(Math.abs(ticks[0].time - (13.001 - 180/94)) < eps, 'first click');
              assert(Math.abs(ticks[2].time - (13.001 - 60/94)) < eps, 'last click');
              assert(downbeat && Math.abs(downbeat.time - 13.001) < eps, 'next downbeat');
              const shortRun = t.run([{...pause, pauseDuration:1.4}, next], 1.4);
              const shortTicks = shortRun.events.filter(e => e.phase === 'timedCount');
              const shortDownbeat = shortRun.events.find(e => e.secIdx === 1 && e.phase === 'play');
              assert(shortTicks.length === 2 && Math.abs(shortTicks[0].time - (11.4 - 120/94)) < eps, 'partial count-in schedule');
              assert(shortDownbeat && Math.abs(shortDownbeat.time - 11.4) < eps, 'partial count-in downbeat');
              for (const subdivision of [2, 3, 4]) {
                const divided = {...pause, pauseSubdivision:subdivision};
                const result = t.run([divided, next], 3.001);
                const countClicks = result.clicks.filter(c => c.time < 13.001 - eps);
                const start = 13.001 - 180/94;
                assert(countClicks.length === 3 * subdivision, 'all count-in subdivisions scheduled');
                countClicks.forEach((c, i) => {
                  assert(Math.abs(c.time - (start + i * 60/94/subdivision)) < eps, 'subdivision timing');
                  assert(c.level === (i % subdivision ? 0 : 2), 'subdivision sound level');
                });
                assert(result.events.filter(e => e.phase === 'timedCount').length === 3, 'visual stays on whole beats');
                assert(Math.abs(result.events.find(e => e.secIdx === 1 && e.phase === 'play').time - 13.001) < eps, 'subdivisions do not shift downbeat');
              }
              const partial = t.run([{...pause, pauseDuration:1.4, pauseSubdivision:3}, next], 1.4);
              assert(partial.clicks.filter(c => c.time < 11.4 - eps).length === 6, 'partial count-in subdivisions');
              const compound = {...next, num:6, den:8, beatUnit:'auto', bpm:60};
              const compoundRun = t.run([{...pause, pauseDuration:3, pauseSubdivision:3}, compound], 3);
              assert(compoundRun.clicks.filter(c => c.time < 13 - eps).length === 6, 'compound dotted beats divide into thirds');
              t.setTrack([pause, next]);
              assert(Math.abs(t.timeline()[1].start - 15.124) < eps, 'timeline');
              const {calls, afterCountStart} = t.videoSync([pause, next]);
              assert(calls[0][0] === 'seek' && Math.abs(calls[0][1] - 12.623) < eps, 'video seek');
              assert(afterCountStart === 2, 'no seek between silence and count-in');
              assert(calls.some(call => call[0] === 'pause') && calls.filter(call => call[0] === 'play').length === 2, 'pause and resume video');
              const marker = {num:'3', den:'4', unit:'auto', bpm:'', dialogue:'', figure:'', skip:'0'};
              t.setMarkers([{...marker,time:0,bar:'175',label:'1',shape:'timedPause'},
                {...marker,time:3.001,bar:'176',label:'2',shape:'steady'},
                {...marker,time:4.916,bar:'177',label:'3',shape:'steady'}]);
              const plan = t.videoPlan();
              assert(plan.secs[0].section.type === 'timedPause' && plan.secs[0].pausePlan.count === 3, 'video plan');
              const merged = t.merge({sections:[{...next,startBar:175}, {...next,startBar:176}]}, plan.secs.map(x => x.section));
              assert(merged.unmatched === 0 && merged.sections[0].type === 'timedPause', 'merge conversion');
              const retained = t.merge({sections:[{...pause,pauseSubdivision:3}, next]}, plan.secs.map(x => x.section));
              assert(retained.sections[0].pauseSubdivision === 3, 'video regeneration preserves count-in subdivisions');
              const original = t.normal({startBar: 180, bars: 1, name: 'Quiet section',
                notes: 'Watch conductor', figure: '7', grouping:'2+2', num:4,
                countIn: 2, click:'sub2', unitPitch:-3, subVelocity:17, advance:'manual'});
              const loss = t.merge({sections:[original]}, [t.normal({startBar:181})]);
              const warning = t.describeRemoved(loss.removed);
              assert(loss.unmatched === 1 && warning.includes('Bar 180') && warning.includes('Quiet section')
                && warning.includes('Watch conductor') && warning.includes('grouping: 2+2')
                && warning.includes('count-in: 2') && warning.includes('click: sub2')
                && warning.includes('velocity 17'), 'removed section settings described');
              const oldSections = [
                t.normal({startBar:1, hitPoint:'1', calcStart:'0:00.000', name:'First', click:'sub2'}),
                t.normal({startBar:3, hitPoint:'2', calcStart:'0:04.000', name:'Second', countIn:2}),
              ];
              const changedBars = t.merge({sections:oldSections}, [
                t.normal({startBar:3, hitPoint:'1', calcStart:'0:00.000'}),
                t.normal({startBar:5, hitPoint:'2', calcStart:'0:04.000'}),
              ]);
              assert(changedBars.unmatched === 0 && changedBars.sections[0].name === 'First'
                && changedBars.sections[0].click === 'sub2' && changedBars.sections[1].name === 'Second'
                && changedBars.sections[1].countIn === 2, 'renumbering preserves the correct section settings');
              const moved = t.merge({sections:oldSections}, [
                t.normal({startBar:2, hitPoint:'1', calcStart:'0:00.500'}),
                t.normal({startBar:3, hitPoint:'2A', calcStart:'0:04.000'}),
              ]);
              assert(moved.unmatched === 0 && moved.sections[0].name === 'First'
                && moved.sections[1].name === 'Second', 'moved and renamed hit points retain settings');
              const removed = t.merge({sections:oldSections}, [
                t.normal({startBar:3, hitPoint:'2', calcStart:'0:04.000'}),
              ]);
              assert(removed.unmatched === 1 && removed.removed[0].section.name === 'First'
                && removed.sections[0].name === 'Second', 'removed sections are still reported');
              const ambiguous = t.merge({sections:oldSections}, [
                t.normal({startBar:1, hitPoint:'3', calcStart:'0:01.000'}),
              ]);
              assert(ambiguous.unmatched === 2 && ambiguous.sections[0].name !== 'First',
                'a new point at the same bar does not silently inherit unrelated settings');
              t.setMarkers([{...marker,time:0,bar:'175',label:'1',shape:'timedPause'},
                {...marker,time:3.001,bar:'176',label:'2',shape:'rall'},
                {...marker,time:4.916,bar:'177',label:'3',shape:'steady'}]);
              let rampRejected = false;
              try { t.videoPlan(); } catch (error) { rampRejected = error.message.includes('starting BPM'); }
              assert(rampRejected, 'ramp after pause requires explicit start');
              const m = {num:'4',den:'4',unit:'auto',bpm:'',dialogue:'',figure:'',skip:'0'};
              const points = [
                {...m,time:0,bar:'1',label:'1',shape:'steady'},
                {...m,time:2,bar:'2',label:'2',shape:'rall',rampAnchor:'end',endBpm:'96.51',num:'5'},
                {...m,time:4.953,bar:'3',label:'2A',shape:'steady'},
                {...m,time:6.953,bar:'4',label:'3',shape:'steady'},
              ];
              t.setMarkers(points);
              let ramp = t.videoPlan().secs[1].section;
              assert(ramp.bpm === 106.67 && ramp.bpmEnd === 96.51, 'fixed end accommodates changed meter');
              points[1] = {...points[1], shape:'accel', rampAnchor:'next'};
              t.setMarkers(points);
              ramp = t.videoPlan().secs[1].section;
              assert(ramp.bpm === 83.18 && ramp.bpmEnd === 120, 'match next section start');
              points[1] = {...points[1], shape:'rall', rampAnchor:'previous'};
              t.setMarkers(points);
              ramp = t.videoPlan().secs[1].section;
              assert(ramp.bpm === 120 && ramp.bpmEnd === 83.18, 'match previous section end');
              points[1] = {...points[1], shape:'accel', rampAnchor:'next'};
              points[2] = {...points[2], shape:'rall', rampAnchor:'previous'};
              t.setMarkers(points);
              let cycle = false;
              try { t.videoPlan(); } catch (error) { cycle = error.message.includes('depend on each other'); }
              assert(cycle, 'cyclic neighbouring anchors rejected');
              t.setMarkers([
                {...m,time:0,bar:'1',label:'1',shape:'steady',num:'6',den:'8'},
                {...m,time:2,bar:'2',label:'2',shape:'accel',rampAnchor:'previous'},
                {...m,time:4,bar:'3',label:'3',shape:'steady'},
              ]);
              ramp = t.videoPlan().secs[1].section;
              assert(ramp.bpm === 90 && ramp.bpmEnd === 150, 'matching converts dotted beat to quarter beat');
              const mod = {num:'4',den:'4',unit:'auto',bpm:'',dialogue:'',figure:'',skip:'0'};
              const modulation = [
                {...mod,time:0,bar:'1',label:'1',shape:'rall',rampAnchor:'next',
                  modulationOld:'1/8:straight',modulationNew:'1/4:straight'},
                {...mod,time:4.068,bar:'2',label:'2',shape:'steady'},
                {...mod,time:6.859,bar:'3',label:'3',shape:'steady'},
              ];
              t.setMarkers(modulation);
              let modPlan = t.videoPlan();
              assert(Math.abs(modPlan.secs[0].section.bpm - 75) < 0.02
                && Math.abs(modPlan.secs[0].section.bpmEnd - 43) < 0.02
                && Math.abs(modPlan.secs[1].section.bpm - 86) < 0.02, 'quaver to crotchet example');
              assert(modPlan.secs[0].modulation === 'Quaver = Crotchet', 'modulation shown in preview');
              for (const [oldValue, newValue, factor] of [
                ['1/4:triplet','1/4:straight', 2/3],
                ['1/4:quintuplet','1/4:straight', 4/5],
                ['1/4:septuplet','1/4:straight', 4/7],
                ['1/8:dotted','1/4:straight', 3/4],
                ['1/4:straight','1/8:straight', 2],
              ]) {
                modulation[0] = {...modulation[0], modulationOld:oldValue, modulationNew:newValue,
                  shape:factor > 0.686 ? 'accel' : 'rall'};
                modulation[1].time = factor > 1 ? 2 : 4.068;
                modulation[2].time = modulation[1].time + 2.791;
                t.setMarkers(modulation);
                modPlan = t.videoPlan();
                assert(Math.abs(modPlan.secs[0].section.bpmEnd - modPlan.secs[1].section.bpm * factor) < 0.011,
                  'modulation ratio: ' + oldValue + ' = ' + newValue);
              }
              modulation[0] = {...modulation[0], shape:'rall', unit:'1/8',
                modulationOld:'1/8:straight', modulationNew:'1/4:straight'};
              modulation[1].time = 4.068;
              modulation[2].time = 6.859;
              t.setMarkers(modulation);
              modPlan = t.videoPlan();
              assert(Math.abs(modPlan.secs[0].section.bpmEnd - modPlan.secs[1].section.bpm) < 0.011,
                'modulation converts differing section beat units');
            }""")
