"""Local PowerPoint extraction and bulk-upload browser coverage."""
import base64
import importlib.util
import io
import json
import os
import re
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def powerpoint(members=None, compression=zipfile.ZIP_DEFLATED):
    files = {
        "ppt/presentation.xml": '<p:presentation xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="s2"/><p:sldId r:id="s1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="s1" Target="slides/slide1.xml"/><Relationship Id="s2" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><a:blip r:embed="image"/><a:blip r:embed="shared"/><a:audioFile r:link="sound"/><p:videoFile r:link="online"/></p:sld>',
        "ppt/slides/slide2.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><a:blip r:embed="shared"/></p:sld>',
        "ppt/slides/_rels/slide1.xml.rels": '<Relationships><Relationship Id="image" Target="../media/image1.png"/><Relationship Id="shared" Target="../media/image2.png"/><Relationship Id="sound" Target="../media/audio1.mp3"/><Relationship Id="online" Target="https://example.invalid/video" TargetMode="External"/></Relationships>',
        "ppt/slides/_rels/slide2.xml.rels": '<Relationships><Relationship Id="shared" Target="../media/image2.png"/></Relationships>',
        "ppt/media/image1.png": PNG,
        "ppt/media/image2.png": PNG,
        "ppt/media/audio1.mp3": b"test audio",
        "ppt/media/image3.emf": b"unsupported media",
    }
    files.update(members or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class PresentationImportBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import Error, sync_playwright
        from test_presentations import PresentationTests

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
            self.skipTest(f"Chrome is unavailable: {error}")
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1100})
        self.context.route("http://studio.test/**", self.serve)
        self.context.route("https://media.test/**", lambda route: route.fulfill(content_type="image/png", body=PNG))
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.show = self.fixture.create()
        self.page.goto("http://studio.test" + self.show["editUrl"])
        self.page.wait_for_function("document.querySelector('#status').textContent.includes('Drag media')")

    def serve(self, route):
        request = route.request
        response = self.fixture.client.open(urlsplit(request.url).path, method=request.method,
            data=request.post_data_buffer, content_type=request.headers.get("content-type"))
        route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
        response.close()

    def extract(self, data, name="concert.pptx"):
        return self.page.evaluate("""async ({data, name}) => {
            const {powerpointFiles} = await import('/assets/js/presentations/import.js');
            try {
                const bytes = Uint8Array.from(atob(data), value => value.charCodeAt(0));
                const result = await powerpointFiles(new File([bytes], name));
                return {files: await Promise.all(result.files.map(async item => ({
                    name: item.file.name, slides: item.slides, size: item.file.size,
                    content: btoa(String.fromCharCode(...new Uint8Array(await item.file.arrayBuffer())))
                }))), skipped: result.skipped, warnings: result.warnings};
            } catch (error) { return {error: error.message}; }
        }""", {"data": base64.b64encode(data).decode(), "name": name})

    def test_powerpoint_order_deduplication_and_archive_validation(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            extracted = self.extract(powerpoint(compression=compression))
            self.assertNotIn("error", extracted, extracted)
            self.assertEqual([item["name"] for item in extracted["files"]], ["image2.png", "image1.png", "audio1.mp3"])
            self.assertEqual(extracted["files"][0]["slides"], [1, 2])
            self.assertEqual(extracted["files"][1]["slides"], [2])
            self.assertEqual(base64.b64decode(extracted["files"][0]["content"]), PNG)
            self.assertEqual(extracted["skipped"], ["ppt/media/image3.emf"])
            self.assertIn("external links", extracted["warnings"][0])
        bad = [
            (b"not a ZIP", "concert.pptx"),
            (powerpoint(), "concert.ppt"),
            (powerpoint({"../escape.png": PNG}), "concert.pptx"),
            (powerpoint({"ppt/presentation.xml": "<broken>"}), "concert.pptx"),
            (powerpoint({"ppt/slides/_rels/slide2.xml.rels": "<Relationships/>"}), "concert.pptx"),
            (powerpoint({"ppt/presentation.xml": '<!DOCTYPE x [<!ENTITY xx "foo">]><x/>'}), "concert.pptx"),
            (powerpoint({"ppt/slides/_rels/slide1.xml.rels": '<Relationships><Relationship Id="image" Target="../../../../escape"/></Relationships>'}), "concert.pptx"),
        ]
        archive = bytearray(powerpoint())
        central = archive.find(b"PK\x01\x02")
        encrypted = bytearray(archive)
        struct.pack_into("<H", encrypted, central + 8, 1)
        bad.append((bytes(encrypted), "concert.pptx"))
        oversized = bytearray(archive)
        struct.pack_into("<I", oversized, central + 24, 100 * 1024 * 1024 + 1)
        bad.append((bytes(oversized), "concert.pptx"))
        mismatch = bytearray(archive)
        struct.pack_into("<I", mismatch, central + 16, 0)
        bad.append((bytes(mismatch), "concert.pptx"))
        for data, name in bad:
            with self.subTest(name=name, length=len(data)):
                self.assertIn("error", self.extract(data, name))
        result = self.page.evaluate("""async () => {
            const {folderFiles} = await import('/assets/js/presentations/import.js');
            const files = ['Slide10.png','notes.txt','Slide2.png','track.mp3'].map(name => new File(['asset'], name));
            const result = folderFiles(files);
            let limit;
            try {folderFiles(Array.from({length:501}, (_, i) => new File(['x'], i + '.png')));}
            catch (error) {limit = error.message;}
            return {names:result.files.map(item=>item.file.name), skipped:result.skipped, limit};
        }""")
        self.assertEqual(result["names"], ["Slide2.png", "Slide10.png", "track.mp3"])
        self.assertEqual(result["skipped"], ["notes.txt"])
        self.assertIn("500", result["limit"])
        self.assertEqual(self.errors, [])

    def test_review_import_retry_and_optional_timeline_append(self):
        self.page.locator("#bulk-import summary").first.click()
        self.page.locator("#powerpoint-file").set_input_files({
            "name": "concert.pptx", "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": powerpoint(),
        })
        self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('3 assets ready')")
        self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 0)
        self.assertIn("slides 1, 2", self.page.locator("#import-files label").first.text_content())
        self.assertFalse(self.page.locator("#import-to-timeline").is_checked())
        self.page.click("#import-select-none")
        self.assertTrue(self.page.locator("#run-import").is_disabled())
        self.page.click("#import-select-all")
        self.page.check("#import-to-timeline")
        self.page.fill("#import-image-duration", "2")
        uploads = []
        failed = {"once": True}
        def cloud_upload(route):
            public_id = re.search(rb'jackcapstaff/presentations/\d+/[a-f0-9]{32}', route.request.post_data_buffer).group().decode()
            uploads.append(public_id)
            if failed["once"] and len(uploads) == 2:
                failed["once"] = False
                route.fulfill(status=503, content_type="text/plain", body="Test outage")
                return
            route.fulfill(content_type="application/json", body=json.dumps({"public_id": public_id}))
        self.context.route("https://api.cloudinary.com/**", cloud_upload)
        def resource(public_id, **kwargs):
            return {"resource_type": kwargs["resource_type"], "bytes": len(PNG),
                "format": "png" if kwargs["resource_type"] == "image" else "mp3",
                "duration": 8, "secure_url": "https://media.test/" + public_id.rsplit("/", 1)[-1] + ".png"}
        with patch.dict(os.environ, {"CLOUDINARY_URL": "test"}), \
             patch("presentationstudio.cloudinary.config", return_value=SimpleNamespace(
                 cloud_name="test", api_key="test-key", api_secret="test-secret")), \
             patch("presentationstudio.cloudinary.api.resource", side_effect=resource):
            self.page.click("#run-import")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('Import stopped')")
            self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 1)
            self.assertEqual(self.page.locator(".timeline-cue").count(), 1)
            self.assertTrue(self.page.locator("#import-files input").first.is_disabled())
            self.assertIn("HTTP 503", self.page.locator("#import-status").text_content())
            self.page.click("#run-import")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('3 total')")
            self.page.wait_for_function("document.querySelector('#run-import').disabled")
        self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 3)
        self.assertEqual(self.page.locator(".timeline-cue").count(), 2)
        self.assertEqual(len(uploads), 4)
        self.page.click("#save-show")
        self.page.wait_for_function("document.querySelector('#status').textContent.includes('Presentation saved')")
        stored = self.fixture.client.get(f"/presentations/api/shows/{self.show['id']}").json
        self.assertEqual([cue["name"] for cue in stored["cues"]], ["image2.png", "image1.png"])
        self.assertEqual([cue["start"] for cue in stored["cues"]], [0, 2])
        self.assertEqual([cue["duration"] for cue in stored["cues"]], [2, 2])
        self.assertTrue(all(cue["muted"] for cue in stored["cues"]))
        self.assertEqual(self.errors, [])

    def test_folder_picker_subfolders_skips_and_stoppable_library_only_import(self):
        uploads = []
        def cloud_upload(route):
            public_id = re.search(rb'jackcapstaff/presentations/\d+/[a-f0-9]{32}', route.request.post_data_buffer).group().decode()
            uploads.append(public_id)
            route.fulfill(content_type="application/json", body=json.dumps({"public_id": public_id}))
        self.context.route("https://api.cloudinary.com/**", cloud_upload)
        self.page.locator("#bulk-import summary").first.click()
        temporary = tempfile.TemporaryDirectory(prefix="presentation-folder-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "Slide10.png").write_bytes(PNG)
        (root / "Slide2.png").write_bytes(PNG)
        (root / "notes.txt").write_text("Unsupported file")
        (root / "nested").mkdir()
        (root / "nested" / "Slide3.png").write_bytes(PNG)
        self.page.locator("#media-folder").set_input_files(str(root))
        self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('3 assets ready')")
        labels = self.page.locator("#import-files label").all_text_contents()
        self.assertIn("nested/Slide3.png", labels[0])
        self.assertIn("Slide2.png", labels[1])
        self.assertIn("Slide10.png", labels[2])
        self.assertIn("1 unsupported", self.page.locator("#import-status").text_content())
        self.page.locator("#import-notices summary").click()
        self.assertIn("notes.txt", self.page.locator("#import-notes").text_content())
        self.page.evaluate("""() => {
            const original = window.fetch;
            window.fetch = async (...args) => {
                const response = await original(...args);
                if (args[0] === '/presentations/api/assets' && args[1]?.method === 'POST' && !window.importHeld) {
                    window.importHeld = true;
                    await new Promise(resolve => window.finishImportFile = resolve);
                }
                return response;
            };
        }""")
        with patch.dict(os.environ, {"CLOUDINARY_URL": "test"}), \
             patch("presentationstudio.cloudinary.config", return_value=SimpleNamespace(
                 cloud_name="test", api_key="test-key", api_secret="test-secret")), \
             patch("presentationstudio.cloudinary.api.resource", return_value={
                 "resource_type": "image", "bytes": len(PNG), "format": "png", "secure_url": "https://media.test/folder.png"}):
            self.page.click("#run-import")
            self.page.wait_for_function("!!window.finishImportFile || document.querySelector('#import-status').textContent.includes('Import stopped')")
            self.assertTrue(self.page.evaluate("!!window.finishImportFile"), self.page.locator("#import-status").text_content())
            self.assertTrue(self.page.locator("#media-folder").is_disabled())
            self.page.click("#stop-import")
            self.page.evaluate("window.finishImportFile()")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('Stopped.')")
            self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 1)
            self.assertEqual(len(uploads), 1)
            self.page.click("#run-import")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('3 total')")
        self.assertEqual(len(uploads), 3)
        self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 3)
        self.assertEqual(self.page.locator(".timeline-cue").count(), 0)
        self.assertEqual(self.errors, [])

    def test_retry_pending_verification_reuses_cloudinary_upload(self):
        self.verify_retry(503, 1)

    def test_rejected_asset_retry_uploads_again_after_resource_deletion(self):
        self.verify_retry(400, 2)

    def verify_retry(self, status, expected_uploads):
        self.page.locator("#bulk-import summary").first.click()
        self.page.locator("#powerpoint-file").set_input_files({
            "name": "concert.pptx", "mimeType": "application/octet-stream", "buffer": powerpoint()})
        self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('3 assets ready')")
        self.page.click("#import-select-none")
        self.page.locator("#import-files input").first.check()
        uploads = []
        def cloud_upload(route):
            public_id = re.search(rb'jackcapstaff/presentations/\d+/[a-f0-9]{32}', route.request.post_data_buffer).group().decode()
            uploads.append(public_id)
            route.fulfill(content_type="application/json", body=json.dumps({"public_id": public_id}))
        self.context.route("https://api.cloudinary.com/**", cloud_upload)
        failed = {"once": True}
        def verify(route):
            if route.request.method == "POST" and failed["once"]:
                failed["once"] = False
                route.fulfill(status=status, content_type="application/json", body='{"error":"Verification unavailable"}')
            else:
                self.serve(route)
        self.context.route("http://studio.test/presentations/api/assets", verify)
        with patch.dict(os.environ, {"CLOUDINARY_URL": "test"}), \
             patch("presentationstudio.cloudinary.config", return_value=SimpleNamespace(
                 cloud_name="test", api_key="test-key", api_secret="test-secret")), \
             patch("presentationstudio.cloudinary.api.resource", return_value={
                 "resource_type": "image", "bytes": len(PNG), "format": "png", "secure_url": "https://media.test/retry.png"}):
            self.page.click("#run-import")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('Verification unavailable')")
            self.assertEqual(len(uploads), 1)
            self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 0)
            self.page.click("#run-import")
            self.page.wait_for_function("document.querySelector('#import-status').textContent.includes('1 total')")
        self.assertEqual(len(uploads), expected_uploads)
        self.assertEqual(self.page.locator("#asset-list .asset-card").count(), 1)
        self.assertEqual(self.errors, [])
