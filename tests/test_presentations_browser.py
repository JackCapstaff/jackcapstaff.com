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
                        transition:'cut', fadeOut:0, volume:0, asset:{url}}]};
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
                    player.render(videoShow, 0.7, false, true);
                    const muted = video.muted;
                    player.clear();
                    URL.revokeObjectURL(url);
                    return {fadeIn, fadeOut, blend, black, literal, injected, sought, running, paused, muted};
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
                    this.mute = () => {}; this.unMute = () => {}; this.setVolume = () => {}; this.destroy = () => {};
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
                page.goto("http://studio.test/presentations/")
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
                self.assertEqual(page.locator('[data-lane="text"] button').count(), 1)
                self.assertEqual(page.locator('[data-lane="audio"] button').count(), 1)
                page.click("#preview-play")
                page.wait_for_function("document.querySelector('#preview .caption span')?.textContent.includes('Band title')")
                self.assertEqual(page.locator("#preview .caption b").count(), 0)
                page.click("#preview-play")
                page.click("#open-controller")
                page.wait_for_url("**/control/")
                page.wait_for_function("document.querySelector('#status').textContent.includes('Ready')")
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
                network["down"] = True
                viewer.wait_for_function("document.querySelector('#connection-status').textContent.includes('Reconnecting')")
                viewer.wait_for_function("document.querySelectorAll('#preview .media-layer').length === 0")
                network["down"] = False
                viewer.wait_for_selector("#preview img")
                viewer.wait_for_function("document.querySelector('#preview audio').paused")
                page.click('[data-action="next"]')
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
                page.once("dialog", lambda dialog: dialog.accept())
                page.click("#revoke-link")
                viewer.wait_for_function("document.querySelector('#connection-status').textContent.includes('ended')")
                self.assertEqual(fixture.guest.get(audience_url).status_code, 404)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            fixture.tearDown()
            fixture.tearDownClass()
