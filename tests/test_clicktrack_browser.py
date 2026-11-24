"""Scheduler checks using Chrome when the optional Playwright package is available."""
import importlib.util
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright is not installed")
class ClickTrackBrowserTests(unittest.TestCase):
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
                page.check("#builder-click-preview")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('ready')")
                page.click("#builder-play")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('preview on')")
                page.select_option("#builder-shape", "timedPause")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('unavailable')")
                page.select_option("#builder-shape", "steady")
                page.wait_for_function("document.querySelector('#builder-click-status').textContent.includes('preview on')")
                page.click("button[data-view=edit]")
                self.assertFalse(page.locator("#builder-click-preview").is_checked())
                self.assertEqual(errors, [])
        finally:
            fixture.tearDown()
            fixture.tearDownClass()

    def test_video_editor_click_preview(self):
        from playwright.sync_api import Error, sync_playwright

        source = (Path(__file__).resolve().parents[1] / "assets" / "js" / "clicktrack.js").read_text(encoding="utf-8")
        original = "  init();\n})();"
        self.assertIn(original, source)
        source = source.replace(original, """
          window.previewHooks = {
            normal: normSection, events: builderClickEvents,
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
                body='<input id="builder-click-preview" type="checkbox"><div id="view-video"></div><div id="builder-click-status"></div>'))
            page.goto("http://clicktrack.test/")
            page.add_script_tag(content=source)
            page.evaluate("""() => {
              const t = window.previewHooks, near = (a,b) => Math.abs(a-b) < 1e-8;
              const assert = (ok, message) => { if (!ok) throw Error(message); };
              const first = t.normal({bpm:120, bars:1, num:4, den:4, calcStart:'0:00.000', calcEnd:'0:02.000'});
              const pause = t.normal({type:'timedPause', pauseDuration:3.001, bars:1, calcStart:'0:02.000', calcEnd:'0:05.001'});
              const next = t.normal({bpm:94, bars:1, num:3, den:4, calcStart:'0:05.001', calcEnd:'0:06.916'});
              const secs = [first, pause, next];
              const events = Array.from(t.events(secs, 0));
              assert(events.length === 10, 'four regular, three count-in and three next beats');
              assert(near(events[4].time, 5.001 - 180/94), 'count-in timing');
              assert(near(events[7].time, 5.001), 'hit-point downbeat');
              assert(Array.from(t.events(secs, 5.001))[0].time === 5.001, 'seek to next section');
              const compound = t.normal({bpm:60, bars:1, num:6, den:8, calcStart:'0:00.000', calcEnd:'0:02.000'});
              assert(Array.from(t.events([compound], 0)).length === 6, 'compound subdivisions');
              const ramp = t.normal({bpm:120,bpmEnd:60,rampMode:'linearTime',bars:1,num:4,den:4,
                calcStart:'0:00.000',calcEnd:'0:03.000'});
              const rampEvents = Array.from(t.events([ramp], 0));
              assert(near(rampEvents[1].time, 2 / (120 + Math.sqrt(120**2 + (60**2-120**2)/4)) * 60), 'ramp timing');
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
            videoPlan: planVideoTrack, merge: mergeVideoSections,
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
              t.setMarkers([{...marker,time:0,bar:'175',label:'1',shape:'timedPause'},
                {...marker,time:3.001,bar:'176',label:'2',shape:'rall'},
                {...marker,time:4.916,bar:'177',label:'3',shape:'steady'}]);
              let rampRejected = false;
              try { t.videoPlan(); } catch (error) { rampRejected = error.message.includes('starting BPM'); }
              assert(rampRejected, 'ramp after pause requires explicit start');
            }""")
