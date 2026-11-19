/* Click track / programmable metronome — see clicktrack.py and templates/clicktrack/index.html */
(function () {
  'use strict';

  const CFG = window.CLICKTRACK_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const LS_PREFS = 'clicktrack.prefs';
  const LS_TRACKS = 'clicktrack.tracks.' + (CFG.userKey || 'anon');
  const LS_ANON = 'clicktrack.tracks.anon';
  const LS_LAST = 'clicktrack.last.' + (CFG.userKey || 'anon');

  const LOOKAHEAD = 0.12;       // seconds of audio scheduled ahead
  const TIMER_MS = 25;
  const DEFAULT_KEYS = ['ArrowRight', 'PageDown', 'ArrowDown'];
  const BACK_KEYS = ['ArrowLeft', 'PageUp', 'ArrowUp'];

  const BEAT_UNITS = [
    ['auto', 'Auto'], ['1/2', 'Minim'], ['1/4', 'Crotchet ♩'], ['1/8', 'Quaver ♪'],
    ['1/16', 'Semiquaver'], ['3/4', 'Dotted minim'], ['3/8', 'Dotted crotchet ♩.'], ['3/16', 'Dotted quaver ♪.'],
  ];
  const UNIT_SYMBOL = {
    '1/1': 'semibreve', '1/2': 'minim', '1/4': '♩', '1/8': '♪', '1/16': 'semiquaver', '1/32': 'demisemiquaver',
    '3/2': 'dotted semibreve', '3/4': 'dotted minim', '3/8': '♩.', '3/16': '♪.', '3/32': 'dotted semiquaver',
  };
  const CLICK_MODES = [
    ['beats', 'Beats only'], ['units', 'Every meter unit'], ['sub2', 'Subdivide ×2'],
    ['sub3', 'Subdivide ×3'], ['sub4', 'Subdivide ×4'], ['silent', 'Silent (visual)'],
  ];
  const DENOMS = [1, 2, 4, 8, 16, 32];

  // ------------------------------------------------------------------ utils
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function num(v, def, lo, hi, isInt) {
    let n = parseFloat(v);
    if (!isFinite(n)) return def;
    n = isInt ? Math.trunc(n) : Math.round(n * 100) / 100;
    return clamp(n, lo, hi);
  }
  function loadJSON(key, def) {
    try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? def : v; } catch (e) { return def; }
  }
  function saveJSON(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* storage full / private mode */ }
  }
  function fmtTime(sec) {
    sec = Math.round(sec);
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
  }
  function timecode(seconds) {
    const tenths = Math.round(Math.abs(seconds) * 10);
    const whole = Math.floor(tenths / 10);
    return (seconds < 0 && tenths ? '-' : '') + Math.floor(whole / 60) + ':'
      + String(whole % 60).padStart(2, '0') + '.' + tenths % 10;
  }

  function parseTimecode(value) {
    const text = String(value).trim();
    if (!/^\d+(?::\d{1,2}){0,2}(?:\.\d+)?$/.test(text)) return null;
    const parts = text.split(':').map(Number);
    if (parts.slice(1).some((part) => part >= 60)) return null;
    const seconds = parts.reduce((total, part) => total * 60 + part, 0);
    return Number.isFinite(seconds) ? seconds : null;
  }
  let toastTimer = null;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
  }
  function setStatus(msg) { $('save-status').textContent = msg || ''; }

  // --------------------------------------------------------------- sections
  function defaultSection(over) {
    return Object.assign({
      type: 'click', name: '', notes: '', bpm: 120, bpmEnd: null, beatUnit: 'auto',
      num: 4, den: 4, grouping: '', bars: 8, advance: 'auto', click: 'beats', countIn: 0, startBar: null,
      unitPitch: 0, unitVelocity: 53, subPitch: 0, subVelocity: 32,
      calcStart: '', calcEnd: '',
    }, over || {});
  }

  function normSection(raw) {
    const s = defaultSection();
    raw = raw && typeof raw === 'object' ? raw : {};
    s.type = raw.type === 'pause' ? 'pause' : 'click';
    s.name = String(raw.name || '').slice(0, 120);
    s.notes = String(raw.notes || '').slice(0, 500);
    s.calcStart = String(raw.calcStart || '').slice(0, 32);
    s.calcEnd = String(raw.calcEnd || '').slice(0, 32);
    s.bpm = num(raw.bpm, 120, 10, 400);
    s.bpmEnd = raw.bpmEnd == null || raw.bpmEnd === '' ? null : num(raw.bpmEnd, null, 10, 400);
    s.beatUnit = BEAT_UNITS.some((u) => u[0] === raw.beatUnit) || /^[1-9]\/(1|2|4|8|16|32)$/.test(raw.beatUnit || '') ? raw.beatUnit : 'auto';
    s.num = num(raw.num, 4, 1, 32, true);
    s.den = DENOMS.includes(+raw.den) ? +raw.den : 4;
    s.grouping = String(raw.grouping || '').replace(/\s+/g, '').slice(0, 60);
    s.bars = num(raw.bars, 4, 1, 9999, true);
    s.advance = raw.advance === 'manual' ? 'manual' : 'auto';
    s.click = CLICK_MODES.some((c) => c[0] === raw.click) ? raw.click : 'beats';
    s.unitPitch = num(raw.unitPitch, 0, -24, 24);
    s.unitVelocity = num(raw.unitVelocity, 53, 0, 127, true);
    s.subPitch = num(raw.subPitch, 0, -24, 24);
    s.subVelocity = num(raw.subVelocity, 32, 0, 127, true);
    s.countIn = num(raw.countIn, 0, 0, 8, true);
    s.startBar = raw.startBar == null || raw.startBar === '' ? null : num(raw.startBar, null, 1, 99999, true);
    return s;
  }

  const isCompound = (s) => s.den >= 8 && s.num > 3 && s.num % 3 === 0;

  function parseGrouping(str, total) {
    if (!str) return null;
    const parts = str.split('+').map((x) => parseInt(x, 10));
    if (parts.some((p) => !(p > 0))) return null;
    return parts.reduce((a, b) => a + b, 0) === total ? parts : null;
  }

  function groupsOf(s) {
    const g = parseGrouping(s.grouping, s.num);
    if (g) return g;
    if (isCompound(s)) return Array(s.num / 3).fill(3);
    return Array(s.num).fill(1);
  }

  // Accent level for each bottom-number unit in the bar: 3 = downbeat, 2 = beat, 1 = unit within a beat.
  function unitLevels(s) {
    const lv = [];
    groupsOf(s).forEach((n, gi) => { for (let i = 0; i < n; i++) lv.push(i === 0 ? (gi === 0 ? 3 : 2) : 1); });
    return lv;
  }

  function beatUnitFrac(s) {
    if (s.beatUnit && s.beatUnit !== 'auto') {
      const [n, d] = s.beatUnit.split('/').map(Number);
      return { n, d };
    }
    return isCompound(s) ? { n: 3, d: s.den } : { n: 1, d: s.den };
  }

  function unitLabel(s) {
    const f = beatUnitFrac(s);
    return UNIT_SYMBOL[f.n + '/' + f.d] || (f.n + '/' + f.d);
  }

  // Seconds per bottom-number note at the given tempo (tempo is expressed in the section's beat unit).
  function unitSeconds(s, bpm) {
    const f = beatUnitFrac(s);
    return (60 / bpm) * ((1 / s.den) / (f.n / f.d));
  }

  function bpmAt(s, progress) {
    if (s.bpmEnd == null) return s.bpm;
    return s.bpm + (s.bpmEnd - s.bpm) * clamp(progress, 0, 1);
  }

  const subdivOf = (s) => ({ sub2: 2, sub3: 3, sub4: 4 }[s.click] || 1);

  function audible(s, level, phase) {
    if (phase === 'countin') return level >= 2;
    switch (s.click) {
      case 'silent': return false;
      case 'beats': return level >= 2;
      case 'units': return level >= 1;
      default: return true;
    }
  }

  function startBars(secs) {
    let run = 1;
    return secs.map((s) => {
      if (s.type === 'pause') return null;
      const st = s.startBar || run;
      run = st + s.bars;
      return st;
    });
  }

  const durationCache = new WeakMap();
  function sectionSeconds(s) {
    if (s.type === 'pause') return 0;
    const total = s.bars * s.num;
    if (s.bpmEnd == null) return total * unitSeconds(s, s.bpm);
    if (durationCache.has(s)) return durationCache.get(s);
    const steps = total * subdivOf(s);
    let t = 0;
    for (let i = 0; i < steps; i++) t += unitSeconds(s, bpmAt(s, i / steps)) / subdivOf(s);
    durationCache.set(s, t);
    return t;
  }

  function sectionTimeline() {
    let time = current ? current.videoOffset : 0, estimated = false;
    return sections().map((s) => {
      const start = time, startEstimated = estimated;
      time += sectionSeconds(s);
      if (s.type === 'pause' || s.advance === 'manual') estimated = true;
      return { start, end: time, startEstimated, endEstimated: estimated };
    });
  }

  function sectionTitle(s, i) {
    return s.name || (s.type === 'pause' ? 'Pause' : 'Section ' + (i + 1));
  }

  // ------------------------------------------------------------------ prefs
  const prefs = Object.assign({ volume: 1, sound: 'beep', flash: true, midi: false, keys: DEFAULT_KEYS.slice() }, loadJSON(LS_PREFS, {}));
  if (!Array.isArray(prefs.keys)) prefs.keys = DEFAULT_KEYS.slice();
  const savePrefs = () => saveJSON(LS_PREFS, prefs);

  // ------------------------------------------------------------------ audio
  let ctx = null, master = null, noiseBuf = null;
  const sources = new Set();
  function trackSource(source) {
    sources.add(source);
    source.addEventListener('ended', () => sources.delete(source), { once: true });
  }
  function cancelClicks() {
    sources.forEach((source) => source.stop());
    sources.clear();
  }

  function ensureAudio() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC({ latencyHint: 'interactive' });
      master = ctx.createGain();
      master.connect(ctx.destination);
      noiseBuf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.1), ctx.sampleRate);
      const d = noiseBuf.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      applyVolume();
    }
    if (ctx.state !== 'running' && !E.suspended) ctx.resume().catch((e) => toast('Audio could not resume: ' + e.message));
    return ctx;
  }

  function applyVolume() { if (master) master.gain.value = prefs.volume; }

  function envGain(time, peak, decay) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, time);
    g.gain.exponentialRampToValueAtTime(peak, time + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, time + decay);
    g.connect(master);
    return g;
  }

  function noise(time, dur, filterType, freq, q, out) {
    const src = ctx.createBufferSource();
    trackSource(src);
    src.buffer = noiseBuf;
    const f = ctx.createBiquadFilter();
    f.type = filterType; f.frequency.value = freq; f.Q.value = q;
    src.connect(f); f.connect(out);
    src.start(time); src.stop(time + dur);
  }

  function tone(time, type, freq, dur, out) {
    const o = ctx.createOscillator();
    trackSource(o);
    o.type = type; o.frequency.value = freq;
    o.connect(out);
    o.start(time); o.stop(time + dur);
  }

  // level: 3 downbeat, 2 beat, 1 unit, 0 subdivision
  function playClick(time, level, section) {
    let peak = [0.25, 0.42, 0.7, 1][level], pitch = 1;
    if (section && level < 2) {
      const prefix = level === 1 ? 'unit' : 'sub';
      peak = section[prefix + 'Velocity'] / 127;
      pitch = Math.pow(2, section[prefix + 'Pitch'] / 12);
    }
    if (peak === 0) return;
    if (prefs.sound === 'wood') {
      const g = envGain(time, peak, 0.06);
      tone(time, 'sine', [700, 900, 1100, 1500][level] * pitch, 0.07, g);
      noise(time, 0.03, 'bandpass', [1800, 2200, 2600, 3400][level] * pitch, 6, envGain(time, peak * 0.8, 0.025));
    } else if (prefs.sound === 'hihat') {
      noise(time, 0.12, 'highpass', Math.min(ctx.sampleRate / 2, (level === 3 ? 5000 : 7500) * pitch), 0.7, envGain(time, peak, [0.03, 0.04, 0.05, 0.1][level]));
    } else {
      tone(time, 'square', [660, 880, 1050, 1760][level] * pitch, 0.06, envGain(time, peak * 0.5, 0.045));
    }
  }

  // Worker-driven timer keeps scheduling steady even when the tab is in the background.
  let worker = null, fallbackTimer = null;
  function timerStart() {
    if (!worker && !fallbackTimer) {
      try {
        const src = 'let t=null;onmessage=e=>{clearInterval(t);t=null;if(e.data==="start")t=setInterval(()=>postMessage(0),' + TIMER_MS + ');};';
        worker = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
        worker.onmessage = scheduler;
      } catch (e) { worker = null; }
    }
    if (worker) worker.postMessage('start');
    else if (!fallbackTimer) fallbackTimer = setInterval(scheduler, TIMER_MS);
  }
  function timerStop() {
    if (worker) worker.postMessage('stop');
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
  }

  let wakeLock = null;
  async function requestWake() {
    try { if ('wakeLock' in navigator && !wakeLock) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); } } catch (e) { /* ignore */ }
  }
  function releaseWake() { if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; } }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && E.playing && !E.suspended) requestWake(); });

  // ----------------------------------------------------------------- engine
  // phase: stopped | countin | play | pause | end. bar/tick/sub describe the *next* step to schedule.
  const E = { playing: false, suspended: false, secIdx: 0, phase: 'stopped', bar: 0, tick: 0, sub: 0, nextTime: 0, queued: null };
  const clock = { start: 0, elapsed: 0, videoStart: 0 };
  let transportBusy = false;
  const visQ = [];
  let shown = null;          // snapshot currently displayed
  let selectedIdx = 0;       // where Start begins
  let lastAdvanceAt = 0;

  let tracks = [];
  let current = null;
  const sections = () => (current ? current.sections : []);

  function pushVis(type, extra) {
    visQ.push(Object.assign({
      type, time: E.nextTime, secIdx: E.secIdx, phase: E.phase, bar: E.bar, tick: E.tick, sub: E.sub, queued: E.queued,
    }, extra || {}));
  }

  function enterSection(idx, flowing) {
    const secs = sections();
    E.bar = 0; E.tick = 0; E.sub = 0;
    if (idx < 0 || idx >= secs.length) { E.phase = 'end'; pushVis('end'); return; }
    E.secIdx = idx;
    const s = secs[idx];
    if (s.type === 'pause') { E.phase = 'pause'; pushVis('state'); return; }
    E.phase = !flowing && s.countIn > 0 ? 'countin' : 'play';
  }

  function scheduler() {
    if (tester.running) { scheduleTester(); return; }
    if (!E.playing || E.suspended || !ctx) return;
    const horizon = ctx.currentTime + LOOKAHEAD;
    let guard = 0;
    while ((E.phase === 'play' || E.phase === 'countin') && E.nextTime < horizon && guard++ < 512) step();
  }

  function step() {
    const s = sections()[E.secIdx];
    if (!s) { E.phase = 'end'; pushVis('end'); return; }
    if (E.tick >= s.num) E.tick = 0;   // meter edited while playing
    const levels = unitLevels(s);
    const counting = E.phase === 'countin';
    const subN = counting ? 1 : subdivOf(s);
    const level = E.sub === 0 ? levels[E.tick] : 0;
    const bpm = counting ? s.bpm : bpmAt(s, (E.bar + (E.tick + E.sub / subN) / s.num) / s.bars);
    if (audible(s, level, E.phase)) playClick(E.nextTime, level, s);
    pushVis('tick', { level, bpm });
    E.nextTime += unitSeconds(s, bpm) / subN;

    if (++E.sub < subN) return;
    E.sub = 0;
    if (++E.tick < s.num) return;
    E.tick = 0;
    endOfBar(s);
  }

  function endOfBar(s) {
    E.bar++;
    if (E.phase === 'countin') {
      if (E.bar >= s.countIn) { E.phase = 'play'; E.bar = 0; }
      return;
    }
    if (E.queued != null) {
      const target = E.queued;
      E.queued = null;
      enterSection(target, true);
    } else if (E.bar >= s.bars && s.advance === 'auto') {
      enterSection(E.secIdx + 1, true);
    }
  }

  function start(fromIdx) {
    if (!sections().length) return;
    stopTester();
    ensureAudio();
    visQ.length = 0;
    E.playing = true;
    E.suspended = false;
    E.queued = null;
    E.nextTime = ctx.currentTime + 0.1;
    enterSection(clamp(fromIdx, 0, sections().length - 1), false);
    const s = sections()[E.secIdx];
    clock.start = E.nextTime + (E.phase === 'countin' ? s.countIn * s.num * unitSeconds(s, s.bpm) : 0);
    clock.elapsed = 0;
    clock.videoStart = sectionTimeline()[E.secIdx].start;
    shown = null;
    timerStart();
    requestWake();
    scheduler();
    renderTransport();
    if (E.phase === 'pause') renderStage({ secIdx: E.secIdx, phase: 'pause', bar: 0, tick: 0, sub: 0, queued: null });
  }

  function stop(reachedEnd) {
    if (!E.playing) return;
    clock.elapsed = Math.max(0, ctx.currentTime - clock.start);
    cancelClicks();
    if (reachedEnd) selectedIdx = 0;
    else if (shown) selectedIdx = shown.secIdx;
    E.playing = false;
    E.suspended = false;
    E.phase = 'stopped';
    E.queued = null;
    visQ.length = 0;
    shown = null;
    timerStop();
    releaseWake();
    if (ctx.state === 'suspended') ctx.resume().catch((e) => toast('Audio could not resume: ' + e.message));
    renderTransport();
    renderIdle();
    renderSetlist();
  }

  async function togglePlay() {
    if (transportBusy) return;
    if (!E.playing) { start(selectedIdx); return; }
    transportBusy = true;
    try {
      if (E.suspended) {
        await ctx.resume();
        if (!E.playing) return;
        E.suspended = false;
        timerStart();
        requestWake();
        scheduler();
      } else {
        await ctx.suspend();
        if (!E.playing) {
          await ctx.resume();
          return;
        }
        E.suspended = true;
        timerStop();
        releaseWake();
      }
      renderTransport();
      renderClock();
    } catch (e) {
      toast('Could not pause/resume audio: ' + e.message);
    } finally {
      transportBusy = false;
    }
  }

  // Pedal / Next: start when stopped; leave a pause immediately; otherwise move on at the next barline.
  function advance() {
    const now = performance.now();
    if (now - lastAdvanceAt < 180) return;   // pedal bounce
    lastAdvanceAt = now;
    if (!current) return;
    if (!E.playing) { start(selectedIdx); return; }
    if (E.suspended) { togglePlay(); return; }
    if (E.phase === 'pause') {
      ensureAudio();
      E.nextTime = Math.max(ctx.currentTime + 0.06, E.nextTime);
      enterSection(E.secIdx + 1, false);
      scheduler();
      return;
    }
    if (E.phase === 'end') return;
    if (E.queued == null) E.queued = E.secIdx + 1;
    renderNext();
    renderSetlist();
  }

  function back() {
    if (E.playing) {
      if (E.queued != null) { E.queued = null; renderNext(); renderSetlist(); toast('Jump cancelled'); }
      return;
    }
    selectIdx(selectedIdx - 1);
  }

  function jumpTo(idx) {
    if (E.suspended) stop(false);
    if (!E.playing) { selectIdx(idx); return; }
    if (E.phase === 'pause') {
      E.nextTime = Math.max(ctx.currentTime + 0.06, E.nextTime);
      enterSection(idx, false);
      scheduler();
      return;
    }
    E.queued = idx;
    renderNext();
    renderSetlist();
  }

  function selectIdx(idx) {
    const n = sections().length;
    if (!n) return;
    selectedIdx = clamp(idx, 0, n - 1);
    renderIdle();
    renderSetlist();
  }

  // -------------------------------------------------------------- tap tempo
  // Averages the last few taps; a gap over 2s starts a fresh measurement.
  const tap = { times: [], idx: -1 };
  function tapTempo(idx) {
    if (idx == null) idx = document.body.classList.contains('editing') ? tester.idx : E.playing ? E.secIdx : selectedIdx;
    const s = sections()[idx];
    if (!s || s.type === 'pause') { toast('Pick a click section to tap a tempo into'); return; }
    const now = performance.now();
    const last = tap.times[tap.times.length - 1];
    if (tap.idx !== idx || (last && now - last > 2000)) tap.times = [];
    tap.idx = idx;
    tap.times.push(now);
    if (tap.times.length > 8) tap.times.shift();
    const n = tap.times.length;
    if (n < 2) { toast('Tap tempo: keep tapping…'); return; }
    const bpm = Math.round(60000 * (n - 1) / (tap.times[n - 1] - tap.times[0]));
    const ns = normSection(Object.assign({}, s, { bpm }));
    current.sections[idx] = ns;
    markDirty();
    updateInfos();
    renderSetlist();
    if (!E.playing) renderIdle();
    const field = document.querySelector('[data-f="bpm"][data-i="' + idx + '"]');
    if (field) field.value = ns.bpm;
    renderTester();
    toast('Tempo ' + unitLabel(ns) + '=' + ns.bpm + (n < 4 ? ' (keep tapping to refine)' : ''));
  }

  // ----------------------------------------------------------- perform view
  let beatsKey = '';
  let litBeat = null;

  function groupIndexOf(s, tick) {
    let acc = 0;
    const g = groupsOf(s);
    for (let i = 0; i < g.length; i++) { acc += g[i]; if (tick < acc) return i; }
    return 0;
  }

  function buildBeats(s, idx) {
    const key = s ? [idx, s.type, s.num, s.den, s.grouping].join('|') : '';
    if (key === beatsKey) return;
    beatsKey = key;
    litBeat = null;
    const box = $('d-beats');
    if (!s || s.type === 'pause') { box.innerHTML = ''; return; }
    let u = 0, html = '';
    groupsOf(s).forEach((n, gi) => {
      html += '<div class="beat-group">';
      for (let i = 0; i < n; i++, u++) {
        const cls = 'beat' + (i === 0 ? '' : ' unit') + (gi === 0 && i === 0 ? ' down' : '');
        html += '<div class="' + cls + '" data-u="' + u + '"></div>';
      }
      html += '</div>';
    });
    box.innerHTML = html;
  }

  function lightBeat(tick) {
    if (litBeat) litBeat.classList.remove('on');
    litBeat = tick == null || tick < 0 ? null : $('d-beats').querySelector('[data-u="' + tick + '"]');
    if (litBeat) litBeat.classList.add('on');
  }

  function renderStage(st) {
    const secs = sections();
    const s = secs[st.secIdx];
    const stage = $('stage');
    stage.classList.toggle('paused', st.phase === 'pause');
    stage.classList.toggle('countin', st.phase === 'countin');
    if (!s) {
      $('d-secnum').textContent = '';
      $('d-name').textContent = current ? 'No sections yet — add some in Edit' : 'Loading…';
      ['d-notes', 'd-barof', 'd-grouping', 'd-tempo-sub'].forEach((id) => { $(id).textContent = ''; });
      ['d-bar', 'd-meter', 'd-tempo'].forEach((id) => { $(id).textContent = '–'; });
      $('d-countin').hidden = true;
      buildBeats(null);
      renderNext();
      return;
    }
    const sb = startBars(secs);
    $('d-secnum').textContent = 'Section ' + (st.secIdx + 1) + ' of ' + secs.length;
    $('d-name').textContent = sectionTitle(s, st.secIdx);
    $('d-notes').textContent = s.notes || '';

    // In a pause, preview the next clicked section so you're ready for it.
    let info = s;
    if (s.type === 'pause') info = secs.slice(st.secIdx + 1).find((x) => x.type !== 'pause') || null;

    if (info) {
      const g = groupsOf(info);
      $('d-meter').textContent = info.num + '/' + info.den;
      $('d-grouping').textContent = (s.type === 'pause' ? 'next · ' : '') + (g.length !== info.num ? g.join('+') : '');
      const bpm = st.bpm != null && s.type !== 'pause' ? st.bpm : info.bpm;
      $('d-tempo').textContent = unitLabel(info) + '=' + Math.round(bpm);
      $('d-tempo-sub').textContent = info.bpmEnd != null
        ? info.bpm + ' → ' + info.bpmEnd + (info.bpmEnd > info.bpm ? ' accel.' : ' rit.')
        : (s.type === 'pause' ? 'next section' : '');
    } else {
      $('d-meter').textContent = '–'; $('d-grouping').textContent = '';
      $('d-tempo').textContent = '–'; $('d-tempo-sub').textContent = '';
    }

    if (s.type === 'pause') {
      $('d-bar').textContent = '–';
      $('d-barof').textContent = st.phase === 'pause' ? 'waiting for cue' : 'pause';
    } else if (st.phase === 'play') {
      const inBar = st.bar % s.bars;
      const rep = Math.floor(st.bar / s.bars);
      $('d-bar').textContent = sb[st.secIdx] + inBar;
      $('d-barof').textContent = (inBar + 1) + ' of ' + s.bars + (rep > 0 ? ' · repeat ' + (rep + 1) : '');
    } else {
      $('d-bar').textContent = sb[st.secIdx];
      $('d-barof').textContent = st.phase === 'countin' ? 'count-in' : s.bars + ' bar' + (s.bars === 1 ? '' : 's');
    }

    buildBeats(s, st.secIdx);
    if (st.phase === 'play' || st.phase === 'countin') {
      if (st.sub === 0) lightBeat(st.tick);
    } else {
      lightBeat(null);
    }

    const ci = $('d-countin');
    if (st.phase === 'countin') {
      ci.hidden = false;
      ci.innerHTML = '<div class="n">' + (groupIndexOf(s, st.tick) + 1) + '</div><div class="t">Count-in'
        + (s.countIn > 1 ? ' · bar ' + (st.bar + 1) + ' of ' + s.countIn : '') + '</div>';
    } else {
      ci.hidden = true;
    }
    renderNext(st);
  }

  function renderNext(st) {
    st = st || shown || { secIdx: selectedIdx, phase: 'stopped', bar: 0, tick: 0 };
    const el = $('d-next');
    const secs = sections();
    const s = secs[st.secIdx];
    const queued = E.playing ? (E.queued != null ? E.queued : st.queued) : null;
    const target = queued != null ? queued : st.secIdx + 1;
    const next = secs[target];
    const sb = startBars(secs);
    const title = next ? sectionTitle(next, target) : 'End of track';
    const preview = $('next-preview');
    preview.hidden = !s;
    $('d-next-name').textContent = 'Up next: ' + title;
    $('d-next-details').textContent = !next ? '' : next.type === 'pause' ? 'Pause / cue — waits for pedal'
      : 'Score bar ' + sb[target] + ' · ' + next.num + '/' + next.den + ' · ' + unitLabel(next) + '=' + next.bpm
        + (next.bpmEnd != null ? ' → ' + next.bpmEnd : '');
    $('d-next-notes').textContent = next ? next.notes : '';
    let change = '';
    if (s && s.type !== 'pause' && next && next.type !== 'pause') {
      // Compare physical note durations, not BPM numbers with potentially different beat units.
      const currentBpm = E.playing && st.phase === 'play' && st.bpm != null ? st.bpm : s.bpm;
      const quarterSeconds = (section, bpm) => unitSeconds(section, bpm) * section.den / 4;
      const ratio = quarterSeconds(s, currentBpm) / quarterSeconds(next, next.bpm);
      change = ratio > 1.01 ? 'Faster' : ratio < 0.99 ? 'Slower' : 'Same pace';
      if (s.num !== next.num || s.den !== next.den) change += ' · meter change';
      if (unitLabel(s) !== unitLabel(next)) change += ' · beat unit changes';
    }
    $('d-next-change').textContent = change;
    let text = '', cls = '';
    if (!s) text = '';
    else if (!E.playing) text = 'Press Start, Next or the pedal to begin';
    else if (st.phase === 'pause') { text = 'Waiting — press Next / pedal for ' + title; cls = 'waiting'; }
    else if (st.phase === 'countin') {
      const left = s.countIn - st.bar;
      text = 'Count-in: ' + left + ' bar' + (left === 1 ? '' : 's') + ' left before ' + sectionTitle(s, st.secIdx)
        + ' · score bar ' + sb[st.secIdx] + (queued != null ? ' · next jump queued' : '');
    }
    else if (s.advance === 'manual' && queued == null) { text = 'Repeating — press Next / pedal for ' + title; cls = 'waiting'; }
    else {
      const left = queued != null ? 1 : Math.max(1, s.bars - st.bar);
      const beatsLeft = groupsOf(s).length - groupIndexOf(s, Math.max(0, st.tick));
      text = left > 1 ? left + ' bars left (including this bar)'
        : 'After this bar · ' + beatsLeft + ' beat' + (beatsLeft === 1 ? '' : 's') + ' left';
      text += ' → ' + title;
      if (queued != null) cls = 'queued';
    }
    el.textContent = text;
    el.className = 'stage-next ' + cls;
  }

  function renderIdle() {
    if (E.playing) return;
    renderStage({ secIdx: selectedIdx, phase: 'stopped', bar: 0, tick: -1, sub: 0 });
  }

  function renderSetlist() {
    const secs = sections();
    const sb = startBars(secs);
    const cur = E.playing && shown ? shown.secIdx : -1;
    const queued = E.playing ? E.queued : null;
    $('setlist').innerHTML = secs.map((s, i) => {
      const cls = [
        s.type === 'pause' ? 'pause' : '',
        i === cur ? 'current' : '',
        i === queued ? 'queued' : '',
        !E.playing && i === selectedIdx ? 'selected' : '',
      ].join(' ');
      const meta = s.type === 'pause'
        ? 'pause · waits for cue'
        : s.num + '/' + s.den + ' · ' + unitLabel(s) + '=' + s.bpm + (s.bpmEnd != null ? '→' + s.bpmEnd : '')
          + ' · ' + s.bars + ' bar' + (s.bars === 1 ? '' : 's') + (s.advance === 'manual' ? ' · wait' : '') + ' · b.' + sb[i];
      return '<li class="' + cls + '" data-i="' + i + '"><span class="idx">' + (i + 1) + '</span><span class="nm">'
        + esc(sectionTitle(s, i)) + '</span><span class="meta">' + esc(meta) + '</span></li>';
    }).join('');
  }

  function renderTransport() {
    $('btn-play').textContent = !E.playing ? '▶ Start' : E.suspended ? '▶ Resume' : 'Pause';
    $('btn-play').classList.toggle('primary', !E.playing);
    $('btn-stop').disabled = !E.playing;
    $('stage').classList.toggle('transport-paused', E.suspended);
  }

  function renderClock() {
    const elapsed = E.playing ? Math.max(0, ctx.currentTime - clock.start) : 0;
    const planned = !E.playing ? sectionTimeline()[selectedIdx] : null;
    const videoTime = E.playing ? clock.videoStart + elapsed : planned ? planned.start : (current ? current.videoOffset : 0);
    $('d-elapsed').textContent = timecode(elapsed);
    $('d-video-time').textContent = timecode(videoTime);
    $('d-clock-status').textContent = E.suspended ? 'Paused — clock frozen'
      : E.playing ? (ctx.currentTime < clock.start ? 'Count-in — clock starts with music' : 'Running (cue waits included)')
        : 'Starts from selected section; video time is not linked to a video player';
  }

  let flashTimer = null;
  function doFlash() {
    const f = $('flash-layer');
    f.classList.add('on');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => f.classList.remove('on'), 70);
  }

  function frame() {
    if (E.playing && !E.suspended && ctx && visQ.length) {
      const now = ctx.currentTime;
      let latest = null, flash = false, ended = false;
      while (visQ.length && visQ[0].time <= now) {
        const ev = visQ.shift();
        if (ev.type === 'end') { ended = true; break; }
        latest = ev;
        if (ev.type === 'tick' && ev.level === 3) flash = true;
      }
      if (ended) {
        stop(true);
        toast('End of track');
      } else if (latest) {
        const changed = !shown || shown.secIdx !== latest.secIdx || shown.phase !== latest.phase;
        shown = latest;
        renderStage(latest);
        if (changed) renderSetlist();
        if (flash && prefs.flash) doFlash();
      }
    }
    renderClock();
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------- storage
  let localSeq = 0;
  const newLocalId = () => 'local-' + Date.now().toString(36) + '-' + (localSeq++);
  const isServerId = (id) => typeof id === 'number';

  function makeTrack(title, secs, notes) {
    return { id: newLocalId(), title: title || 'Untitled', notes: notes || '', videoOffset: 0, sections: (secs || []).map(normSection), _dirty: true, _ver: 1 };
  }

  function normTrack(t) {
    return {
      id: t.id != null ? t.id : newLocalId(),
      title: String(t.title || 'Untitled').slice(0, 255),
      notes: String(t.notes || '').slice(0, 2000),
      videoOffset: num(t.videoOffset, 0, 0, 86400),
      sections: Array.isArray(t.sections) ? t.sections.slice(0, 500).map(normSection) : [],
      _dirty: !!t._dirty, _auto: !!t._auto, _ver: 1,
    };
  }

  const serialise = (t) => ({ id: t.id, title: t.title, notes: t.notes, videoOffset: t.videoOffset, sections: t.sections, _dirty: t._dirty, _auto: t._auto });
  const cacheLocal = () => saveJSON(LS_TRACKS, tracks.map(serialise));

  async function api(method, path, body) {
    const res = await fetch(CFG.apiBase + (path || ''), {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
    return data;
  }

  async function persist(t) {
    if (!CFG.authenticated) { t._dirty = false; cacheLocal(); return; }
    const ver = t._ver;
    const payload = { title: t.title, notes: t.notes, videoOffset: t.videoOffset, sections: t.sections };
    if (isServerId(t.id)) {
      await api('PUT', '/' + t.id, payload);
    } else {
      const created = await api('POST', '', payload);
      t.id = created.id;
      if (current === t) saveJSON(LS_LAST, t.id);
      refreshTrackSelect();
    }
    if (t._ver === ver) t._dirty = false;
    cacheLocal();
  }

  let saveTimer = null, saving = false, saveAgain = false;
  function markDirty(t) {
    t = t || current;
    if (!t) return;
    t._dirty = true;
    t._auto = false;
    t._ver = (t._ver || 0) + 1;
    cacheLocal();
    setStatus(CFG.authenticated ? 'Saving…' : 'Saved in this browser');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDirty, 700);
  }

  async function saveDirty() {
    if (saving) { saveAgain = true; return; }
    saving = true;
    let failed = null;
    for (const t of tracks.slice()) {
      if (!t._dirty) continue;
      try { await persist(t); } catch (e) { failed = e; }
    }
    saving = false;
    if (failed) setStatus('⚠ Not saved to account (' + failed.message + ') — kept in this browser');
    else setStatus(CFG.authenticated ? 'Saved ✓' : 'Saved in this browser');
    if (saveAgain) { saveAgain = false; saveDirty(); }
  }
  window.addEventListener('online', () => { if (tracks.some((t) => t._dirty)) saveDirty(); });
  window.addEventListener('beforeunload', (e) => {
    if (CFG.authenticated && tracks.some((t) => t._dirty)) { e.preventDefault(); e.returnValue = ''; }
  });

  async function loadTracks() {
    const cached = (loadJSON(LS_TRACKS, []) || []).map(normTrack);
    if (CFG.authenticated) {
      try {
        const data = await api('GET');
        tracks = (data.tracks || []).map(normTrack);
        // Re-apply edits that never reached the server (e.g. made offline).
        cached.filter((c) => c._dirty).forEach((c) => {
          const i = tracks.findIndex((t) => t.id === c.id);
          if (i >= 0) tracks[i] = c; else if (!isServerId(c.id)) tracks.push(c);
        });
        await offerAnonImport();
      } catch (e) {
        tracks = cached;
        setStatus('⚠ Offline — using copy saved in this browser');
      }
    } else {
      tracks = cached;
    }
    if (!tracks.length) {
      const t = makeTrack('Metronome', [{ name: 'Metronome', bars: 4, advance: 'manual', countIn: 0 }]);
      t._auto = true;
      tracks.push(t);
    }
    cacheLocal();
    if (tracks.some((t) => t._dirty)) saveDirty();
  }

  async function offerAnonImport() {
    const anon = (loadJSON(LS_ANON, []) || []).map(normTrack).filter((t) => !t._auto);
    if (!anon.length) return;
    if (window.confirm('Copy ' + anon.length + ' click track' + (anon.length === 1 ? '' : 's') + ' saved in this browser to your account?')) {
      anon.forEach((t) => { t.id = newLocalId(); t._dirty = true; tracks.push(t); });
    }
    try { localStorage.removeItem(LS_ANON); } catch (e) { /* ignore */ }
  }

  // ----------------------------------------------------------- track mgmt
  function refreshTrackSelect() {
    const sel = $('track-select');
    const sorted = tracks.slice().sort((a, b) => a.title.localeCompare(b.title));
    sel.innerHTML = sorted.map((t) => '<option value="' + esc(t.id) + '">' + esc(t.title) + '</option>').join('');
    if (current) sel.value = String(current.id);
  }

  function selectTrack(id) {
    if (E.playing) stop(false);
    stopTester();
    current = tracks.find((t) => String(t.id) === String(id)) || tracks[0] || null;
    if (current) saveJSON(LS_LAST, current.id);
    selectedIdx = 0;
    tester.idx = 0;
    tap.times = [];
    beatsKey = '';
    refreshTrackSelect();
    renderAll();
  }

  function addTrack(t) {
    tracks.push(t);
    markDirty(t);
    selectTrack(t.id);
  }

  function slug(s) { return String(s || 'click-track').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'click-track'; }

  function exportTrack() {
    if (!current) return;
    const data = { format: 'jackcapstaff-clicktrack', version: 1, title: current.title, notes: current.notes, videoOffset: current.videoOffset, sections: current.sections };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = slug(current.title) + '.clicktrack.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function importFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        const list = Array.isArray(data) ? data : Array.isArray(data.tracks) ? data.tracks : [data];
        const made = list.filter((x) => x && Array.isArray(x.sections)).map((x) => {
          const t = makeTrack(x.title || file.name.replace(/\.json$/i, ''), x.sections, x.notes);
          t.videoOffset = num(x.videoOffset, 0, 0, 86400);
          return t;
        });
        if (!made.length) throw new Error('no sections found');
        made.forEach((t) => { tracks.push(t); markDirty(t); });
        selectTrack(made[0].id);
        toast('Imported ' + made.length + ' track' + (made.length === 1 ? '' : 's'));
      } catch (e) {
        toast('Could not import: ' + e.message);
      }
    };
    reader.readAsText(file);
  }

  async function deleteTrack() {
    if (!current || !window.confirm('Delete “' + current.title + '”? This cannot be undone.')) return;
    const t = current;
    if (CFG.authenticated && isServerId(t.id)) {
      try { await api('DELETE', '/' + t.id); } catch (e) { toast('Delete failed: ' + e.message); return; }
    }
    tracks = tracks.filter((x) => x !== t);
    if (!tracks.length) tracks.push(makeTrack('Metronome', [{ name: 'Metronome', bars: 4, advance: 'manual' }]));
    cacheLocal();
    if (tracks.some((x) => x._dirty)) saveDirty();
    selectTrack(tracks[0].id);
    toast('Track deleted');
  }

  // ----------------------------------------------------------------- editor
  const opts = (list, val) => list.map(([v, l]) => '<option value="' + esc(v) + '"' + (String(v) === String(val) ? ' selected' : '') + '>' + esc(l) + '</option>').join('');
  const fld = (label, inner, wide) => '<label' + (wide ? ' class="wide"' : '') + '>' + label + inner + '</label>';
  const inp = (i, f, val, attrs) => '<input data-i="' + i + '" data-f="' + f + '" value="' + esc(val == null ? '' : val) + '" ' + (attrs || 'type="text"') + '>';
  const sel = (i, f, list, val) => '<select data-i="' + i + '" data-f="' + f + '">' + opts(list, val) + '</select>';
  const performanceNotes = (i, s) => fld('Performance notes / cue',
    '<textarea data-i="' + i + '" data-f="notes" rows="3" maxlength="500" placeholder="Cues to read while performing">'
    + esc(s.notes) + '</textarea>', true);

  // The tester shares the audio scheduler, but never advances sections or the performance clock.
  const tester = { running: false, idx: 0, tick: 0, sub: 0, nextTime: 0 };
  function renderTester() {
    const choices = sections().map((s, i) => ({ s, i })).filter(({ s }) => s.type === 'click');
    if (!choices.some(({ i }) => i === tester.idx)) tester.idx = choices.length ? choices[0].i : -1;
    $('tester-section').innerHTML = choices.map(({ s, i }) => '<option value="' + i + '">' + esc(sectionTitle(s, i)) + '</option>').join('');
    $('tester-section').value = String(tester.idx);
    const s = sections()[tester.idx];
    $('tester-tempo').textContent = s ? s.num + '/' + s.den + ' · ' + unitLabel(s) + '=' + s.bpm : 'Add a click section to test';
    $('tester-start').disabled = !s;
    $('tester-start').textContent = tester.running ? 'Stop tester' : 'Start tester';
    $('tester-tap').disabled = !s;
  }

  function stopTester() {
    if (!tester.running) return;
    tester.running = false;
    timerStop();
    cancelClicks();
    renderTester();
  }

  function startTester() {
    const s = sections()[tester.idx];
    if (!s || s.type === 'pause') { toast('Choose a click section to test'); return; }
    if (E.playing) stop(false);
    ensureAudio();
    tester.running = true;
    tester.tick = 0; tester.sub = 0; tester.nextTime = ctx.currentTime + 0.06;
    timerStart();
    scheduleTester();
    renderTester();
  }

  function scheduleTester() {
    const s = sections()[tester.idx];
    if (!s || s.type === 'pause') { stopTester(); return; }
    let guard = 0;
    while (tester.nextTime < ctx.currentTime + LOOKAHEAD && guard++ < 512) {
      if (tester.tick >= s.num) tester.tick = 0;
      const subN = subdivOf(s);
      if (tester.sub >= subN) tester.sub = 0;
      const level = tester.sub === 0 ? unitLevels(s)[tester.tick] : 0;
      if (audible(s, level, 'play')) playClick(tester.nextTime, level, s);
      tester.nextTime += unitSeconds(s, s.bpm) / subN;
      if (++tester.sub >= subN) {
        tester.sub = 0;
        tester.tick = (tester.tick + 1) % s.num;
      }
    }
  }

  function calculateTempo(i) {
    const s = sections()[i];
    const startTime = parseTimecode(s.calcStart), endTime = parseTimecode(s.calcEnd);
    const result = $('calc-result-' + i);
    if (startTime == null || endTime == null || endTime <= startTime) {
      result.textContent = 'Enter valid video times with end after start (seconds, m:ss or h:mm:ss).';
      result.classList.add('warn');
      return;
    }
    const bpm = 60 * s.bars * s.num * unitSeconds(s, 60) / (endTime - startTime);
    if (bpm < 10 || bpm > 400) {
      result.textContent = 'Calculated tempo ' + bpm.toFixed(2) + ' is outside 10–400 BPM. Check times, bars and beat unit.';
      result.classList.add('warn');
      return;
    }
    const n = normSection(Object.assign({}, s, { bpm, bpmEnd: null }));
    current.sections[i] = n;
    document.querySelector('[data-f="bpm"][data-i="' + i + '"]').value = n.bpm;
    document.querySelector('[data-f="bpmEnd"][data-i="' + i + '"]').value = '';
    result.textContent = 'Set steady tempo: ' + unitLabel(n) + '=' + n.bpm + ' for ' + timecode(endTime - startTime)
      + '. Times are absolute video positions; offset is not added to this interval.';
    result.classList.remove('warn');
    markDirty(); updateInfos(); renderSetlist(); renderIdle(); renderTester();
  }

  function sectionRow(s, i, n) {
    const btn = (act, label, title, disabled) => '<button class="btn icon' + (act === 'del' ? ' danger' : '') + '" data-act="' + act + '" data-i="' + i + '" title="' + title + '"' + (disabled ? ' disabled' : '') + '>' + label + '</button>';
    const acts = '<div class="acts">' + btn('up', '↑', 'Move up', i === 0) + btn('down', '↓', 'Move down', i === n - 1)
      + btn('dup', '⧉', 'Duplicate') + btn('play', '▶', 'Perform from here') + btn('del', '✕', 'Delete') + '</div>';
    let f = fld('Type', sel(i, 'type', [['click', 'Click'], ['pause', 'Pause / cue']], s.type));
    if (s.type === 'pause') {
      f += fld('Name', inp(i, 'name', s.name, 'type="text" maxlength="120" placeholder="e.g. Narration"'), true)
        + performanceNotes(i, s);
    } else {
      f += fld('Name', inp(i, 'name', s.name, 'type="text" maxlength="120" placeholder="Section ' + (i + 1) + '"'), true)
        + fld('Tempo', '<span class="tapwrap">' + inp(i, 'bpm', s.bpm, 'type="number" min="10" max="400" step="any" inputmode="decimal"')
          + '<button class="btn icon tap" data-act="tap" data-i="' + i + '" title="Tap tempo: tap this repeatedly in time">Tap</button></span>')
        + fld('End tempo', inp(i, 'bpmEnd', s.bpmEnd, 'type="number" min="10" max="400" step="any" inputmode="decimal" placeholder="steady"'))
        + fld('Beat unit', sel(i, 'beatUnit', BEAT_UNITS, s.beatUnit))
        + fld('Meter', '<span class="meter">' + inp(i, 'num', s.num, 'type="number" min="1" max="32" inputmode="numeric"')
          + '<span>/</span>' + sel(i, 'den', DENOMS.map((d) => [d, d]), s.den) + '</span>')
        + fld('Grouping', inp(i, 'grouping', s.grouping, 'type="text" maxlength="60" placeholder="auto"'))
        + fld('Bars', inp(i, 'bars', s.bars, 'type="number" min="1" max="9999" inputmode="numeric"'))
        + fld('Click', sel(i, 'click', CLICK_MODES, s.click))
        + fld('Advance', sel(i, 'advance', [['auto', 'Auto'], ['manual', 'Wait for pedal']], s.advance))
        + fld('Count-in bars', inp(i, 'countIn', s.countIn, 'type="number" min="0" max="8" inputmode="numeric"'))
        + fld('Score bar', inp(i, 'startBar', s.startBar, 'type="number" min="1" max="99999" inputmode="numeric" placeholder="auto"'))
        + performanceNotes(i, s)
        + '<details class="section-calculator"><summary>Calculate steady tempo from video times</summary>'
        + '<p class="muted small">Uses this section’s Bars, Meter and Beat unit. Applying replaces Tempo and clears End tempo. Start/end are absolute video positions, not timing anchors.</p><div class="sound-fields">'
        + fld('Video start (m:ss)', inp(i, 'calcStart', s.calcStart, 'type="text" maxlength="32" placeholder="0:25.0"'))
        + fld('Video end (m:ss)', inp(i, 'calcEnd', s.calcEnd, 'type="text" maxlength="32" placeholder="0:45.0"'))
        + '</div><button class="btn small" data-act="calculate" data-i="' + i + '">Set steady tempo</button>'
        + '<p class="calc-result small" id="calc-result-' + i + '" role="status"></p></details>'
        + '<details class="subdivision-settings"><summary>Subdivision sound</summary><p class="muted small">Choose Every meter unit to hear all six quavers in 6/8. Pitch is a semitone offset; velocity 0 mutes these notes.</p><div class="sound-fields">'
        + fld('Inner notes pitch (semitones)', inp(i, 'unitPitch', s.unitPitch, 'type="number" min="-24" max="24" step="1"'))
        + fld('Inner notes velocity (0–127)', inp(i, 'unitVelocity', s.unitVelocity, 'type="number" min="0" max="127" step="1"'))
        + fld('Extra subdivisions pitch (semitones)', inp(i, 'subPitch', s.subPitch, 'type="number" min="-24" max="24" step="1"'))
        + fld('Extra subdivisions velocity (0–127)', inp(i, 'subVelocity', s.subVelocity, 'type="number" min="0" max="127" step="1"'))
        + '</div></details>';
    }
    return '<div class="sec ' + s.type + '" data-row="' + i + '"><div class="num">' + (i + 1) + '</div><div class="fields">'
      + f + '</div>' + acts + '<div class="info" id="info-' + i + '"></div><div class="section-timing" id="timing-' + i + '"></div></div>';
  }

  function renderSections() {
    const secs = sections();
    $('sections').innerHTML = secs.length
      ? secs.map((s, i) => sectionRow(s, i, secs.length)).join('')
      : '<p class="muted">No sections yet. Add one below.</p>';
    updateInfos();
    renderTester();
  }

  function updateInfos() {
    const secs = sections();
    const sb = startBars(secs);
    const timeline = sectionTimeline();
    let total = 0, bars = 0, open = false;
    secs.forEach((s, i) => {
      const timing = timeline[i];
      $('timing-' + i).textContent = 'Video: ' + (timing.startEstimated ? '≈ ' : '') + timecode(timing.start) + ' → '
        + (timing.endEstimated ? '≈ ' : '') + timecode(timing.end)
        + (s.type === 'pause' ? ' + cue wait (unknown)' : s.advance === 'manual' ? ' for one pass, then repeats until cued' : '')
        + (timing.startEstimated ? ' · estimated after an earlier cue/repeat' : '');
      if (s.type === 'pause') open = true;
      else { total += sectionSeconds(s); bars += s.bars; if (s.advance === 'manual') open = true; }
      const el = $('info-' + i);
      if (!el) return;
      if (s.type === 'pause') { el.textContent = 'Silent: waits for Next / pedal'; el.className = 'info'; return; }
      const ok = !s.grouping || parseGrouping(s.grouping, s.num);
      const g = groupsOf(s);
      const parts = [
        'Bars ' + sb[i] + (s.bars > 1 ? '–' + (sb[i] + s.bars - 1) : ''),
        '≈ ' + fmtTime(sectionSeconds(s)) + (s.advance === 'manual' ? ' per pass, repeats until pedal' : ''),
        (g.length === s.num ? g.length + ' beats' : 'beats ' + g.join('+')) + ', ' + unitLabel(s) + '=' + s.bpm,
      ];
      if (!ok) parts.push('⚠ grouping must add up to ' + s.num + ' (using default)');
      el.textContent = parts.join(' · ');
      el.className = 'info' + (ok ? '' : ' warn');
    });
    $('track-summary').textContent = secs.length + ' section' + (secs.length === 1 ? '' : 's') + ' · ' + bars + ' bars · ≈ '
      + fmtTime(total) + (open ? ' plus pauses / repeats' : '');
  }

  function renderEditor() {
    if (!current) return;
    $('track-title').value = current.title;
    $('track-notes').value = current.notes;
    $('track-offset').value = timecode(current.videoOffset);
    renderSections();
  }

  const RAW_FIELDS = ['name', 'notes', 'grouping', 'calcStart', 'calcEnd'];
  function onFieldInput(e) {
    const el = e.target, f = el.dataset.f;
    if (!f || !current) return;
    if (e.type === 'input' && el.tagName === 'SELECT') return;   // handled on change
    const i = +el.dataset.i;
    const s = current.sections[i];
    if (!s) return;
    if (f === 'type') {
      stopTester();
      if (E.playing) stop(false);
      current.sections[i] = normSection(Object.assign({}, s, { type: el.value, name: s.name || (el.value === 'pause' ? 'Pause' : '') }));
      markDirty(); renderSections(); renderSetlist(); renderIdle();
      return;
    }
    const n = normSection(Object.assign({}, s, { [f]: el.value }));
    current.sections[i] = n;
    if (['bpm', 'bpmEnd', 'num', 'den', 'bars', 'beatUnit', 'calcStart', 'calcEnd'].includes(f)) {
      $('calc-result-' + i).textContent = '';
    }
    if (e.type === 'change' && el.tagName === 'INPUT' && !RAW_FIELDS.includes(f)) el.value = n[f] == null ? '' : n[f];
    markDirty();
    updateInfos();
    renderTester();
    renderSetlist();
    if (!E.playing) renderIdle();
  }

  function onSectionAction(e) {
    const b = e.target.closest('[data-act]');
    if (!b || !current) return;
    const i = +b.dataset.i, secs = current.sections, act = b.dataset.act;
    if (act === 'tap') { tapTempo(i); return; }
    if (act === 'calculate') { calculateTempo(i); return; }
    if (act === 'play') {
      if (E.playing) stop(false);
      switchView('perform');
      selectIdx(i);
      start(i);
      return;
    }
    if (act === 'del' && !window.confirm('Delete section ' + (i + 1) + (secs[i].name ? ' “' + secs[i].name + '”' : '') + '?')) return;
    stopTester();
    if (E.playing) stop(false);
    if (act === 'up' && i > 0) [secs[i - 1], secs[i]] = [secs[i], secs[i - 1]];
    else if (act === 'down' && i < secs.length - 1) [secs[i + 1], secs[i]] = [secs[i], secs[i + 1]];
    else if (act === 'dup') secs.splice(i + 1, 0, normSection(Object.assign({}, secs[i], { startBar: null })));
    else if (act === 'del') secs.splice(i, 1);
    selectedIdx = clamp(selectedIdx, 0, Math.max(0, secs.length - 1));
    markDirty(); renderSections(); renderSetlist(); renderIdle();
  }

  function addSection(pause) {
    if (!current) return;
    if (E.playing) stop(false);
    stopTester();
    const secs = current.sections;
    const prev = secs.slice().reverse().find((s) => s.type !== 'pause');
    const s = pause
      ? defaultSection({ type: 'pause', name: 'Pause' })
      : normSection(prev ? Object.assign({}, prev, { name: '', notes: '', startBar: null, countIn: 0, bpmEnd: null, calcStart: '', calcEnd: '' }) : { countIn: secs.length ? 0 : 1 });
    secs.push(s);
    markDirty(); renderSections(); renderSetlist(); renderIdle();
    const row = document.querySelector('[data-row="' + (secs.length - 1) + '"]');
    if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); const nm = row.querySelector('[data-f="name"]'); if (nm) nm.focus({ preventScroll: true }); }
  }

  // ------------------------------------------------------------ views & UI
  let performanceMode = false, nativeFullscreen = false;
  function setPerformanceMode(on) {
    performanceMode = on;
    document.body.classList.toggle('performance-mode', on);
    $('btn-fullscreen').textContent = on ? 'Exit fullscreen performance' : 'Fullscreen performance';
    $('btn-fullscreen').setAttribute('aria-pressed', String(on));
  }

  async function toggleFullscreen() {
    if (performanceMode) {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
      } catch (e) {
        toast('Could not exit browser fullscreen: ' + e.message);
      }
      nativeFullscreen = false;
      setPerformanceMode(false);
      return;
    }
    switchView('perform');
    showPedalSetup(false);
    setPerformanceMode(true);
    window.scrollTo(0, 0);
    if (!document.documentElement.requestFullscreen) {
      toast('Browser fullscreen is unavailable — using distraction-free view');
      return;
    }
    try {
      await document.documentElement.requestFullscreen();
      nativeFullscreen = true;
    } catch (e) {
      toast('Browser fullscreen unavailable — using distraction-free view (' + e.message + ')');
    }
  }

  function switchView(name) {
    if (name !== 'edit') stopTester();
    document.body.classList.toggle('editing', name === 'edit');
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === name));
    $('view-perform').hidden = name !== 'perform';
    $('view-edit').hidden = name !== 'edit';
    if (name === 'edit') renderEditor();
  }

  function renderAll() {
    renderEditor();
    renderSetlist();
    renderIdle();
    renderTransport();
  }

  const KEY_NAMES = { ArrowRight: '→', ArrowLeft: '←', ArrowUp: '↑', ArrowDown: '↓', PageUp: 'Page Up', PageDown: 'Page Down', ' ': 'Space' };
  const keyName = (k) => KEY_NAMES[k] || k;
  function pedalKey(e) {
    const aliases = { Up: 'ArrowUp', Down: 'ArrowDown', Left: 'ArrowLeft', Right: 'ArrowRight', Spacebar: ' ' };
    if (e.key && e.key !== 'Unidentified') return aliases[e.key] || e.key;
    return ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Enter'].includes(e.code)
      ? e.code : e.code === 'Space' ? ' ' : '';
  }
  function renderPedalKeys() {
    const backKeys = BACK_KEYS.filter((k) => !prefs.keys.includes(k));
    $('pedal-keys').textContent = 'Start / Next / resume: ' + prefs.keys.map(keyName).join(', ')
      + (backKeys.length ? ' · Back: ' + backKeys.map(keyName).join(', ') : '')
      + (prefs.keys.includes(' ') ? '' : ' · Start/pause/resume: Space') + ' · Stop: Esc';
  }

  let learning = false, learnTimer = null;
  function renderPedalFocus() {
    $('pedal-focus').textContent = document.hasFocus()
      ? 'Page focused — ready for keyboard input.'
      : 'Page not focused — click this panel before pressing the pedal.';
  }
  function showPedalSetup(on) {
    $('pedal-setup').hidden = !on;
    document.querySelectorAll('[data-pedal-setup]').forEach((b) => b.setAttribute('aria-expanded', String(on)));
    if (!on) setLearning(false);
    else {
      renderPedalFocus();
      $('pedal-setup').scrollIntoView({ block: 'start' });
    }
  }
  function setLearning(on) {
    clearTimeout(learnTimer);
    learning = on;
    $('btn-learn').classList.toggle('learning', on);
    $('btn-learn').textContent = on ? 'Cancel learning' : 'Learn pedal key';
    if (on) {
      $('pedal-input').textContent = 'Listening — press and release the pedal (Esc cancels). Space and arrow keys are supported.';
      learnTimer = setTimeout(() => {
        setLearning(false);
        $('pedal-input').textContent = 'No usable key received. Click this panel, check keyboard mode and Bluetooth pairing, then try Learn again.';
      }, 12000);
    }
  }

  // MIDI: any note-on, or a controller (e.g. sustain pedal CC64) going down, counts as Next.
  let midiAccess = null;
  const ccState = {};
  function onMidi(e) {
    const [st, d1, d2] = e.data;
    const cmd = st & 0xf0;
    let hit = false;
    if (cmd === 0x90 && d2 > 0) hit = true;
    else if (cmd === 0xb0) { const prev = ccState[d1] || 0; ccState[d1] = d2; hit = d2 >= 64 && prev < 64; }
    if (!hit) return;
    if (learning) {
      setLearning(false);
      $('pedal-input').textContent = 'MIDI pedal detected. MIDI note-on / controller presses use Start / Next / resume.';
      toast('MIDI pedal detected ✓');
      return;
    }
    advance();
  }
  function bindMidi() {
    if (!midiAccess) return;
    midiAccess.inputs.forEach((input) => { input.onmidimessage = prefs.midi ? onMidi : null; });
  }
  async function setMidi(on) {
    prefs.midi = on;
    savePrefs();
    if (!on) { bindMidi(); return; }
    if (!navigator.requestMIDIAccess) {
      toast('MIDI isn’t supported in this browser. Try Chrome or Edge.');
      prefs.midi = false; savePrefs(); $('pref-midi').checked = false;
      return;
    }
    try {
      midiAccess = midiAccess || await navigator.requestMIDIAccess();
      midiAccess.onstatechange = bindMidi;
      bindMidi();
      const n = midiAccess.inputs.size;
      toast(n ? 'MIDI ready (' + n + ' input' + (n === 1 ? '' : 's') + ')' : 'MIDI on — connect a device');
    } catch (e) {
      toast('MIDI access was blocked');
      prefs.midi = false; savePrefs(); $('pref-midi').checked = false;
    }
  }

  function isTyping(t) {
    if (!t) return false;
    if (t.tagName === 'TEXTAREA' || t.isContentEditable) return true;
    if (t.tagName === 'INPUT') return !['checkbox', 'range', 'button', 'file'].includes(t.type);
    return false;
  }

  function onKeyDown(e) {
    const k = pedalKey(e);
    if ((learning || (!$('pedal-setup').hidden && !isTyping(e.target))) && !e.repeat) {
      $('pedal-input').textContent = 'Received: ' + (k ? keyName(k) : 'unidentified key')
        + (e.code ? ' (code: ' + e.code + ')' : '');
    }
    if (learning) {
      e.preventDefault();
      if (k === 'Escape') { setLearning(false); $('pedal-input').textContent = 'Learning cancelled.'; return; }
      if (e.repeat || !k || ['Shift', 'Control', 'Alt', 'Meta', 'AltGraph'].includes(k)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) {
        $('pedal-input').textContent = 'Modified shortcut received. Use an unmodified arrow, Page Up/Down, Space or Enter keyboard mode.';
        return;
      }
      if (!prefs.keys.includes(k)) prefs.keys.push(k);
      savePrefs(); renderPedalKeys(); setLearning(false);
      $('pedal-input').textContent = 'Learned ' + keyName(k) + ' for Start / Next / resume. Press again to try it.';
      toast('Pedal key set: ' + keyName(k));
      return;
    }
    if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    if ((k === 't' || k === 'T') && !prefs.keys.includes(k)) {
      e.preventDefault();
      if (!e.repeat) tapTempo();
      return;
    }
    const handled = k === ' ' || k === 'Escape' || prefs.keys.includes(k) || BACK_KEYS.includes(k);
    if (!handled) return;
    e.preventDefault();
    if (e.repeat) return;
    if (k === 'Escape') {
      stopTester();
      stop(false);
      if (performanceMode) toggleFullscreen();
    }
    else if (prefs.keys.includes(k)) advance();
    else if (k === ' ') {
      if (document.body.classList.contains('editing')) {
        if (E.playing) stop(false);
        tester.running ? stopTester() : startTester();
      } else togglePlay();
    }
    else back();
  }

  function wire() {
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('focus', renderPedalFocus);
    window.addEventListener('blur', renderPedalFocus);
    document.querySelectorAll('[data-pedal-setup]').forEach((b) => b.addEventListener('click', () => showPedalSetup($('pedal-setup').hidden)));
    $('btn-editor-fullscreen').addEventListener('click', () => {
      switchView('perform');
      showPedalSetup(false);
      if (!performanceMode) toggleFullscreen();
    });
    $('btn-start-fullscreen').addEventListener('click', () => {
      showPedalSetup(false);
      if (!performanceMode) toggleFullscreen();
      if (!E.playing || E.suspended) togglePlay();
    });
    $('btn-fullscreen').addEventListener('click', toggleFullscreen);
    document.addEventListener('fullscreenchange', () => {
      if (!document.fullscreenElement && nativeFullscreen) {
        nativeFullscreen = false;
        setPerformanceMode(false);
      }
    });
    // Keep focus off buttons/selects so pedal keys and Space never re-trigger them.
    document.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) b.blur(); });

    $('btn-play').addEventListener('click', togglePlay);
    $('btn-stop').addEventListener('click', () => stop(false));
    $('btn-next').addEventListener('click', () => (E.playing ? advance() : selectIdx(selectedIdx + 1)));
    $('btn-prev').addEventListener('click', () => (E.playing ? back() : selectIdx(selectedIdx - 1)));
    // pointerdown gives tighter timing than click for tapping.
    $('btn-tap').addEventListener('pointerdown', (e) => { e.preventDefault(); tapTempo(); });
    $('btn-tap').addEventListener('keydown', (e) => { if (e.key === 'Enter') tapTempo(); });
    $('setlist').addEventListener('click', (e) => { const li = e.target.closest('li[data-i]'); if (li) jumpTo(+li.dataset.i); });
    $('setlist').addEventListener('dblclick', (e) => { const li = e.target.closest('li[data-i]'); if (li && !E.playing) start(+li.dataset.i); });
    document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => switchView(t.dataset.view)));

    $('track-select').addEventListener('change', (e) => { selectTrack(e.target.value); e.target.blur(); });
    const closeMenu = () => { document.querySelector('.ct-menu').open = false; };
    // No window.prompt here: some browsers (in-app / embedded) block it, which made New appear to do nothing.
    $('btn-new-track').addEventListener('click', () => {
      addTrack(makeTrack('New click track', [{ countIn: 1 }]));
      switchView('edit');
      const t = $('track-title');
      t.focus(); t.select();
    });
    $('btn-dup-track').addEventListener('click', () => {
      closeMenu();
      if (current) {
        const t = makeTrack(current.title + ' (copy)', current.sections.map((s) => Object.assign({}, s)), current.notes);
        t.videoOffset = current.videoOffset;
        addTrack(t);
      }
    });
    $('btn-export-track').addEventListener('click', () => { closeMenu(); exportTrack(); });
    $('import-file').addEventListener('change', (e) => { closeMenu(); if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; });
    $('btn-delete-track').addEventListener('click', () => { closeMenu(); deleteTrack(); });

    $('track-title').addEventListener('input', (e) => {
      if (!current) return;
      current.title = e.target.value.trim().slice(0, 255) || 'Untitled';
      const o = $('track-select').querySelector('option[value="' + CSS.escape(String(current.id)) + '"]');
      if (o) o.textContent = current.title;
      markDirty();
    });
    $('track-title').addEventListener('change', refreshTrackSelect);
    $('track-notes').addEventListener('input', (e) => { if (current) { current.notes = e.target.value.slice(0, 2000); markDirty(); } });
    $('track-offset').addEventListener('input', (e) => e.target.setCustomValidity(''));
    $('track-offset').addEventListener('change', (e) => {
      const offset = parseTimecode(e.target.value);
      if (offset == null || offset > 86400) {
        e.target.setCustomValidity('Enter 0–86400 seconds, m:ss or h:mm:ss.');
        e.target.reportValidity();
        return;
      }
      if (!current) return;
      if (E.playing) clock.videoStart += offset - current.videoOffset;
      current.videoOffset = Math.round(offset * 100) / 100;
      e.target.value = timecode(current.videoOffset);
      markDirty(); updateInfos(); renderClock();
    });
    $('tester-section').addEventListener('change', (e) => {
      const running = tester.running;
      const idx = Number(e.target.value);
      stopTester();
      tester.idx = idx;
      tap.times = [];
      if (running) startTester();
      else renderTester();
      e.target.blur();
    });
    $('tester-start').addEventListener('click', () => tester.running ? stopTester() : startTester());
    $('tester-tap').addEventListener('click', () => tapTempo(tester.idx));
    $('sections').addEventListener('input', onFieldInput);
    $('sections').addEventListener('change', onFieldInput);
    $('sections').addEventListener('click', onSectionAction);
    $('btn-add-section').addEventListener('click', () => addSection(false));
    $('btn-add-pause').addEventListener('click', () => addSection(true));

    $('pref-volume').value = prefs.volume;
    $('pref-sound').value = prefs.sound;
    $('pref-flash').checked = !!prefs.flash;
    $('pref-midi').checked = !!prefs.midi;
    $('pref-volume').addEventListener('input', (e) => { prefs.volume = +e.target.value; applyVolume(); savePrefs(); });
    $('pref-volume').addEventListener('change', (e) => e.target.blur());
    $('pref-sound').addEventListener('change', (e) => {
      prefs.sound = e.target.value; savePrefs(); e.target.blur();
      if (E.playing || tester.running) return;
      ensureAudio(); playClick(ctx.currentTime + 0.03, 3); playClick(ctx.currentTime + 0.33, 2);
    });
    $('pref-flash').addEventListener('change', (e) => { prefs.flash = e.target.checked; savePrefs(); e.target.blur(); });
    $('pref-midi').addEventListener('change', (e) => { setMidi(e.target.checked); e.target.blur(); });
    $('btn-learn').addEventListener('click', () => {
      const on = !learning;
      setLearning(on);
      if (!on) $('pedal-input').textContent = 'Learning cancelled.';
    });
    $('btn-reset-keys').addEventListener('click', () => {
      setLearning(false);
      prefs.keys = DEFAULT_KEYS.slice();
      savePrefs(); renderPedalKeys();
      $('pedal-input').textContent = 'Reset: Right, Down and Page Down start / advance. Left, Up and Page Up go back when stopped.';
      toast('Pedal keys reset');
    });
  }

  async function init() {
    wire();
    renderPedalKeys();
    renderAll();
    requestAnimationFrame(frame);
    await loadTracks();
    selectTrack(loadJSON(LS_LAST, null));
    if (prefs.midi) setMidi(true);
  }

  init();
})();
