"""Concert operator controls and fully disconnected local export."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class ConcertToolsBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright, Error
        from test_presentations import PresentationTests
        from test_presentation_import_browser import PNG
        self.png = PNG
        self.fixture = PresentationTests()
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDownClass)
        self.addCleanup(self.fixture.tearDown)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        try:
            self.browser = self.playwright.chromium.launch(headless=True, channel="chrome")
        except Error as error:
            self.skipTest(str(error))
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(accept_downloads=True)
        self.context.route("https://studio.test/**", self.serve)
        self.context.route("https://media.test/**", lambda route: route.fulfill(content_type="image/png",
            body=self.png, headers={"Access-Control-Allow-Origin": "*"}))
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        asset = self.fixture.asset()
        self.show = self.fixture.save(self.fixture.create(), [
            self.fixture.cue("Entrance", kind="image", assetId=asset["id"], duration=30, note="Wait for conductor", marker=True),
            self.fixture.cue("Finale", start=30, duration=30, marker=True)]).json

    def serve(self, route):
        request = route.request
        response = self.fixture.client.open(urlsplit(request.url).path, method=request.method,
            data=request.post_data_buffer, content_type=request.headers.get("content-type"))
        route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        response.close()

    def test_operator_countdown_emergency_calibration_and_keyboard_pedal(self):
        self.context.add_init_script("""window.testMidi = {id:'concert-pedal'};
          navigator.requestMIDIAccess = async () => ({inputs:new Map([['concert-pedal',window.testMidi]])});""")
        self.page.goto("https://studio.test" + self.show["controlUrl"])
        self.page.wait_for_function("document.querySelector('#status').textContent.includes('Ready.')")
        self.page.click('[data-action="start"]')
        self.page.wait_for_function("document.querySelector('#version-status').textContent.includes('On air')")
        self.page.wait_for_function("document.querySelector('#operator-note').textContent.includes('Wait for conductor')")
        self.assertIn("remaining", self.page.locator("#cue-countdown").text_content())
        self.page.click('[data-action="pause"]')
        self.page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused')")
        self.page.click("#blackout")
        self.page.wait_for_function("document.querySelector('#blackout').getAttribute('aria-pressed') === 'true'")
        self.page.click("#mute-all")
        self.page.wait_for_function("document.querySelector('#mute-all').getAttribute('aria-pressed') === 'true'")
        self.page.click('[data-action="return"]')
        self.page.wait_for_function("document.querySelector('#blackout').getAttribute('aria-pressed') === 'false'")
        self.page.select_option('#output-form [name="aspect"]', "4:3")
        self.page.select_option('#output-form [name="pattern"]', "grid")
        self.page.check('#output-form [name="safeArea"]')
        self.page.click("#output-form button")
        self.page.wait_for_function("document.querySelector('.output-overlay').dataset.pattern === 'grid'")
        self.page.click('[data-action="return"]')
        self.page.wait_for_function("document.querySelector('.output-overlay').dataset.pattern === 'off'")
        self.assertEqual(self.page.locator("#blackout").get_attribute("aria-pressed"), "false")
        self.page.locator("#visual-bindings button").first.click()
        self.page.keyboard.press("F8")
        self.page.check("#visual-input-enabled")
        self.page.click("#show-heading")
        self.page.keyboard.press("F8")
        self.page.wait_for_function("document.querySelector('#operator-note').textContent === '' && document.querySelector('#clock').textContent.startsWith('30.')")
        self.page.keyboard.press("F8")
        self.page.click("#enable-visual-midi")
        self.page.wait_for_function("document.querySelector('#visual-input-status').textContent.includes('1 MIDI')")
        self.page.locator("#visual-bindings button").nth(4).click()
        self.page.evaluate("window.testMidi.onmidimessage({data:[144,60,127]})")
        self.page.evaluate("window.testMidi.onmidimessage({data:[128,60,0]})")
        self.page.wait_for_timeout(320)
        self.page.evaluate("window.testMidi.onmidimessage({data:[144,60,127]})")
        self.page.wait_for_function("document.querySelector('#clock').textContent.includes('Paused')")
        self.page.click("#run-preflight")
        self.page.wait_for_function("document.querySelector('#preflight-status').textContent.includes('0 failed, 0 warnings')")
        self.assertEqual(self.errors, [])

    def test_audience_reports_readiness_and_receives_output_configuration(self):
        self.page.goto("https://studio.test" + self.show["controlUrl"])
        self.page.wait_for_function("document.querySelector('#status').textContent.includes('Ready.')")
        self.page.click('[data-action="start"]')
        audience = self.context.new_page()
        audience.goto("https://studio.test" + self.show["url"])
        audience.wait_for_function("document.querySelector('#preview img')?.naturalWidth > 0")
        self.page.wait_for_function("document.querySelector('#screen-reports').textContent.includes('Ready')")
        self.page.select_option('#output-form [name="aspect"]', "4:3")
        self.page.fill('#output-form [name="scale"]', "80")
        self.page.select_option('#output-form [name="pattern"]', "bars")
        self.page.click("#output-form button")
        audience.wait_for_function("document.querySelector('.output-overlay').dataset.pattern === 'bars'")
        self.assertIn("scale(0.8)", audience.locator("#preview").get_attribute("style"))
        audience.wait_for_function("""() => {
          const box = document.querySelector('#preview').getBoundingClientRect();
          return Math.abs(box.width / box.height - 4/3) < 0.001;
        }""")
        self.page.click('[data-action="return"]')
        audience.wait_for_function("document.querySelector('.output-overlay').dataset.pattern === 'off'")
        audience.close()
        self.assertEqual(self.errors, [])

    def test_downloaded_show_plays_embedded_media_with_network_disabled(self):
        self.page.goto(f"https://studio.test/presentations/{self.show['id']}/local/")
        self.page.wait_for_function("document.querySelector('#local-heading').textContent.includes('local playback')")
        self.page.click("#cache-local")
        self.page.wait_for_function("/Uploaded media cached|Using cached|Caching stopped/.test(document.querySelector('#status').textContent)")
        self.assertNotIn("Caching stopped", self.page.locator("#status").text_content())
        self.page.wait_for_function("document.querySelector('#preview img')?.src.startsWith('blob:')")
        with tempfile.TemporaryDirectory(prefix="concert-offline-") as directory:
            destination = Path(directory) / "show.html"
            with self.page.expect_download() as download:
                self.page.click("#download-local")
            download.value.save_as(str(destination))
            offline = self.browser.new_context(offline=True)
            local = offline.new_page()
            errors = []
            local.on("pageerror", lambda error: errors.append(str(error)))
            local.goto(destination.as_uri())
            local.wait_for_function("document.querySelector('#local-heading').textContent.includes('local playback')")
            local.wait_for_function("document.querySelector('#preview img')?.complete && document.querySelector('#preview img').naturalWidth > 0")
            self.assertTrue(local.locator("#preview img").get_attribute("src").startswith("data:image/png"))
            local.click('[data-local="play"]')
            local.wait_for_function("document.querySelector('#clock').textContent.includes('Playing')")
            local.click('[data-local="next"]')
            local.wait_for_function("document.querySelector('#clock').textContent.startsWith('30.')")
            local.click("#fullscreen-local")
            local.wait_for_function("document.fullscreenElement?.id === 'preview'")
            local.keyboard.press("KeyB")
            local.wait_for_function("[...document.querySelectorAll('.media-layer')].every(layer => layer.style.opacity === '0')")
            local.evaluate("document.exitFullscreen()")
            local.wait_for_function("!document.fullscreenElement && document.querySelector('#preview').style.position === ''")
            self.assertEqual(errors, [])
            offline.close()
        self.assertEqual(self.errors, [])

    def test_operator_editor_notes_persist_and_do_not_reach_audience(self):
        self.page.goto("https://studio.test" + self.show["editUrl"])
        self.page.wait_for_function("document.querySelectorAll('.timeline-cue').length === 2")
        self.page.locator(".timeline-cue").first.click()
        self.page.fill('[name="note"]', "Private stage manager cue")
        self.page.uncheck("#cue-marker")
        self.page.click('#cue-form button[type="submit"]')
        self.page.click("#save-show")
        self.page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
        saved = self.fixture.client.get(f"/presentations/api/shows/{self.show['id']}").json
        self.assertEqual(saved["cues"][0]["note"], "Private stage manager cue")
        self.assertFalse(saved["cues"][0]["marker"])
        self.assertEqual(self.errors, [])
