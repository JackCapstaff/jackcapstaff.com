"""Layered presentation editor/controller/browser integration tests."""
import base64
import importlib.util
import io
import os
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class PresentationBrowserTests(unittest.TestCase):
    def test_overlap_drag_fade_handles_and_presenter_saved_changes(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            first, second, replacement = fixture.asset(), fixture.asset(), fixture.asset()
            show = fixture.save(fixture.create(), [
                fixture.cue("first", kind="image", assetId=first["id"], duration=8, fade=0),
                fixture.cue("second", kind="image", assetId=second["id"], start=8, duration=8, fade=0),
            ]).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin = browser.new_context(viewport={"width": 1600, "height": 1100})
                audience = browser.new_context()
                errors = []
                def serve(route, client):
                    request = route.request
                    response = client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                admin.route("http://studio.test/**", lambda route: serve(route, fixture.client))
                audience.route("http://studio.test/**", lambda route: serve(route, fixture.guest))
                for context in (admin, audience):
                    context.route("https://media.test/**", lambda route: route.fulfill(
                        content_type="image/png", body=base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")))
                page = admin.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + show["editUrl"])
                page.wait_for_selector('.timeline-cue[data-id="first"]')
                page.locator('.timeline-cue[data-id="first"]').dblclick()
                self.assertEqual(page.locator('[name="name"]').evaluate("node => document.activeElement === node"), True)
                # Drag top-corner handles to exact lengths at the default 16px/second zoom.
                for edge, dx, value in (("in", 24, 1.5), ("out", -16, 1)):
                    handle = page.locator(f'.timeline-cue[data-id="first"] .fade-{edge}')
                    handle.scroll_into_view_if_needed()
                    box = handle.bounding_box()
                    x = box["x"] + (2 if edge == "in" else box["width"] - 2)
                    y = box["y"] + 2
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + dx, y, steps=5)
                    page.mouse.up()
                    self.assertAlmostEqual(float(page.locator(f'[name="{"fade" if edge == "in" else "fadeOut"}"]').input_value()), value)
                page.locator('.timeline-cue[data-id="first"] .fade-out').press("ArrowLeft")
                self.assertAlmostEqual(float(page.locator('[name="fadeOut"]').input_value()), 0.9)
                page.click("#undo-edit")
                self.assertEqual(page.locator('[name="fadeOut"]').input_value(), "1")
                page.uncheck("#timeline-snap")
                lane = page.locator('.lane[data-lane="picture"]')
                lane.scroll_into_view_if_needed()
                left = lane.bounding_box()["x"]
                transfer = page.evaluate_handle("new DataTransfer()")
                page.locator('.timeline-cue[data-id="second"]').dispatch_event("dragstart", {"dataTransfer": transfer})
                lane.dispatch_event("drop", {"dataTransfer": transfer, "clientX": left + 6 * 16})
                self.assertEqual(page.locator('[name="start"]').input_value(), "6")
                page.fill("#preview-time", "7")
                page.locator("#preview-time").dispatch_event("change")
                page.wait_for_function("document.querySelector('#preview [data-cue=\"second\"]')?.style.opacity === '0.5'")
                self.assertEqual(page.locator('#preview [data-cue="first"]').evaluate("node => node.style.opacity"), "1")
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                stored = fixture.client.get(f"/presentations/api/shows/{show['id']}").json
                self.assertEqual(stored["cues"][1]["start"], 6)
                page.click("#open-controller")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                page.wait_for_selector('#preview [data-cue="first"] img')
                self.assertIn("latest saved", page.locator("#version-status").text_content())
                current = page.locator(".presenter-workspace > section").nth(0).bounding_box()
                upcoming = page.locator(".presenter-workspace > section").nth(1).bounding_box()
                grid = page.locator(".presenter-workspace > section").nth(2).bounding_box()
                self.assertLess(current["x"] + current["width"], upcoming["x"])
                self.assertLess(upcoming["x"] + upcoming["width"], grid["x"])
                self.assertEqual(current["y"], grid["y"])
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + show["url"])
                page.click('[data-action="start"]')
                page.wait_for_function("document.querySelector('#version-status').textContent.includes('On air')")
                page.click('[data-action="pause"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused')")
                page.fill("#seek-time", "7")
                page.locator("#seek-form button").click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"second\"]')?.style.opacity === '0.5'")
                updated = fixture.save(stored, [fixture.cue("replacement", kind="image", assetId=replacement["id"], duration=15)]).json
                page.wait_for_selector("#saved-changes", state="visible")
                self.assertEqual(page.locator('#cue-buttons [data-cue="first"]').count(), 1)
                self.assertEqual(viewer.locator('[data-cue="replacement"]').count(), 0)
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.click("#load-saved-changes")
                self.assertTrue(page.locator("#saved-changes").is_visible())
                page.once("dialog", lambda dialog: dialog.accept())
                page.click("#load-saved-changes")
                viewer.wait_for_selector('[data-cue="replacement"] img')
                page.wait_for_selector("#saved-changes", state="hidden")
                page.click('[data-action="stop"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Offline')")
                # A stopped controller automatically reloads saved edits, rather than showing the old broadcast.
                fixture.save(updated, [fixture.cue("latest", kind="image", assetId=first["id"])])
                page.wait_for_selector('#cue-buttons [data-cue="latest"]')
                self.assertIn("latest saved", page.locator("#version-status").text_content())
                page.set_viewport_size({"width": 540, "height": 900})
                current = page.locator(".presenter-workspace > section").nth(0).bounding_box()
                upcoming = page.locator(".presenter-workspace > section").nth(1).bounding_box()
                self.assertGreater(upcoming["y"], current["y"])
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 540)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_edit_multiple_video_local_hits_capture_split_and_playback(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            fixture.client.post("/live/api/tracks", json={"title": "Two films", "sections": [
                {"name": "First hold", "type": "pause"},
                {"name": "First scene", "bpm": 120, "num": 2, "bars": 1, "countIn": 0},
                {"name": "Second hold", "type": "pause"},
            ]})
            first = fixture.asset("youtube")
            second = fixture.asset("youtube")
            show = fixture.save(fixture.create(), [
                fixture.cue("first-film", kind="youtube", assetId=first["id"], offset=10, duration=10),
                fixture.cue("second-film", kind="youtube", assetId=second["id"], offset=90, start=10, duration=10),
            ]).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience = browser.new_context()
                errors = []
                def serve(route, client):
                    request = route.request
                    response = client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                admin.route("http://studio.test/**", lambda route: serve(route, fixture.client))
                audience.route("http://studio.test/**", lambda route: serve(route, fixture.guest))
                for context in (admin, audience):
                    context.add_init_script("""
                        window.YT = {Player: function(frame, options) {
                          frame.testPlayer = this;
                          this.time = 0; this.state = 2; this.at = performance.now();
                          this.getCurrentTime = () => this.time + (this.state === 1 ? (performance.now() - this.at) / 1000 : 0);
                          this.getPlayerState = () => this.state;
                          this.seekTo = time => { this.time = time; this.at = performance.now(); };
                          this.playVideo = () => { if (this.state !== 1) { this.state = 1; this.at = performance.now(); } };
                          this.pauseVideo = () => { this.time = this.getCurrentTime(); this.state = 2; this.at = performance.now(); };
                          this.mute = () => {}; this.unMute = () => {}; this.setVolume = () => {};
                          this.destroy = () => {};
                          setTimeout(() => options.events.onReady(), 0);
                        }};
                    """)
                    context.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
                page = admin.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + show["editUrl"])
                page.wait_for_function("document.querySelector('#music-track').options.length === 2")
                page.select_option("#music-track", index=1)
                for section, cue, position, mode in (
                    (0, "first-film", 2, "hold"), (1, "first-film", 3, "run"), (2, "second-film", 15, "hold")
                ):
                    page.select_option("#music-section", str(section))
                    page.select_option("#music-cue", cue)
                    page.fill("#preview-time", str(position))
                    page.locator("#preview-time").dispatch_event("change")
                    page.click("#capture-music-hit")
                    self.assertEqual(float(page.locator("#music-media-time").input_value()), 10 + position if section < 2 else 95)
                    page.select_option("#music-mode", mode)
                    page.click("#add-music-event")
                last = page.locator("#music-events li").last
                last.get_by_role("button", name="Edit event").click()
                self.assertEqual(page.locator("#music-media-time").input_value(), "95")
                page.fill("#music-media-time", "96")
                page.click("#preview-music-hit")
                page.wait_for_function("Number(document.querySelector('#preview-time').value) === 16")
                page.click("#add-music-event")
                self.assertEqual(page.locator("#music-events li").count(), 3)
                # A source-time hit after a split follows the second excerpt, and undo restores it.
                page.locator('[data-id="second-film"]').click()
                page.fill("#preview-time", "15")
                page.locator("#preview-time").dispatch_event("change")
                page.click("#split-cue")
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                stored = fixture.client.get(f"/presentations/api/shows/{show['id']}").json
                event = stored["music"]["events"][-1]
                self.assertNotEqual(event["cueId"], "second-film")
                self.assertEqual(event["mediaTime"], 96)
                page.click("#undo-edit")
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                page.click("#open-controller")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + show["url"])
                page.click('[data-action="start"]')
                transport = page.frame_locator("#music-performer")
                transport.locator("#btn-play").click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"first-film\"] iframe')?.testPlayer?.time === 12")
                viewer.wait_for_function("document.querySelector('[data-cue=\"first-film\"] iframe')?.testPlayer?.state === 2")
                transport.locator("#btn-next").click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"first-film\"] iframe')?.testPlayer?.state === 1")
                viewer.wait_for_function("document.querySelector('[data-cue=\"second-film\"] iframe')?.testPlayer?.time === 96")
                viewer.wait_for_function("document.querySelector('[data-cue=\"second-film\"] iframe')?.testPlayer?.state === 2")
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_chain_nested_musical_sets_and_pedal_intervals_in_one_transport(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            fixture.client.post("/live/api/tracks", json={"title": "Short set", "sections": [
                {"name": "Music", "bpm": 120, "num": 2, "bars": 1, "countIn": 0},
            ]})
            track = fixture.client.get("/presentations/api/music-tracks").json["tracks"][0]
            music = {"trackId": track["id"], "signature": track["signature"], "events": [
                {"id": "opening", "section": 0, "bar": 1, "beat": 1, "cueId": "slide", "mode": "run"}], "holds": {}}
            sets = [fixture.save(fixture.create(), [fixture.cue("slide", duration=0.2, fade=0)], music=music, title=name).json
                    for name in ("Opening", "Finale")]
            logo = fixture.asset()
            nested = fixture.save(fixture.create(), [], programme={"sets": [item["id"] for item in sets],
                                      "placeholderId": logo["id"]}).json
            concert = fixture.save(fixture.create(), [], programme={"sets": [nested["id"], sets[0]["id"]],
                                      "placeholderId": logo["id"]}).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience = browser.new_context()
                errors = []
                def serve(route, client):
                    request = route.request
                    response = client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                admin.route("http://studio.test/**", lambda route: serve(route, fixture.client))
                audience.route("http://studio.test/**", lambda route: serve(route, fixture.guest))
                for context in (admin, audience):
                    context.route("https://media.test/**", lambda route: route.fulfill(
                        content_type="image/png", body=base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")))
                page = admin.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + concert["controlUrl"])
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + concert["url"])
                page.click('[data-action="start"]')
                transport = page.frame_locator("#music-performer")
                transport.locator("#btn-play").click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"0/0/slide\"]')?.style.opacity === '1'")
                self.assertEqual(viewer.locator('[data-cue="0/interval-0"]').evaluate("node => node.style.opacity"), "0")
                viewer.wait_for_selector('[data-cue="0/interval-0"] img')
                page.wait_for_function("document.querySelector('#music-connection').textContent.includes('Waiting for pedal')")
                path = f"/presentations/api/shows/{concert['id']}/live"
                live = fixture.client.get(path).json
                self.assertEqual(len(live["show"]["music"]["track"]["sections"]), 5)
                self.assertFalse(live["playing"])
                # A real keyboard pedal inside the iframe advances from the logo to the next set.
                transport.locator("#btn-next").focus()
                transport.locator("#btn-next").press("ArrowRight")
                viewer.wait_for_selector('[data-cue="interval-0"] img')
                page.wait_for_function("document.querySelector('#music-connection').textContent.includes('Waiting for pedal')")
                live = fixture.client.get(path).json
                self.assertAlmostEqual(live["position"], 0.5)
                page.click('[data-action="next"]')
                viewer.wait_for_function("document.querySelector('[data-cue=\"1/slide\"]')?.style.opacity === '1'")
                # Rehearse backwards across nested-set boundaries from the mapped grid.
                page.locator('#cue-buttons [data-cue="0/0/slide"]').click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"0/0/slide\"]')?.style.opacity === '1'")
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_link_musical_events_and_drive_audience_from_real_transport(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            fixture.client.post("/live/api/tracks", json={"title": "Linked score", "sections": [
                {"name": "Opening", "bpm": 120, "num": 2, "den": 4, "bars": 2, "countIn": 0},
                {"name": "Wait", "type": "pause"},
                {"name": "Finale", "bpm": 120, "bars": 20, "countIn": 0},
            ]})
            logo = fixture.asset()
            show = fixture.save(fixture.create(), [
                fixture.cue("first", kind="image", assetId=logo["id"], duration=4, transition="fade", fade=1),
                fixture.cue("second", start=4, duration=20),
                fixture.cue("finale", start=24, duration=100),
            ]).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience = browser.new_context()
                errors = []
                def serve(route, client):
                    request = route.request
                    response = client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                admin.route("http://studio.test/**", lambda route: serve(route, fixture.client))
                audience.route("http://studio.test/**", lambda route: serve(route, fixture.guest))
                for context in (admin, audience):
                    context.route("https://media.test/**", lambda route: route.fulfill(
                        content_type="image/png", body=base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")))
                page = admin.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + show["editUrl"])
                page.wait_for_function("document.querySelector('#music-track').options.length === 2")
                page.select_option("#music-track", index=1)
                for section, bar, cue, mode in ((0, 1, "first", "hold"), (0, 2, "second", "run"), (2, 1, "finale", "run")):
                    page.select_option("#music-section", str(section))
                    page.fill("#music-bar", str(bar))
                    page.select_option("#music-cue", cue)
                    page.select_option("#music-mode", mode)
                    page.click("#add-music-event")
                self.assertEqual(page.locator("#music-events li").count(), 3)
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.click("#create-music-track")
                self.assertEqual(page.locator("#music-events li").count(), 3)
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                page.click("#open-controller")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + show["url"])
                page.click('[data-action="start"]')
                transport = page.frame_locator("#music-performer")
                transport.locator("#btn-play").wait_for()
                transport.locator("#btn-play").click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"first\"]')?.style.opacity === '1'")
                page.wait_for_function("document.querySelector('#clock').textContent.includes('0.0 s')")
                page.wait_for_function("document.querySelector('#music-connection').textContent.includes('Waiting for pedal')")
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused / held')")
                live_path = f"/presentations/api/shows/{show['id']}/live"
                paused = fixture.client.get(live_path).json
                self.assertEqual(paused["musicFrozen"], False)
                self.assertGreaterEqual(paused["position"], 4)
                viewer.wait_for_function("document.querySelector('[data-cue=\"second\"]')?.style.opacity === '1'")
                transport.locator("#btn-next").click()
                page.wait_for_function("document.querySelector('#clock').textContent.includes('24.')")
                page.click('[data-action="pause"]')
                page.wait_for_function("document.querySelector('#music-connection').textContent.includes('Paused.')")
                page.select_option("#placeholder-asset", str(logo["id"]))
                page.click("#save-placeholder")
                page.wait_for_function("!document.querySelector('#show-placeholder').disabled")
                page.click("#show-placeholder")
                viewer.wait_for_selector('[data-cue^="placeholder-"] img')
                page.click('[data-action="play"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Playing')")
                viewer.wait_for_function("!document.querySelector('[data-cue^=\"placeholder-\"]')")
                page.locator('#cue-buttons [data-cue="first"]').click()
                viewer.wait_for_function("document.querySelector('[data-cue=\"first\"]')?.style.opacity === '1'")
                page.click('[data-action="stop"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Offline')")
                saved = fixture.client.get(f"/presentations/api/shows/{show['id']}").json
                saved["music"]["holds"] = {"1": "continue"}
                self.assertEqual(fixture.save(saved, saved["cues"], music=saved["music"]).status_code, 200)
                page.click('[data-action="start"]')
                transport.locator("#btn-play").wait_for()
                transport.locator("#btn-play").click()
                page.wait_for_function("document.querySelector('#music-connection').textContent.includes('Waiting for pedal')")
                before = fixture.client.get(live_path).json
                self.assertTrue(before["playing"])
                page.wait_for_function(
                    "position => parseFloat(document.querySelector('#clock').textContent) > position + 0.3",
                    arg=before["position"])
                helper = page.evaluate("""async () => {
                    const {musicalVisualState} = await import('/assets/js/presentations/music.js');
                    const music = {events: [{id: 'one', section: 0, bar: 1, beat: 1}], holds: {'1': 'continue'}};
                    const point = {section: 1, bar: 1, beat: 1, phase: 'pause', playing: true, suspended: false};
                    return [musicalVisualState(music, point).playing,
                      musicalVisualState(music, {...point, suspended: true}).playing,
                      musicalVisualState(music, {...point, phase: 'countin'}).playing,
                      musicalVisualState(music, {...point, playing: false}).playing];
                }""")
                self.assertEqual(helper, [True, False, False, False])
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_build_concert_preview_and_broadcast_three_sets_with_intervals(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            logo = fixture.asset()
            sets = [fixture.save(fixture.create(), [fixture.cue(name, duration=0.3, fade=0,
                    advance="manual")], title=name).json for name in ("Opening", "Main set", "Encore")]
            concert = fixture.create()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience = browser.new_context()
                errors = []
                def serve(route, client):
                    request = route.request
                    response = client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                admin.route("http://studio.test/**", lambda route: serve(route, fixture.client))
                audience.route("http://studio.test/**", lambda route: serve(route, fixture.guest))
                for context in (admin, audience):
                    context.route("https://media.test/**", lambda route: route.fulfill(
                        content_type="image/png", body=base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")))
                page = admin.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + concert["editUrl"])
                page.wait_for_function("document.querySelector('#status').textContent.includes('Drag media')")
                page.check("#concert-mode")
                for item in sets:
                    page.select_option("#concert-set", str(item["id"]))
                    page.click("#add-concert-set")
                self.assertEqual(page.locator("#concert-sets li").count(), 3)
                page.locator("#concert-sets li").last.get_by_role("button", name="Move up").click()
                self.assertEqual(page.locator("#concert-sets li a").all_text_contents(), ["Opening", "Encore", "Main set"])
                page.locator("#concert-sets li").nth(1).get_by_role("button", name="Move down").click()
                page.select_option("#concert-placeholder", str(logo["id"]))
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                page.reload()
                page.wait_for_function("document.querySelector('#concert-mode').checked")
                self.assertTrue(page.locator("#timeline-panel").is_hidden())
                page.click("#load-concert-preview")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Concert preview loaded')")
                page.click("#preview-play")
                page.wait_for_selector("#preview img")
                page.wait_for_function("document.querySelector('#preview-time').value === '0.4'")
                page.wait_for_function("document.querySelector('#preview-play').textContent === 'Play preview'")
                self.assertEqual(page.locator("#preview-time").input_value(), "0.4")
                page.click("#preview-next")
                page.wait_for_function("Number(document.querySelector('#preview-time').value) >= 0.7")
                page.click("#open-controller")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                viewer = audience.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + concert["url"])
                page.click('[data-action="start"]')
                viewer.wait_for_selector('#preview [data-cue*="interval-"] img')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused / held')")
                self.assertIn("0.4 s", page.locator("#clock").text_content())
                self.assertEqual(page.locator("#cue-buttons .cue-card").count(), 5)
                self.assertIn("Main set", page.locator("#up-next").text_content())
                page.click('[data-action="next"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('0.8 s')")
                self.assertIn("Encore", page.locator("#up-next").text_content())
                page.click('[data-action="next"]')
                page.wait_for_function("document.querySelector('#clock').textContent.includes('1.1 s')")
                self.assertIn("No more slides", page.locator("#up-next-status").text_content())
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_long_cues_edit_history_split_duplicate_snap_and_preflight(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            yt = fixture.asset("youtube")
            image = fixture.asset("image")
            show = fixture.save(fixture.create(), [
                fixture.cue("film", kind="youtube", assetId=yt["id"], duration=600,
                            offset=20, advance="manual", fadeOut=1),
                fixture.cue("black", start=610),
                fixture.cue("image", kind="image", assetId=image["id"], start=620),
            ]).json
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context(viewport={"width": 1440, "height": 1100})
                errors = []
                broken = {"image": False}
                def serve(route):
                    request = route.request
                    response = fixture.client.open(urlsplit(request.url).path, method=request.method,
                        data=request.post_data_buffer, content_type=request.headers.get("content-type"))
                    route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                    response.close()
                def serve_image(route):
                    if broken["image"]:
                        route.fulfill(status=404, body="Missing")
                    else:
                        route.fulfill(content_type="image/png", body=base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
                context.route("http://studio.test/**", serve)
                context.route("https://media.test/**", serve_image)
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(
                    content_type="text/html", body="<html></html>"))
                context.add_init_script("""
                    window.youtubeDuration = 2000;
                    window.YT = {Player: function(frame, config) {
                        this.getDuration = () => window.youtubeDuration;
                        this.getCurrentTime = () => 0;
                        this.getPlayerState = () => 2;
                        this.seekTo = () => {}; this.playVideo = () => {}; this.pauseVideo = () => {};
                        this.mute = () => {}; this.unMute = () => {}; this.setVolume = () => {}; this.destroy = () => {};
                        if (!window.freezeYouTube) setTimeout(() => config.events.onReady({target:this}), 0);
                    }};
                """)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test" + show["editUrl"])
                page.wait_for_function("document.querySelectorAll('.timeline-cue').length === 3")
                page.locator('[data-id="film"]').click()
                self.assertEqual(page.locator('[name="duration"]').input_value(), "600")
                page.click("#split-cue")
                self.assertEqual(page.locator(".timeline-cue").count(), 3)
                self.assertIn("at least 0.1 seconds", page.locator("#status").text_content())
                page.fill("#preview-time", "120")
                page.locator("#preview-time").press("Tab")
                page.click("#split-cue")
                self.assertEqual(page.locator(".timeline-cue").count(), 4)
                self.assertEqual(page.locator('[name="duration"]').input_value(), "480")
                self.assertEqual(page.locator('[name="offset"]').input_value(), "140")
                self.assertEqual(page.locator('[name="advance"]').input_value(), "manual")
                self.assertEqual(page.locator('[name="transition"]').input_value(), "cut")
                page.click("#undo-edit")
                self.assertEqual(page.locator(".timeline-cue").count(), 3)
                page.click("#redo-edit")
                self.assertEqual(page.locator(".timeline-cue").count(), 4)
                page.locator('[data-lane="picture"] button').filter(has_text="film").last.click()
                page.click("#duplicate-cue")
                self.assertEqual(page.locator('[name="start"]').input_value(), "625")
                self.assertEqual(page.locator('[name="duration"]').input_value(), "480")
                self.assertEqual(page.locator('[name="offset"]').input_value(), "140")
                page.locator("#undo-edit").focus()
                page.keyboard.press("Control+z")
                self.assertEqual(page.locator(".timeline-cue").count(), 4)
                page.keyboard.press("Control+Shift+z")
                self.assertEqual(page.locator(".timeline-cue").count(), 5)
                page.click("#undo-edit")
                # Snap a cue just beyond an adjacent edge; Shift keeps the exact drop.
                lane = page.locator('[data-lane="picture"]')
                transfer = page.evaluate_handle("new DataTransfer()")
                page.locator('[data-id="black"]').dispatch_event("dragstart", {"dataTransfer": transfer})
                left = lane.bounding_box()["x"]
                lane.dispatch_event("drop", {"dataTransfer": transfer, "clientX": left + 600.25 * 16})
                self.assertEqual(page.locator('[name="start"]').input_value(), "600")
                page.locator('[data-id="black"]').dispatch_event("dragstart", {"dataTransfer": transfer})
                lane.dispatch_event("drop", {"dataTransfer": transfer, "clientX": left + 600.25 * 16, "shiftKey": True})
                self.assertEqual(page.locator('[name="start"]').input_value(), "600.25")
                page.click("#undo-edit")
                self.assertEqual(page.locator('[name="start"]').input_value(), "600")
                page.uncheck("#timeline-snap")
                page.locator('[data-id="black"]').dispatch_event("dragstart", {"dataTransfer": transfer})
                lane.dispatch_event("drop", {"dataTransfer": transfer, "clientX": left + 600.25 * 16})
                self.assertEqual(page.locator('[name="start"]').input_value(), "600.25")
                page.click("#undo-edit")
                page.check("#timeline-snap")
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                stored = fixture.client.get(f"/presentations/api/shows/{show['id']}").json
                halves = sorted((cue for cue in stored["cues"] if cue["kind"] == "youtube"), key=lambda cue: cue["start"])
                self.assertEqual([cue["duration"] for cue in halves], [120, 480])
                self.assertEqual([cue["advance"] for cue in halves], ["auto", "manual"])
                self.assertEqual([cue["fadeOut"] for cue in halves], [0, 1])
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('0 failed, 0 warnings')")
                self.assertIn("YouTube embed ready", page.locator("#preflight-results").text_content())
                page.click("#test-sound")
                page.wait_for_function("document.querySelector('#sound-check-status').textContent.includes('Test tone sent')")
                # History survives saving, but redo is discarded by a new edit.
                page.click("#undo-edit")
                page.fill("#show-title", "Replacement title")
                self.assertTrue(page.locator("#redo-edit").is_disabled())
                page.once("dialog", lambda dialog: dialog.accept())
                with fixture.app.app_context():
                    item = fixture.db.session.get(fixture.models["PresentationAsset"], image["id"])
                    item.url += "?missing"
                    fixture.db.session.commit()
                broken["image"] = True
                page.reload()
                page.wait_for_function("document.querySelectorAll('.timeline-cue').length === 4")
                page.evaluate("window.youtubeDuration = 10")
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('Media checks finished')")
                self.assertIn("2 failed", page.locator("#preflight-status").text_content(),
                              page.locator("#preflight-results").text_content())
                self.assertIn("extends beyond", page.locator("#preflight-results").text_content())
                self.assertIn("Image could not load", page.locator("#preflight-results").text_content())
                with fixture.app.app_context():
                    item = fixture.db.session.get(fixture.models["PresentationAsset"], image["id"])
                    item.url = item.url.replace("?missing", "?restored")
                    fixture.db.session.commit()
                broken["image"] = False
                page.reload()
                page.wait_for_function("document.querySelectorAll('.timeline-cue').length === 4")
                page.evaluate("window.youtubeDuration = 0")
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('0 failed, 1 warnings')")
                page.evaluate("window.freezeYouTube = true")
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('Checking media')")
                page.click("#cancel-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('Results are incomplete')")
                self.assertEqual(page.locator(".preflight-probe").count(), 0)
                page.click("#open-controller")
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('0 failed, 0 warnings')")
                self.assertEqual(page.locator(".preflight-probe").count(), 0)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_exact_fades_crossfade_and_native_video_transport(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                context = browser.new_context()
                errors = []
                def serve(route):
                    response = fixture.client.get(urlsplit(route.request.url).path)
                    route.fulfill(status=response.status_code, body=response.data,
                                  content_type=response.content_type)
                    response.close()
                context.route("http://studio.test/**", serve)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test/presentations/")
                result = page.evaluate("""async () => {
                    const {PresentationPlayer} = await import('/assets/js/presentations/player.js');
                    const host = document.createElement('div');
                    const status = document.createElement('p');
                    const unlock = document.createElement('button');
                    document.body.replaceChildren(host, status, unlock);
                    const player = new PresentationPlayer(host, status, unlock);
                    const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
                    const first = {id:'first', name:'First', kind:'image', lane:'picture', start:0, duration:2,
                        transition:'fade', fade:0.8, fadeOut:0.5, offset:0, volume:0, asset:{url:image}};
                    const caption = {id:'caption', name:'Caption', kind:'text', lane:'text', start:0, duration:2,
                        transition:'cut', fade:0, fadeOut:0, offset:0, volume:0, text:'<b>literal</b>',
                        color:'#ffffff', position:'center', fontSize:6};
                    const show = {cues:[first,
                        {...first, id:'second', start:2, transition:'crossfade', fade:1, fadeOut:0},
                        caption, {...caption, id:'caption2', start:2, text:'Next', transition:'crossfade', fade:1}]};
                    const opacity = id => Number(host.querySelector(`[data-cue="${id}"]`).style.opacity);
                    player.render(show, 0.2, false);
                    const fadeIn = opacity('first');
                    const literal = host.querySelector('.caption').textContent;
                    const injected = host.querySelector('.caption b') !== null;
                    player.render(show, 1.8, false);
                    const fadeOut = opacity('first');
                    player.render(show, 2.5, false);
                    const blend = [opacity('first'), opacity('second'), opacity('caption'), opacity('caption2')];
                    player.render(show, 2.5, false, true);
                    const black = [...host.children].every(node => node.style.opacity === '0');
                    player.clear();

                    const canvas = document.createElement('canvas');
                    canvas.width = canvas.height = 32;
                    const drawing = canvas.getContext('2d');
                    const stream = canvas.captureStream(20);
                    const recorder = new MediaRecorder(stream, {mimeType:'video/webm;codecs=vp8'});
                    const chunks = [];
                    recorder.ondataavailable = event => chunks.push(event.data);
                    const stopped = new Promise(resolve => recorder.onstop = resolve);
                    recorder.start();
                    const drawingTimer = setInterval(() => {
                        drawing.fillStyle = `rgb(${Math.floor(Math.random()*255)},0,0)`;
                        drawing.fillRect(0,0,32,32);
                    }, 50);
                    await new Promise(resolve => setTimeout(resolve, 2200));
                    recorder.stop();
                    await stopped;
                    clearInterval(drawingTimer);
                    stream.getTracks().forEach(track => track.stop());
                    const url = URL.createObjectURL(new Blob(chunks, {type:'video/webm'}));
                    const videoShow = {cues:[{...first, id:'movie', kind:'video', duration:1.5,
                        transition:'cut', fadeOut:0, volume:0.37, asset:{url}}]};
                    player.render(videoShow, 0, false);
                    const video = host.querySelector('video');
                    await new Promise((resolve, reject) => {
                        if (video.readyState >= 1) resolve();
                        else {video.addEventListener('loadedmetadata', resolve, {once:true});
                              video.addEventListener('error', () => reject(new Error('Native WebM failed')), {once:true});}
                    });
                    await new Promise(resolve => setTimeout(resolve, 750));
                    player.render(videoShow, 0.5, false);
                    const sought = video.currentTime;
                    player.render(videoShow, 0.5, true);
                    await new Promise(resolve => setTimeout(resolve, 200));
                    const running = !video.paused;
                    player.render(videoShow, 0.7, false);
                    const paused = video.paused;
                    const volume = video.volume;
                    videoShow.cues[0].muted = true;
                    player.render(videoShow, 0.7, false);
                    const cueMuted = video.muted;
                    videoShow.cues[0].muted = false;
                    player.render(videoShow, 0.7, false);
                    const restored = !video.muted && video.volume === 0.37;
                    player.render(videoShow, 0.7, false, true);
                    const muted = video.muted;
                    player.clear();
                    const overlapShow = {cues:[
                        {...first, id:'overlap-a', kind:'video', start:0, duration:1.5,
                            transition:'cut', fade:0, fadeOut:0, volume:0.4, asset:{url}},
                        {...first, id:'overlap-b', kind:'video', start:0.5, duration:1.5,
                            transition:'cut', fade:0, fadeOut:0, volume:0.8, asset:{url}},
                        {...caption, id:'overlap-text-a', duration:1.5},
                        {...caption, id:'overlap-text-b', start:0.5, duration:1.5}]};
                    player.render(overlapShow, 0.75, false);
                    await Promise.all([...host.querySelectorAll('video')].map(video => new Promise((resolve, reject) => {
                        if (video.readyState >= 1) resolve();
                        else { video.addEventListener('loadedmetadata', resolve, {once:true});
                            video.addEventListener('error', () => reject(new Error('Overlap video failed')), {once:true}); }
                    })));
                    player.render(overlapShow, 0.75, true);
                    const overlap = [opacity('overlap-a'), opacity('overlap-b'),
                        opacity('overlap-text-a'), opacity('overlap-text-b')];
                    const overlapVolumes = [...host.querySelectorAll('video')].map(video => video.volume);
                    await new Promise(resolve => setTimeout(resolve, 200));
                    const bothRunning = [...host.querySelectorAll('video')].every(video => !video.paused);
                    player.render(overlapShow, 0.75, false);
                    const bothPaused = [...host.querySelectorAll('video')].every(video => video.paused);
                    player.clear();
                    const buffer = new ArrayBuffer(44 + 8000 * 2 * 3);
                    const data = new DataView(buffer);
                    const word = (at, text) => [...text].forEach((value, i) => data.setUint8(at + i, value.charCodeAt(0)));
                    word(0, 'RIFF'); data.setUint32(4, buffer.byteLength - 8, true); word(8, 'WAVE');
                    word(12, 'fmt '); data.setUint32(16, 16, true); data.setUint16(20, 1, true);
                    data.setUint16(22, 1, true); data.setUint32(24, 8000, true); data.setUint32(28, 16000, true);
                    data.setUint16(32, 2, true); data.setUint16(34, 16, true);
                    word(36, 'data'); data.setUint32(40, buffer.byteLength - 44, true);
                    const soundUrl = URL.createObjectURL(new Blob([buffer], {type:'audio/wav'}));
                    const audioShow = {cues:overlapShow.cues.filter(cue => cue.kind === 'video')
                        .map(cue => ({...cue, kind:'audio', lane:'audio', asset:{url:soundUrl}}))};
                    player.render(audioShow, 0.75, false);
                    await Promise.all([...host.querySelectorAll('audio')].map(audio => new Promise((resolve, reject) => {
                        audio.addEventListener('loadedmetadata', resolve, {once:true});
                        audio.addEventListener('error', () => reject(new Error('Overlap audio failed')), {once:true});
                        if (audio.readyState >= 1) resolve();
                    })));
                    player.render(audioShow, 0.75, false);
                    const audioVolumes = [...host.querySelectorAll('audio')].map(audio => audio.volume);
                    player.clear();
                    URL.revokeObjectURL(soundUrl);
                    URL.revokeObjectURL(url);
                    return {fadeIn, fadeOut, blend, black, literal, injected, sought, running, paused, muted,
                        volume, cueMuted, restored, overlap, overlapVolumes, bothRunning, bothPaused, audioVolumes};
                }""")
                self.assertAlmostEqual(result["fadeIn"], 0.25)
                self.assertAlmostEqual(result["fadeOut"], 0.4)
                self.assertEqual(result["blend"], [1, 0.5, 0.5, 0.5])
                self.assertEqual(result["literal"], "<b>literal</b>")
                self.assertFalse(result["injected"])
                self.assertTrue(result["black"])
                self.assertAlmostEqual(result["sought"], 0.5, delta=0.03)
                self.assertTrue(result["running"])
                self.assertTrue(result["paused"])
                self.assertTrue(result["muted"])
                self.assertEqual(result["volume"], 0.37)
                self.assertTrue(result["cueMuted"])
                self.assertTrue(result["restored"])
                self.assertEqual(result["overlap"], [1, 0.25, 0.75, 0.25])
                for values in (result["overlapVolumes"], result["audioVolumes"]):
                    self.assertAlmostEqual(values[0], 0.3)
                    self.assertAlmostEqual(values[1], 0.2)
                self.assertTrue(result["bothRunning"])
                self.assertTrue(result["bothPaused"])
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_create_drag_edit_save_broadcast_layers_and_revoke(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

        fixture = PresentationTests()
        fixture.setUpClass()
        fixture.setUp()
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000 * 8)
        try:
            asset = fixture.asset("audio", duration=8)
            with fixture.app.app_context():
                item = fixture.db.session.get(fixture.models["PresentationAsset"], asset["id"])
                item.url = "https://media.test/music.wav"
                fixture.db.session.commit()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                except Error as error:
                    self.skipTest(f"Chrome is unavailable: {error}")
                admin_context = browser.new_context(viewport={"width": 1440, "height": 1100})
                audience_context = browser.new_context(viewport={"width": 1280, "height": 720})
                errors = []
                network = {"down": False}
                mock_yt = """
                  window.YT = {Player: function(frame, config) {
                    this.time = 0; this.state = 2; this.at = performance.now();
                    this.getCurrentTime = () => this.time + (this.state === 1 ? (performance.now() - this.at) / 1000 : 0);
                    this.getPlayerState = () => this.state;
                    this.seekTo = time => { this.time = time; this.at = performance.now(); };
                    this.playVideo = () => { if (this.state !== 1) { this.state = 1; this.at = performance.now(); } };
                    this.pauseVideo = () => { this.time = this.getCurrentTime(); this.state = 2; this.at = performance.now(); };
                    window.testYouTube = this;
                    this.mute = () => {this.muted = true;}; this.unMute = () => {this.muted = false;};
                    this.setVolume = volume => {this.volume = volume;}; this.destroy = () => {};
                    setTimeout(() => config.events.onReady({target: this}), 0);
                  }};
                """
                def route_request(route, client):
                    if client is fixture.guest and network["down"]:
                        route.abort()
                        return
                    request = route.request
                    path = urlsplit(request.url).path
                    response = client.open(path, method=request.method, data=request.post_data_buffer,
                                           content_type=request.headers.get("content-type"),
                                           headers={"X-Requested-With": request.headers.get("x-requested-with", "")})
                    route.fulfill(status=response.status_code, body=response.data,
                                  headers={"content-type": response.content_type})
                    response.close()

                for context in (admin_context, audience_context):
                    context.add_init_script(mock_yt)
                    context.route("https://media.test/image.png", lambda route: route.fulfill(
                        content_type="image/png", body=png))
                    context.route("https://media.test/music.wav", lambda route: route.fulfill(
                        content_type="audio/wav", body=wav.getvalue()))
                    context.route("https://www.youtube-nocookie.com/**", lambda route: route.fulfill(
                        content_type="text/html", body="<html></html>"))
                admin_context.route("http://studio.test/**", lambda route: route_request(route, fixture.client))
                audience_context.route("http://studio.test/**", lambda route: route_request(route, fixture.guest))
                admin_context.route("https://api.cloudinary.com/**", lambda route: route.fulfill(
                    content_type="application/json",
                    body='{"public_id":"' + f"jackcapstaff/presentations/{fixture.admin_id}/" + "a" * 32 + '"}'))
                page = admin_context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://studio.test/live/home/")
                page.get_by_role("link", name="Open Media Centre").click()
                page.wait_for_url("**/presentations/")
                self.assertEqual(page.locator("h1").text_content(), "Media Centre")
                page.fill("#new-title", "Winter concert")
                page.click("#new-show button")
                page.wait_for_url("**/edit/")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Drag media')")
                with patch.dict(os.environ, {"CLOUDINARY_URL": "cloudinary://test:test@test"}), \
                     patch("presentationstudio.cloudinary.config", return_value=SimpleNamespace(
                         cloud_name="test", api_key="test-key", api_secret="test-secret")), \
                     patch("presentationstudio.cloudinary.api.resource", return_value={
                         "resource_type": "image", "bytes": len(png), "format": "png", "secure_url": "https://media.test/image.png",
                     }):
                    page.locator("#media-files").set_input_files({
                        "name": "Opening image.png", "mimeType": "image/png", "buffer": png,
                    })
                    page.wait_for_function("document.querySelector('#status').textContent.includes('Uploads saved')")
                image_card = page.locator(".asset-card").filter(has_text="Opening image.png")
                transfer = page.evaluate_handle("new DataTransfer()")
                image_card.dispatch_event("dragstart", {"dataTransfer": transfer})
                picture_lane = page.locator('.lane[data-lane="picture"]')
                box = picture_lane.bounding_box()
                picture_lane.dispatch_event("drop", {"dataTransfer": transfer, "clientX": box["x"]})
                self.assertEqual(picture_lane.locator("button").count(), 1, errors)
                page.fill('[name="duration"]', "2")
                page.select_option('[name="advance"]', "manual")
                page.select_option('[name="transition"]', "fade")
                page.fill('[name="fade"]', "0.4")
                page.click("#cue-form button[type=submit]")
                page.click("#add-blank")
                page.fill('[name="duration"]', "2")
                page.click("#cue-form button[type=submit]")
                page.click("#add-text")
                page.fill('[name="text"]', "Band title <b>literal</b>")
                page.fill('[name="duration"]', "6")
                page.select_option('[name="position"]', "center")
                page.select_option('[name="transition"]', "fade")
                page.click("#cue-form button[type=submit]")
                page.locator(".asset-card").filter(has_text="Test audio").get_by_role("button", name="Add to audio").click()
                page.fill("#youtube-url", "https://youtu.be/abcdefghijk")
                page.click("#youtube-form button")
                page.wait_for_function("document.querySelector('#status').textContent.includes('YouTube video added')")
                page.locator(".asset-card").filter(has_text="YouTube video").get_by_role("button", name="Add to picture").click()
                page.fill('[name="duration"]', "2")
                page.select_option('[name="transition"]', "crossfade")
                self.assertTrue(page.locator("#cue-muted").is_checked())
                page.locator('[name="volume"]').evaluate("""element => {
                    element.value = '0.37'; element.dispatchEvent(new Event('input', {bubbles:true}));
                }""")
                self.assertFalse(page.locator("#cue-muted").is_checked())
                self.assertEqual(page.locator("#cue-volume-value").text_content(), "37%")
                page.check("#cue-muted")
                page.click("#cue-form button[type=submit]")
                self.assertTrue(page.locator("#cue-muted").is_checked())
                page.uncheck("#cue-muted")
                self.assertEqual(page.locator('[name="volume"]').input_value(), "0.37")
                page.click("#cue-form button[type=submit]")
                # Numeric move is available alongside drag/drop for exact positioning.
                self.assertEqual(page.locator('[name="start"]').input_value(), "4")
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = async (...args) => {
                        const result = await original(...args);
                        if (args[1]?.method === 'PUT' && !window.saveDelayed) {
                            window.saveDelayed = true;
                            await new Promise(resolve => window.finishSave = resolve);
                        }
                        return result;
                    };
                }""")
                page.click("#save-show")
                page.wait_for_function("window.saveDelayed")
                page.fill("#show-title", "Winter concert - revised")
                page.evaluate("window.finishSave()")
                page.wait_for_function("document.querySelector('#status').textContent.includes('New edits remain unsaved')")
                page.click("#save-show")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
                page.reload()
                page.wait_for_function("document.querySelectorAll('.timeline-cue').length === 5")
                self.assertEqual(page.locator("#show-title").input_value(), "Winter concert - revised")
                page.locator('[data-lane="picture"] button').last.click()
                self.assertEqual(page.locator('[name="volume"]').input_value(), "0.37")
                self.assertFalse(page.locator("#cue-muted").is_checked())
                ruler = page.locator("#time-ruler")
                ruler.scroll_into_view_if_needed()
                box = ruler.bounding_box()
                page.mouse.click(box["x"] + 4.8 * 16, box["y"] + 18)
                page.wait_for_function("document.querySelector('#timeline-position').textContent === '4.8 s'")
                self.assertEqual(page.locator("#preview-time").input_value(), "4.8")
                page.wait_for_function("window.testYouTube && window.testYouTube.volume === 37 && !window.testYouTube.muted")
                page.click("#preview-play")
                page.wait_for_function("document.querySelector('#preview-play').textContent === 'Pause preview'")
                ruler.scroll_into_view_if_needed()
                box = ruler.bounding_box()
                page.mouse.move(box["x"] + 3 * 16, box["y"] + 18)
                page.mouse.down()
                page.mouse.move(box["x"] + 5.2 * 16, box["y"] + 18, steps=6)
                page.mouse.up()
                page.wait_for_function("document.querySelector('#timeline-position').textContent === '5.2 s'")
                self.assertEqual(page.locator("#preview-play").text_content(), "Play preview")
                page.locator("#timeline-zoom").evaluate("""element => {
                    element.value = '32'; element.dispatchEvent(new Event('input'));
                }""")
                page.wait_for_function("Math.abs(parseFloat(document.querySelector('#timeline-playhead').style.left) - 166.4) < 0.1")
                handle = page.locator("#timeline-playhead")
                handle.scroll_into_view_if_needed()
                box = handle.bounding_box()
                page.mouse.move(box["x"] + 7, box["y"] + 7)
                page.mouse.down()
                page.mouse.move(box["x"] + 7 - 32, box["y"] + 7, steps=4)
                page.mouse.up()
                page.wait_for_function("document.querySelector('#timeline-position').textContent === '4.2 s'")
                page.locator("#timeline-scrub").focus()
                page.keyboard.press("Home")
                page.wait_for_function("document.querySelector('#timeline-position').textContent === '0.0 s'")
                page.keyboard.press("End")
                page.wait_for_function("document.querySelector('#timeline-position').textContent === '8.0 s'")
                page.keyboard.press("Home")
                self.assertEqual(page.locator('[data-lane="text"] button').count(), 1)
                self.assertEqual(page.locator('[data-lane="audio"] button').count(), 1)
                page.click("#preview-play")
                page.wait_for_function("document.querySelector('#preview .caption span')?.textContent.includes('Band title')")
                self.assertEqual(page.locator("#preview .caption b").count(), 0)
                page.click("#preview-play")
                page.click("#open-controller")
                page.wait_for_url("**/control/")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                page.click("#run-preflight")
                page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('0 failed, 1 warnings')")
                self.assertIn("initial playback data", page.locator("#preflight-results").text_content())
                page.select_option("#placeholder-asset", label="Opening image.png")
                page.click("#save-placeholder")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Broadcast updated')")
                page.reload()
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
                self.assertEqual(page.locator("#placeholder-asset option:checked").text_content(), "Opening image.png")
                audience_url = page.locator("#audience-link").get_attribute("href")
                viewer = audience_context.new_page()
                viewer.on("pageerror", lambda error: errors.append(str(error)))
                viewer.goto("http://studio.test" + audience_url)
                viewer.wait_for_function("document.querySelector('#connection-status').textContent.includes('Waiting')")
                page.click('[data-action="start"]')
                viewer.wait_for_selector("#preview .caption span")
                self.assertIn("Band title", viewer.locator(".caption span").text_content())
                self.assertEqual(viewer.locator("nav").count(), 0)
                self.assertEqual(viewer.locator("#preview img").count(), 1)
                self.assertEqual(viewer.locator("#preview audio").count(), 1)
                viewer.wait_for_function("""() => {
                    const audio = document.querySelector('#preview audio');
                    return (audio && !audio.paused && !audio.muted) || !document.querySelector('#allow-playback').hidden;
                }""")
                if viewer.locator("#allow-playback").is_visible():
                    viewer.click("#allow-playback")
                viewer.wait_for_function("""() => {
                    const audio = document.querySelector('#preview audio');
                    return audio && !audio.paused && !audio.muted && audio.currentTime > 0;
                }""")
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused / held')")
                self.assertIn("2.0 s", page.locator("#clock").text_content())
                self.assertEqual(page.locator("#cue-buttons .cue-card").count(), 4)
                self.assertIn("Black cue", page.locator("#up-next").text_content())
                self.assertGreater(page.locator('#cue-buttons [aria-current="true"]').count(), 0)
                network["down"] = True
                viewer.wait_for_function("document.querySelector('#connection-status').textContent.includes('Reconnecting')")
                viewer.wait_for_function("document.querySelectorAll('#preview .media-layer').length === 0")
                network["down"] = False
                viewer.wait_for_selector("#preview img")
                viewer.wait_for_function("document.querySelector('#preview audio').paused")
                page.locator("#up-next").get_by_role("button", name="Go to Black cue").click()
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Playing')")
                viewer.wait_for_selector("#preview iframe", timeout=6000)
                page.click('[data-action="pause"]')
                page.fill("#seek-time", "4.5")
                page.click("#seek-form button")
                viewer.wait_for_selector("#preview iframe")
                page.click('[data-action="black"]')
                viewer.wait_for_function("[...document.querySelectorAll('#preview .media-layer')].every(el => el.style.opacity === '0')")
                page.click('[data-action="black"]')
                viewer.wait_for_function("[...document.querySelectorAll('#preview .media-layer')].some(el => Number(el.style.opacity) > 0)")
                page.click("#show-placeholder")
                page.wait_for_function("document.querySelector('#clock').textContent.includes('Placeholder / paused')")
                viewer.wait_for_selector('#preview [data-cue^="placeholder-"] img')
                self.assertEqual(viewer.locator("#preview audio").count(), 0)
                self.assertEqual(viewer.locator("#preview .caption").count(), 0)
                self.assertEqual(viewer.locator("#cue-buttons, #up-next").count(), 0)
                page.click('[data-action="play"]')
                viewer.wait_for_selector("#preview iframe")
                page.click('[data-action="pause"]')
                self.assertEqual(viewer.locator('#preview [data-cue^="placeholder-"]').count(), 0)
                # Grid navigation can return to an earlier slide and then jump ahead.
                page.locator("#cue-buttons").get_by_role("button", name="Go to Opening image.png").click()
                viewer.wait_for_selector("#preview img")
                page.locator("#cue-buttons").get_by_role("button", name="Go to YouTube video").click()
                viewer.wait_for_selector("#preview iframe")
                page.click('[data-action="pause"]')
                page.once("dialog", lambda dialog: dialog.accept())
                page.click("#revoke-link")
                viewer.wait_for_function("document.querySelector('#connection-status').textContent.includes('ended')")
                self.assertEqual(fixture.guest.get(audience_url).status_code, 404)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()
