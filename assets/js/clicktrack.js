/* Click track / programmable metronome — see clicktrack.py and templates/clicktrack/index.html */
(function () {
  'use strict';

  const CFG = window.CLICKTRACK_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  const LS_PREFS = 'clicktrack.prefs';
  const LS_TRACKS = 'clicktrack.tracks.' + (CFG.userKey || 'anon');
  const LS_ANON = 'clicktrack.tracks.anon';
  const LS_LAST = 'clicktrack.last.' + (CFG.userKey || 'anon');
  const OFFLINE_KEY = 'clicktrack.offline-show.v1';
  const OFFLINE_CACHE = 'clicktrack-offline-shell-v1';

  const LOOKAHEAD = 0.12;       // seconds of audio scheduled ahead
  const TIMER_MS = 25;
  const DEFAULT_KEYS = ['ArrowRight', 'PageDown', 'ArrowDown'];
  const DOUBLE_PRESS_MS = 350;
  const PEDAL_ACTIONS = { forward: 'Forward', pause: 'Pause / resume', back: 'Back' };
  const defaultBindings = () => ({
    single: Object.assign(Object.create(null), {
      ArrowLeft: 'back', ArrowUp: 'back', PageUp: 'pause', ' ': 'pause',
      ...Object.fromEntries(DEFAULT_KEYS.map((key) => [key, 'forward'])),
    }),
    double: Object.assign(Object.create(null), { PageUp: 'back' }),
  });

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
  function setStatus(msg) {
    $('save-status').textContent = msg || '';
    $('quick-edit-save-status').textContent = msg || '';
  }

  // --------------------------------------------------------------- sections
  function defaultSection(over) {
    const section = Object.assign({
      type: 'click', name: '', notes: '', bpm: 120, bpmEnd: null, beatUnit: 'auto',
      num: 4, den: 4, grouping: '', bars: 8, advance: 'auto', click: 'beats', countIn: null, startBar: null,
      unitPitch: 0, unitVelocity: 53, subPitch: 0, subVelocity: 32,
      calcStart: '', calcEnd: '',
    }, over || {});
    if (!over || !Object.prototype.hasOwnProperty.call(over, 'click')) {
      section.click = isCompound(section) ? 'units' : 'beats';
    }
    section.compoundClickDefaulted = true;
    return section;
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
    s.click = CLICK_MODES.some((c) => c[0] === raw.click) ? raw.click : (isCompound(s) ? 'units' : 'beats');
    if (!raw.compoundClickDefaulted && isCompound(s) && s.click === 'beats') s.click = 'units';
    s.unitPitch = num(raw.unitPitch, 0, -24, 24);
    s.unitVelocity = num(raw.unitVelocity, 53, 0, 127, true);
    s.subPitch = num(raw.subPitch, 0, -24, 24);
    s.subVelocity = num(raw.subVelocity, 32, 0, 127, true);
    s.countIn = raw.countIn == null || raw.countIn === '' ? null : num(raw.countIn, 0, 0, 8, true);
    s.startBar = raw.startBar == null || raw.startBar === '' ? null : num(raw.startBar, null, 1, 99999, true);
    s.figure = String(raw.figure || '').trim().slice(0, 32);
    s.visualCue = raw.visualCue === true;
    s.visualEndCue = raw.visualEndCue === true;
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

  const figureLabel = (label) => /^fig(?:ure)?\.?\s/i.test(label) ? label : 'Figure ' + label;

  // ------------------------------------------------------------------ prefs
  const offlineShow = CFG.offline ? loadJSON(OFFLINE_KEY, null) : null;
  const prefs = Object.assign({ volume: 1, sound: 'beep', flash: true, midi: false, beatVisual: 'circles' },
    CFG.offline ? offlineShow && offlineShow.prefs : loadJSON(LS_PREFS, {}));
  if (!['circles', 'pendulum', 'both'].includes(prefs.beatVisual)) prefs.beatVisual = 'circles';
  const bindings = defaultBindings();
  if (prefs.pedalBindings) {
    for (const gesture of ['single', 'double']) {
      bindings[gesture] = Object.assign(Object.create(null), Object.fromEntries(
        Object.entries(prefs.pedalBindings[gesture] || {}).filter(([, action]) => Object.hasOwn(PEDAL_ACTIONS, action))
      ));
    }
  } else if (Array.isArray(prefs.keys)) {
    for (const key of prefs.keys) {
      if (typeof key === 'string' && key !== 'Escape' && key !== 'PageUp') {
        bindings.single[key] = 'forward';
      }
    }
  }
  prefs.pedalBindings = bindings;
  delete prefs.keys;
  const savePrefs = () => { if (!CFG.offline) saveJSON(LS_PREFS, prefs); };

  // ------------------------------------------------------------------ audio
  let ctx = null, master = null, noiseBuf = null;
  const sources = new Set();
  let audioRevision = 0;
  function trackSource(source) {
    sources.add(source);
    source.addEventListener('ended', () => sources.delete(source), { once: true });
  }
  function cancelClicks() {
    audioRevision++;
    sources.forEach((source) => source.stop());
    sources.clear();
  }

  function ensureAudio(autoResume = true) {
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
    if (autoResume && ctx.state !== 'running' && !E.suspended) ctx.resume().catch((e) => toast('Audio could not resume: ' + e.message));
    return ctx;
  }

  function applyVolume() { if (master) master.gain.value = prefs.volume; }

  const mediaDevices = navigator.mediaDevices;
  const canRouteAudio = typeof (window.AudioContext || window.webkitAudioContext)?.prototype.setSinkId === 'function';
  const outputHint = 'If your glasses are not listed, select them in your device’s audio output controls (Control Centre on iPhone), use System default if shown here, then play the test clicks.';
  let audioOutput = { id: '', label: 'System default' }, outputBusy = false, outputRequest = 0;

  function renderOutputControls() {
    $('audio-output').disabled = $('btn-choose-output').disabled = E.playing || outputBusy;
    $('btn-test-clicks').disabled = E.playing || outputBusy;
  }

  async function refreshAudioOutputs() {
    const request = ++outputRequest;
    const select = $('audio-output');
    let outputs = [];
    try {
      outputs = (await mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audiooutput' && d.deviceId && d.deviceId !== 'default');
    } catch (e) {
      if (request === outputRequest && !outputBusy) {
        $('audio-output-status').textContent = 'Could not list audio outputs: ' + e.message + ' ' + outputHint;
      }
      return;
    }
    if (request !== outputRequest) return;
    const wasListed = Array.from(select.options).some((o) => o.value === audioOutput.id && !o.disabled);
    select.replaceChildren(new Option('System default', ''));
    outputs.forEach((d, i) => select.add(new Option(d.label || 'Audio output ' + (i + 1), d.deviceId)));
    if (audioOutput.id && !outputs.some((d) => d.deviceId === audioOutput.id)) {
      const option = new Option(audioOutput.label, audioOutput.id);
      option.disabled = true;
      select.add(option);
      if (wasListed && !outputBusy) {
        $('audio-output-status').textContent = audioOutput.label + ' is no longer listed. Reconnect it or select another output, then test again.';
      }
    }
    select.value = audioOutput.id;
  }

  async function selectAudioOutput(choose) {
    if (E.playing || tester.running || outputBusy) { $('audio-output').value = audioOutput.id; return; }
    const select = $('audio-output'), status = $('audio-output-status');
    outputBusy = true;
    renderOutputControls();
    status.textContent = 'Choosing audio output…';
    try {
      const device = choose ? await mediaDevices.selectAudioOutput()
        : { deviceId: select.value, label: select.selectedOptions[0].textContent };
      cancelClicks();
      await ensureAudio().setSinkId(device.deviceId);
      audioOutput = { id: device.deviceId, label: device.label || 'Selected audio device' };
      status.textContent = 'Output: ' + audioOutput.label + '. Play the test clicks to confirm.';
      $('pre-show-audio').textContent = '';
    } catch (e) {
      const message = e.name === 'NotAllowedError' ? 'Selection cancelled or not permitted.' : e.message;
      status.textContent = 'Output not changed (' + audioOutput.label + '): ' + message + ' ' + outputHint;
    } finally {
      if (!Array.from(select.options).some((o) => o.value === audioOutput.id)) {
        select.add(new Option(audioOutput.label, audioOutput.id));
      }
      select.value = audioOutput.id;
      outputBusy = false;
      renderOutputControls();
    }
  }

  function wireAudioOutputs() {
    if (!canRouteAudio || !mediaDevices?.enumerateDevices) {
      $('audio-output-status').textContent = 'This browser uses your system audio output. ' + outputHint;
      return;
    }
    $('audio-output-controls').hidden = false;
    $('btn-choose-output').hidden = typeof mediaDevices.selectAudioOutput !== 'function';
    $('audio-output-status').textContent = 'Output: System default. Selection applies to click audio in this page until it is reloaded. ' + outputHint;
    $('audio-output').addEventListener('change', () => selectAudioOutput(false));
    $('btn-choose-output').addEventListener('click', () => selectAudioOutput(true));
    mediaDevices.addEventListener('devicechange', refreshAudioOutputs);
  }

  function envGain(audio, time, peak, decay) {
    const g = audio.ctx.createGain();
    g.gain.setValueAtTime(0.0001, time);
    g.gain.exponentialRampToValueAtTime(peak, time + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, time + decay);
    g.connect(audio.master);
    return g;
  }

  function noise(audio, time, dur, filterType, freq, q, out) {
    const src = audio.ctx.createBufferSource();
    if (audio.track) audio.track(src);
    src.buffer = audio.noiseBuf;
    const f = audio.ctx.createBiquadFilter();
    f.type = filterType; f.frequency.value = freq; f.Q.value = q;
    src.connect(f); f.connect(out);
    src.start(time); src.stop(time + dur);
  }

  function tone(audio, time, type, freq, dur, out) {
    const o = audio.ctx.createOscillator();
    if (audio.track) audio.track(o);
    o.type = type; o.frequency.value = freq;
    o.connect(out);
    o.start(time); o.stop(time + dur);
  }

  // level: 3 downbeat, 2 beat, 1 unit, 0 subdivision
  function playClick(time, level, section, audio = { ctx, master, noiseBuf, track: trackSource, sound: prefs.sound }) {
    let peak = [0.25, 0.42, 0.7, 1][level], pitch = 1;
    if (section && level < 2) {
      const prefix = level === 1 ? 'unit' : 'sub';
      peak = section[prefix + 'Velocity'] / 127;
      pitch = Math.pow(2, section[prefix + 'Pitch'] / 12);
    }
    if (peak === 0) return;
    if (audio.sound === 'wood') {
      const g = envGain(audio, time, peak, 0.06);
      tone(audio, time, 'sine', [700, 900, 1100, 1500][level] * pitch, 0.07, g);
      noise(audio, time, 0.03, 'bandpass', [1800, 2200, 2600, 3400][level] * pitch, 6, envGain(audio, time, peak * 0.8, 0.025));
    } else if (audio.sound === 'hihat') {
      noise(audio, time, 0.12, 'highpass', Math.min(audio.ctx.sampleRate / 2, (level === 3 ? 5000 : 7500) * pitch), 0.7, envGain(audio, time, peak, [0.03, 0.04, 0.05, 0.1][level]));
    } else {
      tone(audio, time, 'square', [660, 880, 1050, 1760][level] * pitch, 0.06, envGain(audio, time, peak * 0.5, 0.045));
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

  let wakeLock = null, wakeRequest = null, wakeGeneration = 0;
  const showWake = (message) => { $('pre-show-wake').textContent = message; };
  async function requestWake() {
    if (!('wakeLock' in navigator)) { showWake('Screen wake lock is unavailable in this browser. Check your device auto-lock settings.'); return; }
    if (wakeLock || wakeRequest) return;
    const generation = wakeGeneration;
    const request = navigator.wakeLock.request('screen');
    wakeRequest = request;
    try {
      const lock = await request;
      if (generation !== wakeGeneration && !(E.playing && !E.suspended)) { await lock.release(); return; }
      wakeLock = lock;
      showWake('Screen wake lock active.');
      lock.addEventListener('release', () => {
        if (wakeLock !== lock) return;
        wakeLock = null;
        showWake('Screen wake lock released by the browser.');
      });
    } catch (e) {
      if (generation === wakeGeneration) showWake('Could not keep the screen awake: ' + e.message);
    } finally {
      if (wakeRequest === request) wakeRequest = null;
    }
  }
  function releaseWake() {
    wakeGeneration++;
    if (wakeLock) {
      const lock = wakeLock;
      wakeLock = null;
      lock.release().catch((e) => showWake('Could not release screen wake lock: ' + e.message));
    }
    showWake('Screen wake lock not active.');
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && E.playing && !E.suspended) requestWake(); });

  // ----------------------------------------------------------------- engine
  // phase: stopped | countin | play | pause | end. bar/tick/sub describe the *next* step to schedule.
  const E = { playing: false, suspended: false, secIdx: 0, phase: 'stopped', bar: 0, tick: 0, sub: 0, nextTime: 0, queued: null, countIn: 0, phaseStart: 0, filmCountIn: false, swingSide: 1, startBarOffset: 0, countBpm: 120 };
  const clock = { start: 0, elapsed: 0, videoStart: 0 };
  let transportBusy = false;
  const visQ = [];
  const STREAMER_LEAD = 2;
  let cueOnEntry = false, cueRoute = 0;

  function barSeconds(s, bar) {
    if (s.bpmEnd == null) return s.num * unitSeconds(s, s.bpm);
    const steps = s.num * subdivOf(s);
    let seconds = 0;
    for (let i = 0; i < steps; i++) {
      seconds += unitSeconds(s, bpmAt(s, (bar + i / steps) / s.bars)) / subdivOf(s);
    }
    return seconds;
  }

  function streamerLead(secs, index, bar) {
    const firstBar = barSeconds(secs[index], bar);
    let seconds = 0;
    for (let i = index; i < secs.length; i++, bar = 0) {
      const s = secs[i];
      if (s.type === 'pause' || s.advance !== 'auto') return null;
      for (; bar < s.bars; bar++) {
        seconds += barSeconds(s, bar);
        // A later barline already provides enough warning.
        if (seconds - firstBar >= STREAMER_LEAD - 1e-8) return null;
      }
      const next = secs[i + 1];
      if ((s.visualEndCue || next?.visualCue) && seconds >= STREAMER_LEAD - 1e-8) return seconds;
      if (!next || next.type === 'pause') return null;
    }
    return null;
  }
  let shown = null;          // snapshot currently displayed
  let selectedIdx = 0;       // where Start begins
  let lastAdvanceAt = 0;
  let actionFeedback = '', actionFeedbackTimer;
  function confirmAction(message) {
    actionFeedback = message;
    clearTimeout(actionFeedbackTimer);
    actionFeedbackTimer = setTimeout(() => { actionFeedback = ''; renderNext(); }, 1800);
    renderNext();
  }

  let tracks = [];
  let current = null;
  let videoEnabled = true;
  const sections = () => (current ? current.sections : []);

  function pushVis(type, extra) {
    visQ.push(Object.assign({
      type, time: E.nextTime, secIdx: E.secIdx, phase: E.phase, bar: E.bar, tick: E.tick, sub: E.sub, queued: E.queued, countIn: E.countIn,
      phaseStart: E.phaseStart,
      filmCountIn: E.filmCountIn,
      startBarOffset: E.startBarOffset,
    }, extra || {}));
  }

  function enterSection(idx, flowing, countInOverride = null) {
    const secs = sections();
    E.phaseStart = E.nextTime;
    E.bar = 0; E.tick = 0; E.sub = 0; E.countIn = 0;
    E.startBarOffset = 0;
    E.filmCountIn = false;
    if (idx < 0 || idx >= secs.length) { E.phase = 'end'; pushVis('end'); return; }
    E.secIdx = idx;
    const s = secs[idx];
    E.countBpm = s.bpm;
    cueOnEntry = s.visualCue;
    if (s.type === 'pause') { E.phase = 'pause'; pushVis('state', { punch: cueOnEntry }); return; }
    if (!flowing) E.countIn = countInOverride ?? effectiveCountIn(s);
    E.phase = E.countIn > 0 ? 'countin' : 'play';
  }

  const countInSeconds = (section, bars) => bars * section.num * unitSeconds(section, section.bpm);
  const effectiveCountIn = (section) => section.countIn ?? Math.max(1, Math.ceil(2 / countInSeconds(section, 1)));
  const countInLabel = (section) => section.countIn == null
    ? 'Auto: ' + effectiveCountIn(section) + ' bars, at least 2 seconds'
    : section.countIn + ' bars';

  function filmCountIn(s, cueTime) {
    const unit = unitSeconds(s, s.bpm);
    const bar = s.num * unit;
    const fullBars = Math.floor((cueTime + 1e-8) / bar);
    const remainder = cueTime - fullBars * bar;
    // Work back from the downbeat, including any beat boundaries in a partial bar.
    let tick = s.num;
    const levels = unitLevels(s);
    for (let i = s.num - 1; i >= 0; i--) {
      if ((s.num - i) * unit > remainder + 1e-8) break;
      if (levels[i] >= 2) tick = i;
    }
    return {
      bars: fullBars + (tick < s.num ? 1 : 0),
      tick: tick % s.num,
      lead: fullBars * bar + (s.num - tick) * unit,
    };
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
    const bpm = counting ? E.countBpm : bpmAt(s, (E.bar + (E.tick + E.sub / subN) / s.num) / s.bars);
    const groups = groupsOf(s), groupIndex = groupIndexOf(s, E.tick);
    const groupStart = groups.slice(0, groupIndex).reduce((sum, length) => sum + length, 0);
    if (level >= 2) E.swingSide *= -1;
    const duration = unitSeconds(s, bpm) / subN;
    const swing = {
      side: E.swingSide, progress: (E.tick - groupStart + E.sub / subN) / groups[groupIndex],
      increment: 1 / (subN * groups[groupIndex]), duration,
    };
    if (audible(s, level, E.phase)) playClick(E.nextTime, level, s);
    const downbeat = !counting && E.tick === 0 && E.sub === 0;
    const punch = downbeat && cueOnEntry;
    if (downbeat) cueOnEntry = false;
    const lead = downbeat && practice?.media && E.queued == null ? streamerLead(sections(), E.secIdx, E.bar) : null;
    const beatsLeft = counting && E.filmCountIn
      ? (E.countIn - E.bar) * groupsOf(s).length - groupIndexOf(s, E.tick) : null;
    pushVis('tick', { level, bpm, punch, beatsLeft, swing, streamerEnd: lead === null ? null : E.nextTime + lead, cueRoute });
    E.nextTime += duration;

    if (++E.sub < subN) return;
    E.sub = 0;
    if (++E.tick < s.num) return;
    E.tick = 0;
    endOfBar(s);
  }

  function endOfBar(s) {
    E.bar++;
    if (E.queued != null) {
      const target = E.queued;
      E.queued = null;
      enterSection(target, true);
    } else if (E.phase === 'countin') {
      if (E.bar >= E.countIn) {
        E.phase = 'play';
        E.phaseStart = E.nextTime;
        E.bar = E.startBarOffset;
        E.filmCountIn = false;
      }
    } else if (E.bar >= s.bars && s.advance === 'auto') {
      if (s.visualEndCue) pushVis('visual-hit', { punch: true });
      enterSection(E.secIdx + 1, true);
    }
  }

  function start(fromIdx, countInOverride = null, fromVideoBeginning = false, barOffset = 0) {
    if (outputBusy) { toast('Finish choosing an audio output before starting.'); return; }
    if (!sections().length) return;
    stopTester();
    $('pre-show').open = false;
    cancelClicks();
    clearPracticeCues();
    ensureAudio();
    visQ.length = 0;
    E.playing = true;
    E.suspended = false;
    E.queued = null;
    E.swingSide = 1;
    const idx = clamp(fromIdx, 0, sections().length - 1);
    const s = sections()[idx];
    const videoStart = ctx.currentTime + 0.1;
    let cueTime = sectionTimeline()[idx].start;
    for (let bar = 0; bar < barOffset; bar++) cueTime += barSeconds(s, bar);
    const startBpm = bpmAt(s, barOffset / s.bars);
    const countSection = barOffset ? { ...s, bpm: startBpm } : s;
    const film = fromVideoBeginning && s.type !== 'pause' ? filmCountIn(s, cueTime) : null;
    const countIn = film?.bars ?? (s.type === 'pause' ? 0 : countInOverride ?? effectiveCountIn(countSection));
    const lead = film?.lead ?? countInSeconds(countSection, countIn);
    const intro = fromVideoBeginning ? Math.max(0, cueTime - lead) : 0;
    if (intro > 0) {
      E.nextTime = videoStart;
      pushVis('state', {
        secIdx: idx, phase: 'intro', phaseStart: videoStart, introEnd: videoStart + intro, bar: 0, tick: -1, sub: 0,
      });
    }
    E.nextTime = videoStart + intro;
    enterSection(idx, false, countIn);
    E.startBarOffset = barOffset;
    E.countBpm = startBpm;
    if (barOffset) cueOnEntry = false;
    if (E.phase === 'play') E.bar = barOffset;
    if (film?.bars) {
      E.filmCountIn = true;
      E.tick = film.tick;
    }
    clock.start = E.nextTime + lead;
    clock.elapsed = 0;
    clock.videoStart = cueTime;
    if (fromVideoBeginning && practice?.media && s.visualCue && cueTime >= STREAMER_LEAD) {
      const bar = countInSeconds(s, 1);
      const bars = Math.ceil((STREAMER_LEAD - 1e-8) / bar);
      const warning = bars <= countIn && bars * bar <= cueTime ? bars * bar : STREAMER_LEAD;
      practice.openingStreamer = { start: clock.start - warning, end: clock.start };
    }
    shown = null;
    timerStart();
    requestWake();
    scheduler();
    renderTransport();
    if (E.phase === 'pause' && !intro) renderStage({ secIdx: E.secIdx, phase: 'pause', bar: 0, tick: 0, sub: 0, queued: null });
  }

  function stop(reachedEnd) {
    cancelPendingPedal();
    actionFeedback = '';
    if (!E.playing) return;
    if (!reachedEnd) clearPracticeCues();
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
        if (E.phase === 'pause' && E.queued != null) leaveCue(E.queued);
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
      confirmAction(E.suspended ? 'Paused' : 'Resumed');
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
    if (!E.playing) {
      start(selectedIdx);
      if (E.playing) confirmAction('Playback started');
      return;
    }
    if (E.suspended) { togglePlay(); return; }
    if (E.phase === 'pause') {
      leaveCue(E.secIdx + 1);
      confirmAction('Next accepted');
      scheduler();
      return;
    }
    if (E.phase === 'end') return;
    if (E.queued == null) {
      E.queued = E.secIdx + 1;
      clearPracticeCues();
    }
    renderNext();
    renderSetlist();
  }

  function back() {
    if (!E.playing) { selectIdx(selectedIdx - 1); return; }
    if (E.phase === 'end' || E.secIdx === 0) return;
    const immediate = E.phase === 'pause' && !E.suspended;
    jumpTo(E.secIdx - 1);
    if (immediate) confirmAction('Back accepted');
  }

  function leaveCue(idx) {
    if (ctx.currentTime < E.phaseStart) return;
    E.queued = null;
    E.nextTime = Math.max(ctx.currentTime + 0.06, E.nextTime);
    enterSection(idx, false);
  }

  function jumpTo(idx) {
    if (!E.playing) { selectIdx(idx); return; }
    if (E.phase === 'pause' && !E.suspended) {
      leaveCue(idx);
      scheduler();
      return;
    }
    E.queued = idx;
    clearPracticeCues();
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
    if (CFG.offline) return;
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
    $('d-secnum').textContent = 'Section ' + (st.secIdx + 1) + ' of ' + secs.length
      + (s.figure ? ' · ' + figureLabel(s.figure) : '');
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
        ? Math.round(info.bpm) + ' → ' + Math.round(info.bpmEnd) + (info.bpmEnd > info.bpm ? ' accel.' : ' rit.')
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
      ci.innerHTML = '<div class="n">' + (st.filmCountIn ? '−' + st.beatsLeft : groupIndexOf(s, st.tick) + 1)
        + '</div><div class="t">' + (st.filmCountIn ? 'Beats to bar ' + sb[st.secIdx]
          : 'Count-in' + (st.countIn > 1 ? ' · bar ' + (st.bar + 1) + ' of ' + st.countIn : '')) + '</div>';
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
    const feedback = $('d-pedal-feedback');
    if (queued != null && queued !== st.secIdx) {
      feedback.hidden = false;
      const message = (queued < st.secIdx ? 'Back' : 'Next') + ' queued → '
        + (sb[target] == null ? title : 'b. ' + sb[target] + ' · ' + title);
      if (feedback.textContent !== message) feedback.textContent = message;
    } else {
      feedback.hidden = !actionFeedback;
      if (feedback.textContent !== actionFeedback) feedback.textContent = actionFeedback;
    }
    const preview = $('next-preview');
    preview.hidden = !s;
    $('d-next-name').textContent = 'Up next: ' + title;
    $('d-next-details').textContent = !next ? '' : next.type === 'pause' ? 'Pause / cue — waits for pedal'
      : 'Score bar ' + sb[target] + ' · ' + next.num + '/' + next.den + ' · ' + unitLabel(next) + '=' + Math.round(next.bpm)
        + (next.bpmEnd != null ? ' → ' + Math.round(next.bpmEnd) : '');
    $('d-next-notes').textContent = next ? next.notes : '';
    let change = '';
    if (s && s.type !== 'pause' && next && next.type !== 'pause') {
      const currentBpm = E.playing && st.phase === 'play' && st.bpm != null ? st.bpm : s.bpm;
      const currentBeat = beatUnitFrac(s), nextBeat = beatUnitFrac(next);
      const equivalentBpm = currentBpm * (currentBeat.n / currentBeat.d) / (nextBeat.n / nextBeat.d);
      const difference = Math.round(next.bpm) - Math.round(equivalentBpm);
      change = difference === 0 ? 'Same pace'
        : (difference > 0 ? 'Faster (+' : 'Slower (') + difference + ' BPM'
          + (unitLabel(s) !== unitLabel(next) ? ' at ' + unitLabel(next) : '') + ')';
      if (s.num !== next.num || s.den !== next.den) change += ' · meter change';
      if (unitLabel(s) !== unitLabel(next)) change += ' · beat unit changes';
    }
    $('d-next-change').textContent = change;
    let text = '', cls = '';
    if (!s) text = '';
    else if (!E.playing) text = 'Press Start, Next or the pedal to begin';
    else if (st.phase === 'intro') { text = 'Film intro — waiting for the clicks'; cls = 'waiting'; }
    else if (st.phase === 'pause') { text = 'Waiting — press Next / pedal for ' + title; cls = 'waiting'; }
    else if (st.phase === 'countin') {
      const left = st.countIn - st.bar;
      const units = st.filmCountIn ? st.beatsLeft + (st.beatsLeft === 1 ? ' beat' : ' beats')
        : left + ' bar' + (left === 1 ? '' : 's');
      text = 'Count-in: ' + units + ' left before ' + sectionTitle(s, st.secIdx)
        + ' · score bar ' + sb[st.secIdx] + (queued != null ? ' · next jump queued' : '');
    }
    else if (s.advance === 'manual' && queued == null) { text = 'Repeating — press Next / pedal for ' + title; cls = 'waiting'; }
    else {
      const left = queued != null ? 1 : Math.max(1, s.bars - st.bar);
      const beatsLeft = groupsOf(s).length - groupIndexOf(s, Math.max(0, st.tick));
      text = left > 1 ? left + ' bars left'
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
        : s.num + '/' + s.den + ' · ' + unitLabel(s) + '=' + Math.round(s.bpm) + (s.bpmEnd != null ? '→' + Math.round(s.bpmEnd) : '')
          + ' · ' + s.bars + ' bar' + (s.bars === 1 ? '' : 's') + (s.advance === 'manual' ? ' · wait' : '') + ' · b.' + sb[i];
      return '<li class="' + cls + '" data-i="' + i + '"><span class="idx">' + (i + 1) + '</span><span class="nm">'
        + esc(sectionTitle(s, i)) + '</span><span class="meta">' + esc((s.figure ? figureLabel(s.figure) + ' · ' : '') + meta) + '</span></li>';
    }).join('');
    renderRehearsalList();
  }

  function renderTransport() {
    $('btn-play').textContent = !E.playing ? '▶ Start' : E.suspended ? '▶ Resume' : 'Pause';
    $('rehearsal-play').textContent = !E.playing ? 'Start' : E.suspended ? 'Resume' : 'Pause';
    $('rehearsal-play').disabled = !sections().length;
    $('btn-quick-edit').disabled = !sections().length;
    $('btn-play').classList.toggle('primary', !E.playing);
    $('btn-stop').disabled = !E.playing;
    renderOutputControls();
    $('btn-export-wav').disabled = E.playing || wavBusy || !$('wav-run').value;
    $('stage').classList.toggle('transport-paused', E.suspended);
    renderRehearsalList();
  }

  function renderRehearsalList() {
    const list = $('rehearsal-list');
    const bars = startBars(sections());
    const active = E.playing && !E.suspended;
    $('rehearsal-instructions').textContent = active
      ? 'Pause playback, then select a section to choose a count-in and restart.'
      : 'Select a section to choose a count-in and restart.';
    list.replaceChildren(...sections().map((section, i) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'rehearsal-point';
      button.dataset.index = i;
      button.disabled = active;
      const location = bars[i] == null ? 'Cue' : 'Bar ' + bars[i];
      button.textContent = (section.figure ? figureLabel(section.figure) + ' · ' : '')
        + location + ' · ' + sectionTitle(section, i);
      if (i === (E.playing ? shown?.secIdx ?? selectedIdx : selectedIdx)) button.setAttribute('aria-current', 'true');
      return button;
    }));
  }

  function renderClock() {
    const elapsed = E.playing ? Math.max(0, ctx.currentTime - clock.start) : 0;
    const planned = !E.playing ? sectionTimeline()[selectedIdx] : null;
    const videoTime = practice?.media && practice.videoTime != null ? practice.videoTime
      : E.playing ? clock.videoStart + elapsed : planned ? planned.start : (current ? current.videoOffset : 0);
    $('d-elapsed').textContent = timecode(elapsed);
    $('d-video-time').textContent = timecode(videoTime);
    $('d-clock-status').textContent = E.suspended ? 'Paused — clock frozen'
      : E.playing ? (shown?.phase === 'intro' ? 'Film intro — clicks have not started'
        : ctx.currentTime < clock.start ? 'Count-in — clock starts with music' : 'Running (cue waits included)')
        : practice?.media ? 'Practice video follows the selected section' : 'Starts from selected section; video time is not linked to a video player';
  }

  function renderBeatVisual() {
    const circles = prefs.beatVisual !== 'pendulum';
    const pendulum = prefs.beatVisual !== 'circles';
    $('d-beats').hidden = !circles;
    $('d-pendulum').hidden = !pendulum;
    $('stage').classList.toggle('show-pendulum', pendulum);
  }

  function renderPendulum() {
    if ($('d-pendulum').hidden) return;
    const swing = shown?.swing;
    if (!E.playing || !swing || !ctx) {
      $('d-pendulum-rod').style.transform = 'rotate(0deg)';
      return;
    }
    const elapsed = clamp((ctx.currentTime - shown.time) / swing.duration, 0, 1);
    const progress = clamp(swing.progress + elapsed * swing.increment, 0, 1);
    const angle = swing.side * 34 * Math.cos(Math.PI * progress);
    $('d-pendulum-rod').style.transform = 'rotate(' + angle.toFixed(2) + 'deg)';
  }

  let flashTimer = null;
  function doFlash() {
    const f = $('flash-layer');
    f.classList.add('on');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => f.classList.remove('on'), 70);
  }

  function frame() {
    if (!CFG.offline && !builder.youtube && !$('view-video').hidden && !$('builder-video').paused) reviewVideoPlayback();
    if (E.playing && !E.suspended && ctx && visQ.length) {
      const now = ctx.currentTime;
      let latest = null, flash = false, ended = false;
      while (visQ.length && visQ[0].time <= now) {
        const ev = visQ.shift();
        if (practice?.media) consumePracticeCue(ev, now);
        if (ev.type === 'visual-hit') continue;
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
    if (practice?.media) {
      syncPracticeVideo();
      renderPracticeCues();
    }
    renderPendulum();
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
    const migrated = Array.isArray(t.sections) && t.sections.some((s) =>
      s && !s.compoundClickDefaulted && +s.den >= 8 && +s.num > 3 && +s.num % 3 === 0 && s.click === 'beats');
    return {
      id: t.id != null ? t.id : newLocalId(),
      title: String(t.title || 'Untitled').slice(0, 255),
      notes: String(t.notes || '').slice(0, 2000),
      videoOffset: clamp(Number(t.videoOffset) || 0, 0, 86400),
      videoDraftId: Number.isInteger(t.videoDraftId) && t.videoDraftId > 0 ? t.videoDraftId : null,
      _linkDirty: !!t._linkDirty,
      sections: Array.isArray(t.sections) ? t.sections.slice(0, 500).map(normSection) : [],
      _dirty: !!t._dirty || migrated, _auto: !!t._auto, _ver: 1,
    };
  }

  const serialise = (t) => ({ id: t.id, title: t.title, notes: t.notes, videoOffset: t.videoOffset, videoDraftId: t.videoDraftId, sections: t.sections, _dirty: t._dirty, _auto: t._auto, _linkDirty: t._linkDirty });
  const cacheLocal = () => saveJSON(LS_TRACKS, tracks.filter((t) => !t._preview).map(serialise));

  async function api(method, path, body, base = CFG.apiBase) {
    const res = await fetch(base + (path || ''), {
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
    if (t._linkDirty || (!isServerId(t.id) && t.videoDraftId)) payload.videoDraftId = t.videoDraftId;
    if (isServerId(t.id)) {
      await api('PUT', '/' + t.id, payload);
    } else {
      const created = await api('POST', '', payload);
      t.id = created.id;
      if (current === t) saveJSON(LS_LAST, t.id);
      refreshTrackSelect();
    }
    if (t._ver === ver) t._dirty = t._linkDirty = false;
    cacheLocal();
  }

  let saveTimer = null, saving = false, saveAgain = false;
  function markDirty(t) {
    if (CFG.offline) return;
    t = t || current;
    if (!t) return;
    if (t._preview) { setStatus('Preview only — changes are not saved'); return; }
    t._dirty = true;
    t._auto = false;
    t._ver = (t._ver || 0) + 1;
    cacheLocal();
    setStatus(CFG.authenticated ? 'Saving…' : 'Saved in this browser');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDirty, 700);
  }

  async function saveDirty() {
    if (saving) { saveAgain = true; return false; }
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
    return !failed;
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
    if (!CFG.offline) refreshBuilderTargets();
  }

  function selectTrack(id) {
    cancelPendingPedal();
    if (E.playing) stop(false);
    stopTester();
    const track = tracks.find((t) => String(t.id) === String(id)) || tracks[0] || null;
    if (!CFG.offline && track !== current) closePracticeVideo();
    if (current?._preview && track !== current) tracks = tracks.filter((t) => t !== current);
    current = track;
    if (current && !current._preview) saveJSON(LS_LAST, current.id);
    if (!CFG.offline) $('preview-notice').hidden = !current?._preview;
    selectedIdx = 0;
    tester.idx = 0;
    tap.times = [];
    beatsKey = '';
    refreshTrackSelect();
    renderAll();
    renderStageTools();
    if (!CFG.offline) renderPracticeAvailability();
    if (!CFG.offline && !$('view-perform').hidden) showPracticeVideo();
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
    download(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), slug(current.title) + '.clicktrack.json');
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  // --------------------------------------------------------------- stage tools
  function renderStageTools() {
    const secs = sections(), bars = startBars(secs);
    const select = $('wav-run'), run = select.value;
    select.replaceChildren();
    for (let i = 0; i < secs.length;) {
      if (secs[i].type === 'pause' || secs[i].advance !== 'auto') { i++; continue; }
      const first = i++;
      while (i < secs.length && secs[i].type === 'click' && secs[i].advance === 'auto') i++;
      const label = (first === i - 1 ? sectionTitle(secs[first], first)
        : sectionTitle(secs[first], first) + ' → ' + sectionTitle(secs[i - 1], i - 1));
      select.add(new Option('b. ' + bars[first] + ' · ' + label, first + ':' + i));
    }
    if (Array.from(select.options).some((o) => o.value === run)) select.value = run;
    if (!select.options.length) select.add(new Option('No automatic sections', ''));
    $('btn-export-wav').disabled = !select.value || wavBusy || E.playing;
    $('btn-print-cues').disabled = $('btn-backup-json').disabled = !current;
    if (!CFG.offline) $('btn-prepare-offline').disabled = !secs.length || offlineBusy;
  }

  function printCues() {
    const bars = startBars(sections()), timeline = sectionTimeline();
    const rows = sections().map((s, i) => {
      const t = timeline[i];
      const timing = (t.startEstimated ? '≈ ' : '') + timecode(t.start) + ' → '
        + (s.type === 'pause' ? 'cue (unknown)' : (t.endEstimated ? '≈ ' : '') + timecode(t.end));
      const cells = [(s.figure ? figureLabel(s.figure) + ' · ' : '') + sectionTitle(s, i), bars[i] == null ? '—' : bars[i] + (s.bars > 1 ? '–' + (bars[i] + s.bars - 1) : ''),
        timing, s.type === 'pause' ? '—' : s.num + '/' + s.den + ' · ' + unitLabel(s) + '=' + s.bpm + (s.bpmEnd == null ? '' : ' → ' + s.bpmEnd),
        s.type === 'pause' ? 'Wait for cue' : (s.advance === 'manual' ? 'Repeat until pedal' : 'Automatic') + (effectiveCountIn(s) ? ' · ' + countInLabel(s) + ' count-in on entry from start/cue' : ''),
        s.notes];
      return '<tr>' + cells.map((cell) => '<td>' + esc(cell) + '</td>').join('') + '</tr>';
    }).join('');
    $('cue-print').innerHTML = '<h1>' + esc(current.title) + ' — cue sheet</h1><p>' + esc(current.notes)
      + '</p><p>Video offset: ' + timecode(current.videoOffset) + '. ≈ indicates estimated timing after a hold/repeat. Planned times exclude count-ins.</p>'
      + '<table><thead><tr><th>Section</th><th>Score bars</th><th>Video time</th><th>Meter / tempo</th><th>Advance</th><th>Notes</th></tr></thead><tbody>'
      + rows + '</tbody></table>';
    window.print();
  }

  let offlineBusy = false;
  async function withTimeout(operation, message, ms = 15000) {
    let timer;
    try {
      return await Promise.race([operation, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), ms);
      })]);
    } finally { clearTimeout(timer); }
  }

  async function prepareOffline() {
    if (offlineBusy || !sections().length) return;
    offlineBusy = true;
    renderStageTools();
    const status = $('offline-prepare-status');
    status.textContent = 'Preparing offline show…';
    const snapshot = JSON.stringify({ prepared: new Date().toISOString(), prefs, track: {
      title: current.title, notes: current.notes, videoOffset: current.videoOffset, sections: current.sections,
    } });
    try {
      if (!navigator.serviceWorker || !window.caches) throw new Error('Offline storage needs HTTPS and a supported browser.');
      const response = await withTimeout(fetch(CFG.offlinePage, { cache: 'no-store', credentials: 'omit' }),
        'The offline page did not respond. Check your connection and try again.');
      if (!response.ok || response.headers.get('X-Clicktrack-Offline-Shell') !== '1') throw new Error('The offline page was not returned by the site.');
      const registration = await withTimeout(navigator.serviceWorker.register(CFG.offlineWorker, { scope: CFG.offlinePage }),
        'The browser did not register the offline worker. Try again in a normal browser tab.');
      if (registration.active?.state !== 'activated') {
        const worker = registration.installing || registration.waiting || registration.active;
        if (!worker) throw new Error('The browser did not install the offline worker.');
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => finish('Offline worker did not activate. Try again.'), 15000);
          function finish(error) {
            clearTimeout(timer);
            worker.removeEventListener('statechange', check);
            if (error) reject(new Error(error)); else resolve();
          }
          function check() {
            if (worker.state === 'activated') finish();
            if (worker.state === 'redundant') finish('Offline worker installation failed.');
          }
          worker.addEventListener('statechange', check);
          check();
        });
      }
      const cache = await caches.open(OFFLINE_CACHE);
      await cache.put(CFG.offlinePage, response);
      localStorage.setItem(OFFLINE_KEY, snapshot);
      $('open-offline').hidden = false;
      status.textContent = 'Offline show prepared. Open it, bookmark it, then test in airplane mode.';
    } catch (e) {
      status.textContent = 'Could not prepare offline show: ' + e.message;
    } finally {
      offlineBusy = false;
      renderStageTools();
    }
  }

  let wavBusy = false;
  async function exportWav() {
    if (E.playing || wavBusy || !$('wav-run').value) return;
    const [first, end] = $('wav-run').value.split(':').map(Number);
    const run = sections().slice(first, end), title = current.title;
    const status = $('wav-status'), Audio = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const sound = prefs.sound, volume = prefs.volume;
    wavBusy = true;
    renderStageTools();
    status.textContent = 'Rendering click WAV…';
    try {
      if (!Audio) throw new Error('This browser does not support offline audio rendering.');
      if (volume === 0) throw new Error('Raise the app volume before exporting a click WAV.');
      const rate = 22050, tail = 0.13;
      const duration = run.reduce((sum, s) => sum + sectionSeconds(s), 0)
        + countInSeconds(run[0], effectiveCountIn(run[0]));
      if (duration > 1800) throw new Error('This run exceeds the 30-minute WAV limit.');
      const frames = Math.ceil((duration + tail) * rate);
      const data = new ArrayBuffer(44 + frames * 2), wav = new DataView(data);
      for (const [at, text] of [[0, 'RIFF'], [8, 'WAVE'], [12, 'fmt '], [36, 'data']]) {
        for (let i = 0; i < text.length; i++) wav.setUint8(at + i, text.charCodeAt(i));
      }
      for (const [at, value] of [[4, data.byteLength - 8], [16, 16], [24, rate], [28, rate * 2], [40, frames * 2]]) wav.setUint32(at, value, true);
      for (const [at, value] of [[20, 1], [22, 1], [32, 2], [34, 16]]) wav.setUint16(at, value, true);
      const voices = new Map();
      async function voice(level, s) {
        const pitch = level === 0 ? s.subPitch : level === 1 ? s.unitPitch : 0;
        const velocity = level === 0 ? s.subVelocity : level === 1 ? s.unitVelocity : 127;
        const key = [level, pitch, velocity].join('/');
        if (!voices.has(key)) {
          const ctx = new Audio(1, Math.ceil(tail * rate), rate), master = ctx.createGain();
          master.gain.value = volume;
          master.connect(ctx.destination);
          const noiseBuf = ctx.createBuffer(1, Math.ceil(0.13 * rate), rate);
          const noise = noiseBuf.getChannelData(0);
          for (let i = 0; i < noise.length; i++) noise[i] = Math.random() * 2 - 1;
          playClick(0, level, s, { ctx, master, noiseBuf, sound });
          voices.set(key, (await ctx.startRendering()).getChannelData(0));
        }
        return voices.get(key);
      }
      let time = 0, barsRendered = 0;
      for (const [i, s] of run.entries()) {
        const levels = unitLevels(s), countIn = i === 0 ? effectiveCountIn(s) : 0;
        for (let bar = -countIn; bar < s.bars; bar++) {
          const phase = bar < 0 ? 'countin' : 'play', subN = bar < 0 ? 1 : subdivOf(s);
          for (let tick = 0; tick < s.num; tick++) {
            for (let sub = 0; sub < subN; sub++) {
              const level = sub === 0 ? levels[tick] : 0;
              if (audible(s, level, phase)) {
                const sample = await voice(level, s), offset = 44 + 2 * Math.round(time * rate);
                for (let n = 0, at = offset; n < sample.length && at < data.byteLength; n++, at += 2) {
                  wav.setInt16(at, clamp(wav.getInt16(at, true) + Math.round(sample[n] * 32767), -32768, 32767), true);
                }
              }
              const bpm = bar < 0 ? s.bpm : bpmAt(s, (bar + (tick + sub / subN) / s.num) / s.bars);
              time += unitSeconds(s, bpm) / subN;
            }
          }
          if (++barsRendered % 32 === 0) await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
      download(new Blob([data], { type: 'audio/wav' }), slug(title) + '-sections-' + (first + 1) + '-' + end + '.wav');
      status.textContent = 'WAV ready · ' + timecode(duration) + ' including count-in.';
    } catch (e) {
      status.textContent = 'Could not render WAV: ' + e.message;
    } finally {
      wavBusy = false;
      renderStageTools();
    }
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
    if (outputBusy) { toast('Finish choosing an audio output before starting.'); return; }
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
      + btn('dup', '⧉', 'Duplicate') + btn('play', '▶ Perform', 'Perform from here') + btn('del', '✕', 'Delete') + '</div>';
    let f = fld('Type', sel(i, 'type', [['click', 'Click'], ['pause', 'Pause / cue']], s.type))
      + fld('Rehearsal figure', inp(i, 'figure', s.figure, 'type="text" maxlength="32" placeholder="e.g. 6 or A"'))
      + fld('Streamer and punch at start', '<input type="checkbox" data-f="visualCue" data-i="' + i + '"' + (s.visualCue ? ' checked' : '') + '>');
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
        + fld('Count-in bars', inp(i, 'countIn', s.countIn, 'type="number" min="0" max="8" inputmode="numeric" placeholder="auto (2 seconds)"'))
        + fld('Score bar', inp(i, 'startBar', s.startBar, 'type="number" min="1" max="99999" inputmode="numeric" placeholder="auto"'))
        + fld('Streamer and punch at automatic end', '<input type="checkbox" data-f="visualEndCue" data-i="' + i + '"' + (s.visualEndCue ? ' checked' : '') + '>')
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

  const firstClickSection = (track) => track?.sections.find((s) => s.type === 'click');

  function renderStartingCountIn() {
    const section = firstClickSection(current), input = $('track-count-in');
    input.disabled = !section;
    input.value = section?.countIn ?? '';
  }

  let quickEditIndex = null;
  function renderSections() {
    renderStartingCountIn();
    const secs = sections();
    $('quick-edit-section').replaceChildren();
    $('sections').innerHTML = secs.length
      ? secs.map((s, i) => sectionRow(s, i, secs.length)).join('')
      : '<p class="muted">No sections yet. Add one below.</p>';
    if ($('quick-edit-dialog').open && quickEditIndex != null) {
      const row = $('sections').querySelector('[data-row="' + quickEditIndex + '"]');
      if (row) $('quick-edit-section').append(row);
    }
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

  const RAW_FIELDS = ['name', 'notes', 'grouping', 'calcStart', 'calcEnd', 'figure'];
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
    const n = normSection(Object.assign({}, s, { [f]: el.type === 'checkbox' ? el.checked : el.value }));
    if ((f === 'num' || f === 'den') && !isCompound(s) && isCompound(n) && s.click === 'beats') {
      n.click = 'units';
      const click = el.closest('[data-row]').querySelector('[data-f="click"]');
      if (click) click.value = n.click;
    }
    current.sections[i] = n;
    if (f === 'countIn' && n === firstClickSection(current)) renderStartingCountIn();
    if (['bpm', 'bpmEnd', 'num', 'den', 'bars', 'beatUnit', 'calcStart', 'calcEnd'].includes(f)) {
      $('calc-result-' + i).textContent = '';
    }
    if (e.type === 'change' && el.tagName === 'INPUT' && el.type !== 'checkbox' && !RAW_FIELDS.includes(f)) el.value = n[f] == null ? '' : n[f];
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
    else if (act === 'dup') secs.splice(i + 1, 0, normSection(Object.assign({}, secs[i], { startBar: null, figure: '', visualCue: false, visualEndCue: false })));
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
      : normSection(prev ? Object.assign({}, prev, { name: '', notes: '', startBar: null, figure: '', countIn: null, bpmEnd: null, calcStart: '', calcEnd: '', visualCue: false, visualEndCue: false }) : {});
    secs.push(s);
    markDirty(); renderSections(); renderSetlist(); renderIdle();
    const row = document.querySelector('[data-row="' + (secs.length - 1) + '"]');
    if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); const nm = row.querySelector('[data-f="name"]'); if (nm) nm.focus({ preventScroll: true }); }
  }

  // ------------------------------------------------------------ views & UI
  const builderMarker = (time, previous) => ({
    time, label: time === 0 ? '0' : '', bar: '', skip: '0', num: previous?.num ?? '4', den: previous?.den ?? '4',
    unit: previous?.unit ?? 'auto', shape: 'steady', bpm: '', dialogue: '', figure: '', visualCue: false,
  });
  const builder = {
    url: null, markers: [builderMarker(0)], selected: 0, dirty: false, zoomStart: null,
    youtube: null, draftId: null, draftsBusy: false,
    reviewTime: null, reviewAnchor: null, returnTrack: null, localTrack: null, localSnapshot: null,
  };
  const markerFields = ['bar', 'num', 'den', 'unit', 'shape', 'bpm', 'dialogue', 'figure'];

  function nextMarkerNumber(used, start) {
    let number = start;
    while (number > 999999999 || used.has(String(number))) {
      number = number >= 999999999 ? 1 : number + 1;
    }
    return String(number);
  }

  function numberDraftMarkers(markers) {
    const used = new Set(markers.map((m) => m.label).filter(Boolean));
    return markers.map((m, i) => {
      const label = m.label || nextMarkerNumber(used, i);
      used.add(label);
      return { ...m, label };
    });
  }

  function newMarkerLabel(index) {
    const used = new Set(builder.markers.map((m) => m.label));
    const base = builder.markers[index - 1].label.match(/^\d+/)[0];
    if (index === builder.markers.length) {
      return nextMarkerNumber(used, Number(base) + 1);
    }
    // At most 502 markers, so two suffix letters suffice.
    for (let i = 0; ; i++) {
      const letter = (n) => String.fromCharCode(65 + n);
      const label = base + (i < 26 ? '' : letter(Math.floor(i / 26) - 1)) + letter(i % 26);
      if (!used.has(label)) return label;
    }
  }

  function planMarkerRenumber(markers, selected, label, ripple) {
    const numberOf = (value) => Number(value.match(/^\d+/)[0]);
    const shift = numberOf(label) - numberOf(markers[selected].label);
    const labels = markers.map((marker, index) => {
      if (index === selected) return label;
      if (!ripple || !shift || index < selected) return marker.label;
      const [, digits, suffix] = marker.label.match(/^(\d+)([A-Z]*)$/);
      const number = Number(digits) + shift;
      if (number < 1 || number > 999999999) {
        throw new Error('Renumbering would put a following hit point outside the range 1 to 999999999.');
      }
      return String(number).padStart(digits.startsWith('0') ? digits.length : 0, '0') + suffix;
    });
    const used = new Set();
    for (const value of labels) {
      if (used.has(value)) throw new Error('Hit point number ' + value + ' would be used twice. No numbers were changed.');
      used.add(value);
    }
    return labels;
  }

  function youtubeId(value) {
    let url;
    try { url = new URL(value.trim()); } catch (e) { return null; }
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.port) return null;
    const path = url.pathname.split('/').filter(Boolean);
    let id;
    if (url.hostname === 'youtu.be' && path.length === 1) id = path[0];
    if (['www.youtube.com', 'youtube.com', 'm.youtube.com', 'www.youtube-nocookie.com'].includes(url.hostname)) {
      if (url.pathname === '/watch') id = url.searchParams.get('v');
      else if (path.length === 2 && ['embed', 'shorts', 'live'].includes(path[0])) id = path[1];
    }
    return typeof id === 'string' && /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
  }

  let youtubeApi;
  function youtubeFrame(host, id, title) {
    const frame = document.createElement('iframe');
    frame.title = title;
    frame.allow = 'autoplay; encrypted-media; fullscreen; picture-in-picture';
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    const params = new URLSearchParams({ enablejsapi: '1', playsinline: '1', origin: window.location.origin });
    frame.src = 'https://www.youtube-nocookie.com/embed/' + id + '?' + params;
    host.replaceChildren(frame);
    return frame;
  }

  function loadYoutubeApi() {
    if (window.YT?.Player) return Promise.resolve(window.YT);
    if (!youtubeApi) {
      youtubeApi = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://www.youtube.com/iframe_api';
        const timeout = setTimeout(() => fail(), 12000);
        const fail = () => {
          clearTimeout(timeout);
          script.remove();
          reject(new Error('YouTube did not load. Check your connection or content blocker and try again.'));
        };
        script.addEventListener('error', fail, { once: true });
        window.onYouTubeIframeAPIReady = () => { clearTimeout(timeout); resolve(window.YT); };
        document.head.append(script);
      }).catch((e) => { youtubeApi = null; throw e; });
    }
    return youtubeApi;
  }

  function builderMedia() {
    const source = builder.youtube;
    if (!source) return $('builder-video');
    const player = source.ready ? source.player : null;
    return {
      get readyState() { return player ? 1 : 0; },
      get duration() { return player?.getDuration() || 0; },
      get currentTime() { return player?.getCurrentTime() || 0; },
      set currentTime(time) { player?.seekTo(time, true); },
      get paused() { return ![1, 3].includes(player?.getPlayerState()); },
      get ended() { return player?.getPlayerState() === 0; },
      error: source.error,
      pause: () => player?.pauseVideo(),
      play: () => player?.playVideo(),
    };
  }

  function resetBuilderSource() {
    const video = $('builder-video'), source = builder.youtube;
    builder.youtube = null;
    if (source) {
      clearInterval(source.timer);
      clearTimeout(source.timeout);
      source.player?.destroy();
    }
    $('builder-youtube').replaceChildren();
    $('builder-youtube').hidden = true;
    video.hidden = false;
    video.pause();
    if (builder.url) {
      video.removeAttribute('src');
      video.load();
      URL.revokeObjectURL(builder.url);
      builder.url = null;
    }
    builder.draftId = null;
    builder.localTrack = null;
    builder.localSnapshot = null;
    builder.zoomStart = null;
    builder.dirty = false;
    $('builder-count-in').value = '';
    resetVideoReview();
  }

  async function openYoutube(id, draft) {
    resetBuilderSource();
    const source = { id, ready: false, error: false };
    builder.youtube = source;
    builder.draftId = draft?.id ?? null;
    builder.markers = draft ? numberDraftMarkers(draft.markers) : [builderMarker(0)];
    $('builder-title').value = draft?.title ?? 'YouTube video ' + id;
    $('builder-youtube-url').value = 'https://youtu.be/' + id;
    $('builder-video').hidden = true;
    const host = $('builder-youtube'), status = $('builder-media-status');
    host.hidden = false;
    status.textContent = 'Loading YouTube…';
    selectVideoMarker(0);
    setBuilderMediaControls();
    renderVideoPlan();
    renderDraftControls();
    const fail = (message) => {
      if (builder.youtube !== source) return;
      source.error = true;
      clearInterval(source.timer);
      clearTimeout(source.timeout);
      status.textContent = message;
      setBuilderMediaControls();
      renderVideoTimeline();
      renderVideoPlan();
    };
    const refresh = () => {
      if (builder.youtube !== source || source.error || !source.ready) return;
      const media = builderMedia(), duration = media.duration;
      const durationChanged = source.duration !== duration;
      if (source.paused !== media.paused || source.ended !== media.ended || durationChanged) {
        source.paused = media.paused;
        source.ended = media.ended;
        source.duration = duration;
        setBuilderMediaControls();
      }
      if (durationChanged) {
        status.textContent = videoReady() ? 'Video duration: ' + markerTime(duration) : 'Press Play video to load the YouTube timeline.';
        renderVideoTimeline();
        renderVideoPlan();
      } else updateVideoPlayhead();
      if (videoReady() && source.pendingSeek) {
        const pending = source.pendingSeek;
        source.pendingSeek = null;
        if (pending.markerIndex === null || pending.markerIndex === builder.selected) seekBuilderVideo(pending.time);
      }
      reviewVideoPlayback();
    };
    try {
      const yt = await loadYoutubeApi();
      if (builder.youtube !== source) return;
      const frame = youtubeFrame(host, id, 'YouTube source video');
      source.timeout = setTimeout(() => fail('The YouTube player did not respond. Reload the video or try a local file.'), 15000);
      source.player = new yt.Player(frame, {
        events: {
          onReady: (event) => {
            if (builder.youtube !== source || source.error) return;
            clearTimeout(source.timeout);
            source.player = event.target;
            source.ready = true;
            status.textContent = 'Press Play video to load the YouTube timeline.';
            refresh();
            source.timer = setInterval(refresh, 150);
          },
          onStateChange: () => {
            if (builder.youtube === source && $('view-video').hidden && !builderMedia().paused) builderMedia().pause();
            refresh();
          },
          onError: (event) => fail('YouTube cannot play this video here (error ' + event.data + '). Try a link that allows embedding or a local file.'),
        },
      });
    } catch (e) { fail(e.message); }
  }

  let practice = null;

  function clearPracticeCues() {
    cueRoute++;
    if (!practice) return;
    practice.streamer = null;
    practice.openingStreamer = null;
    practice.punchUntil = 0;
  }

  function consumePracticeCue(event, now) {
    if (event.punch && now - event.time < 0.125) {
      practice.punchUntil = performance.now() + 125;
    }
    if (event.streamerEnd != null && event.cueRoute === cueRoute && now < event.streamerEnd) {
      practice.streamer = { start: event.time, end: event.streamerEnd };
    }
  }

  function renderPracticeCues() {
    const enabled = $('practice-show-cues').checked;
    const now = ctx?.currentTime ?? 0;
    const opening = practice.openingStreamer;
    const sweep = opening && now >= opening.start && now < opening.end ? opening : practice.streamer;
    const bar = $('practice-streamer');
    bar.hidden = !enabled || !E.playing || !sweep || now >= sweep.end - 1e-8;
    if (!bar.hidden) bar.style.left = (100 * clamp((now - sweep.start) / (sweep.end - sweep.start), 0, 1)) + '%';
    $('practice-punch').hidden = !enabled || !(performance.now() < practice.punchUntil);
    $('practice-visual-cues').hidden = !enabled;
  }

  function hasPracticeVideo() {
    return !!(current?._previewVideoId || current?.videoDraftId
      || ((builder.localTrack === current || current?._previewLocal) && builder.url));
  }

  function renderPracticeAvailability() {
    const available = hasPracticeVideo();
    $('practice-no-video').hidden = available;
    $('practice-controls').hidden = !available;
    $('btn-practice-edit-video').disabled = !current || builder.draftsBusy;
  }

  function closePracticeVideo() {
    const source = practice;
    practice = null;
    if (source) {
      clearTimeout(source.timeout);
      source.media?.pause();
      source.player?.destroy();
      $('practice-player').replaceChildren();
    }
    $('practice-video').hidden = true;
    $('perform-layout').classList.remove('with-video');
    $('practice-visual-cues').hidden = true;
    $('practice-resync').disabled = true;
    $('practice-from-start').disabled = true;
  }

  function showPracticeVideo() {
    if (!videoEnabled || practice || !current) return;
    const available = hasPracticeVideo();
    renderPracticeAvailability();
    $('practice-video').hidden = false;
    $('perform-layout').classList.toggle('with-video', available);
    if (available) openPracticeVideo();
  }

  function syncPracticeVideo(force = false) {
    const source = practice;
    if (!source?.media) return;
    if (!force && !E.playing && performance.now() < source.punchUntil) {
      if (!source.holdingEnd) source.media.pause();
      source.holdingEnd = true;
      return;
    }
    source.holdingEnd = false;
    const idx = E.playing ? (shown?.secIdx ?? selectedIdx) : selectedIdx;
    const phase = E.playing ? (shown?.phase ?? 'waiting') : 'stopped';
    const rolling = phase === 'play' || phase === 'countin' || phase === 'intro';
    const section = sections()[idx];
    if (!section) return;
    const entry = E.playing ? shown?.phaseStart : null;
    const positionKey = idx + ':' + phase + ':' + entry;
    const entering = source.positionKey !== positionKey;
    const previousBoundary = source.rollUntil;
    if (entering || force) {
      source.positionKey = positionKey;
      let start = sectionTimeline()[idx].start;
      if (rolling && shown?.startBarOffset) {
        for (let bar = 0; bar < shown.startBarOffset; bar++) start += barSeconds(section, bar);
      }
      const preRoll = phase === 'countin'
        ? shown.filmCountIn ? clock.start - entry
          : countInSeconds({ ...section, bpm: bpmAt(section, (shown.startBarOffset ?? 0) / section.bars) }, shown.countIn) : 0;
      source.cueTime = phase === 'intro' ? 0 : start - preRoll;
      source.rollUntil = phase === 'intro' ? shown.introEnd
        : phase === 'countin' ? entry + preRoll : null;
    }
    const expected = source.cueTime + (rolling ? Math.max(0, ctx.currentTime - entry) : 0);
    const playing = rolling && !E.suspended && expected >= -1e-8;
    source.videoTime = Math.max(0, expected);
    const key = positionKey + ':' + playing;
    if (!force && key === source.syncKey) return;
    source.syncKey = key;
    // Avoid a seek at a scheduled intro/count-in handoff.
    const continuous = entering && previousBoundary != null && source.sectionIdx === idx
      && playing && source.playing && Math.abs(entry - previousBoundary) < 1e-8;
    source.sectionIdx = idx;
    source.playing = playing;
    if (continuous && !force) return;
    if (phase !== 'waiting' && (entering || playing || force)) source.media.seek(source.videoTime);
    if (playing) source.media.play();
    else source.media.pause();
  }

  async function openPracticeVideo() {
    const track = current, host = $('practice-player'), status = $('practice-status');
    const source = { syncKey: null };
    practice = source;
    $('practice-sound').checked = false;
    status.textContent = 'Loading practice video…';
    const fail = (message) => {
      if (practice !== source) return;
      clearTimeout(source.timeout);
      status.textContent = message;
    };
    const ready = (media) => {
      if (practice !== source) return;
      clearTimeout(source.timeout);
      source.media = media;
      media.mute(!$('practice-sound').checked);
      $('practice-resync').disabled = false;
      $('practice-from-start').disabled = !sections().length;
      status.textContent = 'Ready. Use the click track’s Start / Pause controls.';
      syncPracticeVideo(true);
      renderPracticeCues();
    };
    const blocked = () => fail('Video playback was blocked. Tap Resync to clicks or use the player to allow playback.');
    try {
      const local = !track.videoDraftId && (builder.localTrack === track || track._previewLocal) && builder.url;
      $('practice-picture').classList.toggle('local-video', !!local);
      if (local) {
        const video = document.createElement('video');
        video.playsInline = true;
        video.preload = 'metadata';
        video.setAttribute('aria-label', 'Practice video');
        video.addEventListener('loadedmetadata', () => ready({
          seek: (time) => { video.currentTime = Math.min(time, video.duration); },
          play: () => { video.play().catch((e) => { if (e.name !== 'AbortError') fail(e.message); }); },
          pause: () => video.pause(),
          mute: (muted) => { video.muted = muted; },
        }), { once: true });
        video.addEventListener('error', () => fail('Could not play the local practice video.'), { once: true });
        video.src = builder.url;
        host.replaceChildren(video);
        return;
      }
      if (!track.videoDraftId && !track._previewVideoId) throw new Error('No video is linked to this track.');
      const draft = track._previewVideoId ? { videoId: track._previewVideoId } : await draftRequest('GET', track.videoDraftId);
      if (practice !== source) return;
      const yt = await loadYoutubeApi();
      if (practice !== source) return;
      const frame = youtubeFrame(host, draft.videoId, 'YouTube practice video');
      source.timeout = setTimeout(() => fail('The YouTube player did not respond. Close and reopen Practice with video to retry.'), 15000);
      source.player = new yt.Player(frame, {
        events: {
          onReady: ({ target }) => ready({
            seek: (time) => target.seekTo(time, true),
            play: () => target.playVideo(),
            pause: () => target.pauseVideo(),
            mute: (muted) => muted ? target.mute() : target.unMute(),
          }),
          onAutoplayBlocked: blocked,
          onError: ({ data }) => fail('YouTube could not play this video here (error ' + data + ').'),
        },
      });
    } catch (e) { fail('Could not load the practice video: ' + e.message); }
  }

  function renderDraftControls() {
    const busy = builder.draftsBusy, selected = $('builder-drafts').value;
    const track = builder.returnTrack;
    $('builder-open-draft').disabled = $('builder-delete-draft').disabled = busy || !selected;
    $('builder-save-draft').disabled = busy || !builder.youtube;
    $('builder-load-youtube').disabled = $('builder-file').disabled = busy;
    $('btn-edit-video').disabled = busy;
    $('btn-practice-edit-video').disabled = !current || busy;
    $('builder-create').disabled = busy || !videoReady() || !builder.planValid
      || (!$('builder-skip').disabled && !$('builder-skip').validity.valid);
    $('builder-target-track').disabled = busy;
    $('builder-overwrite').disabled = $('builder-create').disabled || !builderTargetTrack();
    $('builder-back-to-edit').hidden = !track;
    $('builder-perform').hidden = !track;
    $('builder-perform').disabled = busy;
    $('builder-link-draft').hidden = !track || !builder.draftId || track.videoDraftId === builder.draftId;
    $('builder-link-draft').disabled = busy;
  }

  function builderTargetTrack() {
    const id = $('builder-target-track').value;
    return tracks.find((t) => !t._auto && String(t.id) === id);
  }

  function refreshBuilderTargets(preferred) {
    const list = $('builder-target-track');
    const selected = preferred === undefined ? list.value : String(preferred ?? '');
    const eligible = tracks.filter((t) => !t._auto).sort((a, b) => a.title.localeCompare(b.title));
    list.replaceChildren(new Option('Choose a track', ''), ...eligible.map((t) => new Option(t.title, t.id)));
    if (eligible.some((t) => String(t.id) === selected)) list.value = selected;
    renderDraftControls();
  }

  const draftRequest = (method, id, body) => api(method, id ? '/' + id : '', body, CFG.videoDrafts);
  async function refreshDraftList() {
    const list = $('builder-drafts'), selected = builder.draftId ?? list.value;
    const { drafts } = await draftRequest('GET');
    list.replaceChildren(new Option('Choose a draft', ''), ...drafts.map((d) => new Option(d.title, d.id)));
    if (Array.from(list.options).some((o) => o.value === String(selected))) list.value = selected;
    renderDraftControls();
  }

  const confirmBuilderReplace = () => !builder.dirty || window.confirm('Discard unsaved changes to this video draft?');
  const draftPayload = () => ({
    videoId: builder.youtube.id, title: $('builder-title').value.trim(), markers: builder.markers,
  });

  async function saveYoutubeDraft() {
    const payload = JSON.stringify(draftPayload());
    const saved = await draftRequest(builder.draftId ? 'PUT' : 'POST', builder.draftId, JSON.parse(payload));
    builder.draftId = saved.id;
    const unchanged = payload === JSON.stringify(draftPayload());
    if (unchanged) builder.dirty = false;
    return unchanged;
  }

  async function runDraftAction(action) {
    if (builder.draftsBusy) return;
    builder.draftsBusy = true;
    renderDraftControls();
    const status = $('builder-draft-status');
    status.textContent = 'Working…';
    try { status.textContent = await action(); }
    catch (e) { status.textContent = 'Could not complete the draft action: ' + e.message; }
    finally { builder.draftsBusy = false; renderDraftControls(); }
  }

  function markerTime(seconds) {
    const ms = Math.round(seconds * 1000), whole = Math.floor(ms / 1000);
    return Math.floor(whole / 60) + ':' + String(whole % 60).padStart(2, '0')
      + '.' + String(ms % 1000).padStart(3, '0');
  }

  function fitRamp(s, duration, direction) {
    const steps = s.bars * s.num * subdivOf(s);
    if (steps < 2) throw new Error('A ramp needs at least two timed notes.');
    const scale = unitSeconds(s, 1) / subdivOf(s);
    // Sum the same musical steps that playback schedules, not the arithmetic mean of the endpoints.
    const at = (end) => {
      let total = 0;
      for (let i = 0; i < steps; i++) total += 1 / (s.bpm + (end - s.bpm) * i / steps);
      return total * scale;
    };
    const start = Math.round(s.bpm * 100);
    const min = direction === 'accel' ? start + 1 : 1000;
    const max = direction === 'accel' ? 40000 : start - 1;
    const tolerance = 0.0005;
    if (min > max || duration > at(min / 100) + tolerance || duration < at(max / 100) - tolerance) {
      throw new Error('This ' + (direction === 'accel' ? 'accelerando' : 'rallentando')
        + ' cannot reach the next hit point from ' + s.bpm + ' BPM within 10–400 BPM. Check the starting tempo, markers or tempo shape.');
    }
    let lo = min, hi = max;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (at(mid / 100) > duration) lo = mid + 1; else hi = mid;
    }
    if (lo > min && Math.abs(at((lo - 1) / 100) - duration) < Math.abs(at(lo / 100) - duration)) lo--;
    return lo / 100;
  }

  function planVideoTrack() {
    const points = builder.markers.filter((m, i) => i !== 0 || m.bar !== '');
    if (points.length < 2) throw new Error('Add a musical hit point and a closing hit point with the next score bar.');
    if (points.length > 501) throw new Error('A track can have at most 500 sections.');
    if (videoReady() && points[points.length - 1].time > roundMarkerTime(builderMedia().duration)) {
      throw new Error('A hit point is beyond the end of the video. Move it before creating the track.');
    }
    if (points[0].time > 86400) throw new Error('The first musical hit point must be within the first 24 hours.');
    const secs = [];
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1], startBar = Number(a.bar), endBar = Number(b.bar);
      const skip = b.skip === '' || b.skip == null ? 0 : Number(b.skip);
      const bars = endBar - startBar - skip, numerator = Number(a.num), duration = b.time - a.time;
      try {
        if (!Number.isInteger(skip) || skip < 0 || skip > 9998) throw new Error('Enter skipped score bars as a whole number from 0 to 9998.');
        if (!Number.isInteger(startBar) || startBar < 1 || !Number.isInteger(endBar) || endBar > 99999
          || bars < 1 || bars > 9999) throw new Error('Score bars and skipped bars must leave 1–9999 played bars between hit points.');
        if (!(duration > 0)) throw new Error('The next hit point must have a later video time.');
        if (!Number.isInteger(numerator) || numerator < 1 || numerator > 32) throw new Error('Enter a meter numerator from 1 to 32.');
        const s = defaultSection({
          name: 'Bar ' + startBar, notes: a.dialogue, figure: (a.figure || '').trim(), startBar, bars, num: numerator,
          visualCue: a.visualCue === true, visualEndCue: i === points.length - 2 && b.visualCue === true,
          den: Number(a.den), beatUnit: a.unit, calcStart: markerTime(a.time), calcEnd: markerTime(b.time),
        });
        const average = bars * numerator * unitSeconds(s, 60) * 60 / duration;
        if (a.shape === 'steady') {
          if (average < 10 || average > 400) throw new Error('The marked duration requires ' + average.toFixed(2) + ' BPM, outside the 10–400 BPM range.');
          s.bpm = Math.round(average * 100) / 100;
        } else {
          let start = a.bpm === '' ? null : Number(a.bpm);
          const previous = secs[i - 1]?.section;
          if (start == null && previous) {
            const from = beatUnitFrac(previous), to = beatUnitFrac(s);
            start = (previous.bpmEnd ?? previous.bpm) * (from.n / from.d) / (to.n / to.d);
          }
          if (start == null) throw new Error('Enter a starting BPM for the first ramp.');
          if (!Number.isFinite(start) || start < 10 || start > 400) throw new Error('Starting BPM must be between 10 and 400.');
          s.bpm = Math.round(start * 100) / 100;
          s.bpmEnd = fitRamp(s, duration, a.shape);
        }
        secs.push({ section: s, average, hitPoint: a.label, endHitPoint: b.label, skip });
      } catch (e) {
        throw new Error('Hit point at ' + markerTime(a.time) + ': ' + e.message);
      }
    }
    return { offset: points[0].time, secs };
  }

  function renderVideoPlan() {
    const preview = $('builder-preview'), result = $('builder-result');
    preview.replaceChildren();
    builder.planValid = false;
    try {
      const plan = planVideoTrack();
      for (const { section: s, average, hitPoint, endHitPoint, skip } of plan.secs) {
        const li = document.createElement('li');
        li.textContent = 'Hit point ' + hitPoint + ' → ' + endHitPoint + ' · '
          + (s.figure ? figureLabel(s.figure) + ' · ' : '') + s.name + ' · ' + s.calcStart + ' → ' + s.calcEnd + ' · ' + s.bars + ' bars of '
          + s.num + '/' + s.den + ' · ' + unitLabel(s) + '=' + s.bpm
          + (s.bpmEnd == null ? '' : ' → ' + s.bpmEnd + ' (average ' + average.toFixed(2) + ' BPM)')
          + (skip ? ' · ' + skip + ' score bar' + (skip === 1 ? '' : 's') + ' skipped before next hit' : '')
          + (s.visualCue ? ' · Visual cue at start' : '') + (s.visualEndCue ? ' · Visual cue at end' : '')
          + (s.notes ? ' · ' + s.notes : '');
        preview.append(li);
      }
      result.textContent = plan.secs.length + ' section(s) · video offset ' + markerTime(plan.offset);
      builder.planValid = true;
    } catch (e) {
      result.textContent = e.message;
    }
    renderDraftControls();
  }

  function renderVideoMarkers() {
    const list = $('builder-markers');
    list.replaceChildren(...builder.markers.map((m, i) => new Option(m.label + ' · ' + markerTime(m.time)
      + (m.figure?.trim() ? ' · ' + figureLabel(m.figure.trim()) : '')
      + (m.visualCue ? ' · Visual cue' : '')
      + (Number(nextMusicalMarker(i)?.skip) ? ' · Skip ' + nextMusicalMarker(i).skip + ' score bars before next hit' : '')
      + (m.bar ? ' · Bar ' + m.bar : i === 0 ? ' · Video start' : ' · Add bar number')
      + (i > 0 && i === builder.markers.length - 1 ? ' · Current closing point' : ''), i)));
    list.value = builder.selected;
  }

  const markerHeading = (marker, time = marker.time) => 'Hit point ' + marker.label + ' at ' + markerTime(time);

  // Saved drafts store each span's skip count on its closing hit point.
  function nextMusicalMarker(index) {
    if (!builder.markers[index]?.bar) return null;
    const next = builder.markers[index + 1];
    return next?.bar ? next : null;
  }

  function renderSelectedSkip() {
    const next = nextMusicalMarker(builder.selected), input = $('builder-skip');
    input.disabled = !next;
    input.value = next?.skip ?? '0';
    $('builder-skip-help').textContent = next
      ? 'Applies to this span, before hit point ' + next.label + ' (bar ' + next.bar + ').'
      : 'Add a score bar here and a following score-bar hit point to set skips for this span.';
  }

  function renderSelectedMarkerHeading() {
    const marker = builder.markers[builder.selected];
    $('builder-marker-title').textContent = markerHeading(marker);
    $('builder-marker-label').value = marker.label;
  }

  function selectVideoMarker(index, seek = false, recenter = true) {
    builder.selected = index;
    const marker = builder.markers[index];
    renderVideoMarkers();
    renderSelectedMarkerHeading();
    $('builder-time').value = markerTime(marker.time);
    $('builder-time').readOnly = index === 0;
    $('builder-remove').disabled = index === 0;
    $('builder-retime').disabled = index === 0 || !videoReady();
    for (const field of markerFields) $('builder-' + field).value = marker[field] ?? '';
    renderSelectedSkip();
    $('builder-visual-cue').checked = marker.visualCue === true;
    $('builder-bpm-field').hidden = marker.shape === 'steady';
    if (recenter && builder.zoomStart !== null && videoReady()) zoomAroundMarker();
    if (seek && videoReady()) seekBuilderVideo(marker.time);
    renderVideoTimeline();
  }

  const roundMarkerTime = (time) => Math.round(time * 1000) / 1000;

  function videoReady() {
    const video = builderMedia();
    return video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 0 && !video.error;
  }

  function seekBuilderVideo(time) {
    const video = builderMedia();
    const target = clamp(time, 0, video.duration);
    resetVideoReview(builder.youtube ? target : null);
    video.pause();
    video.currentTime = target;
    updateVideoPlayhead();
  }

  function resetVideoReview(anchor = null) {
    builder.reviewTime = null;
    builder.reviewAnchor = anchor;
    $('builder-review-status').textContent = '';
  }

  function reviewVideoPlayback() {
    if ($('view-video').hidden || !$('builder-review').checked || !videoReady()) return;
    const media = builderMedia();
    if (media.seeking || (media.paused && !media.ended)) return;
    const time = media.currentTime, anchor = builder.reviewAnchor;
    // YouTube can resume slightly before the requested cue due to keyframe seeking.
    if (anchor !== null && time < anchor) return;
    const previous = builder.reviewTime ?? anchor;
    builder.reviewAnchor = null;
    builder.reviewTime = time;
    if (previous === null || time <= previous) return;
    const index = builder.markers.findIndex((marker) => marker.time > previous && marker.time <= time);
    if (index < 0) return;
    const marker = builder.markers[index];
    selectVideoMarker(index, true);
    $('builder-review-status').textContent = 'Paused at hit point ' + marker.label + ' at ' + markerTime(marker.time)
      + (marker.bar ? ' (bar ' + marker.bar + ')' : '') + '. Edit the hit point or resume.';
  }

  function zoomAroundMarker() {
    builder.zoomStart = clamp(builder.markers[builder.selected].time - 5, 0, Math.max(0, builderMedia().duration - 10));
  }

  function timelineRange() {
    const duration = builderMedia().duration;
    return builder.zoomStart === null ? [0, duration] : [builder.zoomStart, Math.min(builder.zoomStart + 10, duration)];
  }

  function updateVideoPlayhead() {
    if (!videoReady()) return;
    const [start, end] = timelineRange(), time = builderMedia().currentTime, playhead = $('builder-playhead');
    playhead.hidden = time < start || time > end;
    playhead.style.left = 100 * (time - start) / (end - start) + '%';
  }

  function markerLimits(index) {
    const min = roundMarkerTime(builder.markers[index - 1].time + 0.001);
    const end = roundMarkerTime(builderMedia().duration);
    const max = builder.markers[index + 1] ? Math.min(end, roundMarkerTime(builder.markers[index + 1].time - 0.001)) : end;
    return min <= max ? [min, max] : null;
  }

  function renderVideoTimeline() {
    const timeline = $('builder-timeline');
    timeline.hidden = !videoReady();
    if (timeline.hidden) return;
    const duration = builderMedia().duration, [start, end] = timelineRange();
    const zoom = $('builder-zoom'), zoomed = builder.zoomStart !== null;
    zoom.hidden = duration <= 10;
    zoom.setAttribute('aria-pressed', String(zoomed));
    zoom.textContent = zoomed ? 'Show full timeline' : 'Zoom around hit point';
    $('builder-timeline-start').textContent = markerTime(start);
    $('builder-timeline-end').textContent = markerTime(end);
    const points = $('builder-timeline-points');
    // Keep the existing buttons so keyboard focus survives a nudge or selection.
    while (points.children.length > builder.markers.length) points.lastElementChild.remove();
    builder.markers.forEach((marker, index) => {
      let button = points.children[index];
      if (!button) {
        button = document.createElement('button');
        button.type = 'button';
        button.className = 'builder-timeline-point';
        button.dataset.index = index;
        points.append(button);
      }
      button.hidden = marker.time < roundMarkerTime(start) || marker.time > roundMarkerTime(end);
      button.style.left = clamp(100 * (marker.time - start) / (end - start), 0, 100) + '%';
      button.title = markerHeading(marker) + (marker.bar ? ' · Bar ' + marker.bar : index ? '' : ' · Video start');
      button.setAttribute('aria-label', button.title);
      button.setAttribute('aria-pressed', String(index === builder.selected));
    });
    const selected = builder.markers[builder.selected], limits = builder.selected ? markerLimits(builder.selected) : null;
    $('builder-nudge-back').disabled = !limits || selected.time <= limits[0];
    $('builder-nudge-forward').disabled = !limits || selected.time >= limits[1];
    updateVideoPlayhead();
  }

  function moveVideoMarker(index, time, recenter) {
    const marker = builder.markers[index];
    if (time !== marker.time) {
      marker.time = time;
      builder.markers.sort((a, b) => a.time - b.time);
      builder.dirty = true;
    }
    selectVideoMarker(builder.markers.indexOf(marker), true, recenter);
    renderVideoPlan();
  }

  function nudgeVideoMarker(index, delta) {
    if (!index || !videoReady()) return;
    const limits = markerLimits(index);
    if (!limits) return;
    const time = clamp(roundMarkerTime(builder.markers[index].time + delta), ...limits);
    const [start, end] = timelineRange();
    moveVideoMarker(index, time, time < roundMarkerTime(start) || time > roundMarkerTime(end));
  }

  function wireVideoTimeline() {
    const rail = $('builder-timeline-rail');
    let drag = null;
    $('builder-zoom').addEventListener('click', () => {
      if (builder.zoomStart === null) zoomAroundMarker();
      else builder.zoomStart = null;
      renderVideoTimeline();
    });
    $('builder-nudge-back').addEventListener('click', () => nudgeVideoMarker(builder.selected, -0.1));
    $('builder-nudge-forward').addEventListener('click', () => nudgeVideoMarker(builder.selected, 0.1));
    const indexOfButton = (target) => Number(target.closest('.builder-timeline-point')?.dataset.index);
    rail.addEventListener('click', (e) => {
      if (!videoReady()) return;
      const index = indexOfButton(e.target);
      if (Number.isInteger(index)) {
        if (e.detail === 0 || !index) selectVideoMarker(index, true, false);
      } else {
        const [start, end] = timelineRange(), rect = rail.getBoundingClientRect();
        seekBuilderVideo(start + clamp((e.clientX - rect.left) / rect.width, 0, 1) * (end - start));
      }
    });
    rail.addEventListener('keydown', (e) => {
      const index = indexOfButton(e.target);
      if (!index || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
      e.preventDefault();
      nudgeVideoMarker(index, e.key === 'ArrowLeft' ? -0.1 : 0.1);
    });
    rail.addEventListener('pointerdown', (e) => {
      let index = indexOfButton(e.target);
      if (drag || !videoReady() || !e.isPrimary || e.button !== 0 || !Number.isInteger(index)) return;
      const marker = builder.markers[index];
      e.target.focus({ preventScroll: true });
      index = builder.markers.indexOf(marker);
      if (index < 0) return;
      selectVideoMarker(index, true, false);
      if (index === 0 || !markerLimits(index)) return;
      const button = $('builder-timeline-points').children[index];
      button.setPointerCapture(e.pointerId);
      drag = { id: e.pointerId, button, marker, x: e.clientX, time: marker.time };
    });
    function previewDrag(e, index) {
      const [start, end] = timelineRange(), limits = markerLimits(index);
      if (!limits) return null;
      const msPerPixel = (end - start) / rail.getBoundingClientRect().width;
      const time = clamp(roundMarkerTime(drag.time + (e.clientX - drag.x) * msPerPixel),
        Math.max(roundMarkerTime(start), limits[0]), Math.min(roundMarkerTime(end), limits[1]));
      drag.button.style.left = clamp(100 * (time - start) / (end - start), 0, 100) + '%';
      $('builder-time').value = markerTime(time);
      $('builder-marker-title').textContent = markerHeading(drag.marker, time);
      seekBuilderVideo(time);
      return time;
    }
    rail.addEventListener('pointermove', (e) => {
      if (drag?.id !== e.pointerId || !videoReady()) return;
      const index = builder.markers.indexOf(drag.marker);
      if (index > 0) previewDrag(e, index);
    });
    rail.addEventListener('pointerup', (e) => {
      if (drag?.id !== e.pointerId) return;
      const index = builder.markers.indexOf(drag.marker);
      const time = index > 0 && videoReady() ? previewDrag(e, index) : null;
      drag = null;
      if (time !== null) moveVideoMarker(index, time, false);
    });
    rail.addEventListener('lostpointercapture', (e) => {
      if (drag?.id !== e.pointerId) return;
      const index = builder.markers.indexOf(drag.marker);
      drag = null;
      if (index > 0) selectVideoMarker(index, true, false);
    });
  }

  function addVideoMarker() {
    if (!videoReady()) return;
    const video = builderMedia(), time = roundMarkerTime(video.currentTime);
    resetVideoReview(time);
    video.pause();
    let index = builder.markers.findIndex((marker) => marker.time === time);
    if (index < 0) {
      if (builder.markers.length >= 502) { toast('A track can have at most 500 sections.'); return; }
      index = builder.markers.findIndex((marker) => marker.time > time);
      if (index < 0) index = builder.markers.length;
      if (builder.markers.length === 501 && builder.markers[0].bar !== '') { toast('A track can have at most 500 sections.'); return; }
      const marker = builderMarker(time, builder.markers[index - 1]);
      marker.label = newMarkerLabel(index);
      builder.markers.splice(index, 0, marker);
      builder.dirty = true;
    }
    selectVideoMarker(index);
    renderVideoPlan();
    $('builder-bar').focus();
  }

  function setBuilderMediaControls() {
    const video = builderMedia(), canPlay = builder.youtube ? video.readyState >= 1 && !video.error : videoReady();
    $('builder-add').disabled = !videoReady();
    $('builder-retime').disabled = !videoReady() || builder.selected === 0;
    $('builder-play').disabled = !canPlay;
    $('builder-play').textContent = video.paused ? 'Play video' : 'Pause video';
  }

  async function toggleBuilderVideo(resumeOnly = false) {
    const play = $('builder-play'), media = builderMedia();
    if (play.disabled) return;
    if (!media.paused && !resumeOnly) { media.pause(); return; }
    play.focus();
    if (!media.paused) return;
    const anchor = builder.reviewAnchor;
    resetVideoReview(anchor);
    if (anchor === null && !media.ended) builder.reviewTime = media.currentTime;
    try { await media.play(); }
    catch (e) { $('builder-media-status').textContent = 'Could not play video: ' + e.message; }
  }

  function wireVideoBuilder() {
    const video = $('builder-video'), fileInput = $('builder-file');
    wireVideoTimeline();
    $('builder-den').replaceChildren(...DENOMS.map((n) => new Option(n, n)));
    $('builder-unit').replaceChildren(...BEAT_UNITS.map(([value, name]) => new Option(name, value)));
    $('btn-video-track').addEventListener('click', () => {
      builder.returnTrack = null;
      switchView('video');
      refreshBuilderTargets(null);
      if (!builder.draftsBusy) runDraftAction(async () => { await refreshDraftList(); return ''; });
    });
    const link = $('builder-youtube-url'), drafts = $('builder-drafts'), draftStatus = $('builder-draft-status');
    function focusVideoSection(target) {
      if (!target) return '';
      const matches = builder.markers.map((marker, index) => ({ marker, index }))
        .filter(({ marker }) => target.bar != null && marker.bar !== '' && Number(marker.bar) === target.bar);
      const match = matches.length === 1 ? matches[0] : null;
      const time = match ? match.marker.time : target.time;
      if (match) selectVideoMarker(match.index);
      if (videoReady()) seekBuilderVideo(time);
      else if (builder.youtube) builder.youtube.pendingSeek = { time, markerIndex: match ? match.index : null };
      return match
        ? ' Selected hit point ' + match.marker.label + ' for score bar ' + target.bar + '.'
        : ' No unique hit point for ' + (target.bar == null ? 'this cue' : 'score bar ' + target.bar)
          + '; showing its estimated video position instead.';
    }

    async function editWithVideo(target = null) {
      if (!current || builder.draftsBusy) return;
      const track = current;
      if (performanceMode) await toggleFullscreen();
      if (current !== track) return;
      if (track._preview) {
        switchView('video');
        if (tracks.includes(builder.returnTrack)) selectTrack(builder.returnTrack.id);
        draftStatus.textContent = 'Returned to the unsaved video draft.' + focusVideoSection(target);
        return;
      }
      builder.returnTrack = track;
      switchView('video');
      refreshBuilderTargets(track.id);
      runDraftAction(async () => {
        await refreshDraftList();
        if ($('view-video').hidden || builder.returnTrack !== track) return 'Open cancelled.';
        if (track.videoDraftId) {
          if (track.videoDraftId === builder.draftId) return 'Linked video draft is open.' + focusVideoSection(target);
          if (!Array.from(drafts.options).some((o) => o.value === String(track.videoDraftId))) {
            return 'The linked draft is no longer available. Open another saved draft to link it.';
          }
          const draft = await draftRequest('GET', track.videoDraftId);
          if ($('view-video').hidden || builder.returnTrack !== track || !confirmBuilderReplace()) return 'Open cancelled.';
          drafts.value = draft.id;
          openYoutube(draft.videoId, draft);
          return 'Opened linked draft: ' + draft.title + '.' + focusVideoSection(target);
        }
        if (builder.localTrack === track) return 'Local video is still open in this tab.' + focusVideoSection(target);
        return 'Open your saved YouTube draft below, then link it to this track for next time.';
      });
    }
    $('btn-edit-video').addEventListener('click', () => editWithVideo());
    $('btn-practice-edit-video').addEventListener('click', () => editWithVideo());
    $('quick-edit-video').addEventListener('click', () => {
      if (builder.draftsBusy || quickEditIndex == null || !sections()[quickEditIndex]) {
        toast('Wait for the video operation to finish, then retry.');
        return;
      }
      const target = { bar: startBars(sections())[quickEditIndex], time: sectionTimeline()[quickEditIndex].start };
      $('quick-edit-dialog').close();
      editWithVideo(target);
    });
    $('builder-back-to-edit').addEventListener('click', () => {
      if (tracks.includes(builder.returnTrack)) selectTrack(builder.returnTrack.id);
      switchView('edit');
    });
    $('preview-return').addEventListener('click', () => {
      switchView('video');
      if (tracks.includes(builder.returnTrack)) selectTrack(builder.returnTrack.id);
    });
    $('builder-link-draft').addEventListener('click', () => {
      const track = builder.returnTrack;
      if (!tracks.includes(track) || !builder.draftId || builder.draftsBusy) return;
      if (!window.confirm('Link this video draft to “' + track.title + '”? This will not change its click sections.')) return;
      track.videoDraftId = builder.draftId;
      track._linkDirty = true;
      markDirty(track);
      renderDraftControls();
      draftStatus.textContent = 'Draft linked. Changes to the draft do not change the finished track.';
    });
    function loadLink() {
      const id = youtubeId(link.value);
      if (!id) { draftStatus.textContent = 'Enter a YouTube video link, such as a watch or youtu.be URL.'; return; }
      if (builder.draftsBusy || !confirmBuilderReplace()) return;
      draftStatus.textContent = '';
      drafts.value = '';
      openYoutube(id);
    }
    $('builder-load-youtube').addEventListener('click', loadLink);
    $('builder-replace-youtube').addEventListener('click', () => {
      const id = youtubeId(link.value);
      if (!id) { draftStatus.textContent = 'Enter a valid YouTube video link first.'; return; }
      if (builder.draftsBusy) return;
      if (!window.confirm('Replace the video while keeping the hit points? Saving updates this draft for all linked tracks.')) return;
      const draft = { id: builder.draftId, title: $('builder-title').value, markers: builder.markers };
      openYoutube(id, draft);
      builder.dirty = true;
      draftStatus.textContent = 'Video replaced. Save the YouTube draft to keep this change.';
    });
    let shiftPreview = null;
    function proposedShift() {
      const input = $('builder-shift');
      if (input.value === '' || !input.reportValidity() || !Number.isFinite(input.valueAsNumber)) throw new Error('Enter a shift in seconds.');
      const shift = input.valueAsNumber, points = builder.markers;
      const musical = points.filter((marker, index) => index > 0 || marker.bar !== '');
      if (!musical.length) throw new Error('Add musical hit points before shifting.');
      const shifted = musical.map((marker) => ({ ...marker, time: roundMarkerTime(marker.time + shift) }));
      if (shifted.some((marker) => marker.time < 0 || marker.time > 86400
        || (videoReady() && marker.time > roundMarkerTime(builderMedia().duration)))) {
        throw new Error('The shift would put a hit point outside the video or the 24-hour limit.');
      }
      if (shifted[0].time > 0) {
        const reference = points[0].bar === '' ? { ...points[0] } : builderMarker(0);
        const used = new Set(shifted.map((marker) => marker.label));
        if (used.has(reference.label)) reference.label = nextMarkerNumber(used, 0);
        shifted.unshift(reference);
      }
      if (shifted.length > 502) throw new Error('The shifted draft would exceed the 502 hit point limit.');
      return shifted;
    }
    $('builder-shift-preview').addEventListener('click', () => {
      try {
        const shifted = proposedShift();
        shiftPreview = { original: JSON.stringify(builder.markers), value: $('builder-shift').value };
        const before = builder.markers.find((m, i) => i > 0 || m.bar !== '');
        const after = shifted.find((m, i) => i > 0 || m.bar !== '');
        $('builder-shift-status').textContent = 'First musical hit point: ' + markerTime(before.time) + ' → ' + markerTime(after.time)
          + '. Last hit point: ' + markerTime(builder.markers.at(-1).time) + ' → ' + markerTime(shifted.at(-1).time) + '.';
        $('builder-shift-apply').disabled = false;
      } catch (error) {
        shiftPreview = null;
        $('builder-shift-apply').disabled = true;
        $('builder-shift-status').textContent = error.message;
      }
    });
    $('builder-shift').addEventListener('input', () => { shiftPreview = null; $('builder-shift-apply').disabled = true; });
    $('builder-shift-apply').addEventListener('click', () => {
      if (!shiftPreview || shiftPreview.original !== JSON.stringify(builder.markers) || shiftPreview.value !== $('builder-shift').value) {
        $('builder-shift-status').textContent = 'The draft changed. Preview the shift again.';
        $('builder-shift-apply').disabled = true;
        return;
      }
      try {
        builder.markers = proposedShift();
      } catch (error) {
        $('builder-shift-status').textContent = error.message;
        $('builder-shift-apply').disabled = true;
        shiftPreview = null;
        return;
      }
      builder.dirty = true;
      shiftPreview = null;
      $('builder-shift-apply').disabled = true;
      selectVideoMarker(0);
      renderVideoPlan();
      $('builder-shift-status').textContent = 'Shift applied. Save the draft and update the click track separately.';
    });
    link.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); loadLink(); } });
    drafts.addEventListener('change', renderDraftControls);
    $('builder-open-draft').addEventListener('click', () => runDraftAction(async () => {
      const draft = await draftRequest('GET', drafts.value);
      if (!confirmBuilderReplace()) return 'Open cancelled.';
      drafts.value = draft.id;
      openYoutube(draft.videoId, draft);
      return 'Opened saved draft: ' + draft.title;
    }));
    $('builder-save-draft').addEventListener('click', () => runDraftAction(async () => {
      await saveYoutubeDraft();
      await refreshDraftList();
      return builder.dirty ? 'Draft saved, but there are newer unsaved edits.' : 'Draft saved to your account.';
    }));
    $('builder-delete-draft').addEventListener('click', () => {
      const id = Number(drafts.value);
      if (!id || !window.confirm('Delete this saved YouTube draft? Finished click tracks will not be deleted.')) return;
      runDraftAction(async () => {
        await draftRequest('DELETE', id);
        tracks.filter((t) => t.videoDraftId === id).forEach((t) => { t.videoDraftId = null; });
        cacheLocal();
        if (builder.draftId === id) { builder.draftId = null; builder.dirty = true; }
        await refreshDraftList();
        return 'Saved draft deleted.';
      });
    });
    fileInput.addEventListener('change', () => {
      const file = fileInput.files[0];
      fileInput.value = '';
      if (!file || builder.draftsBusy || !confirmBuilderReplace()) return;
      resetBuilderSource();
      builder.url = URL.createObjectURL(file);
      builder.markers = [builderMarker(0)];
      link.value = '';
      draftStatus.textContent = '';
      video.src = builder.url;
      $('builder-title').value = file.name.replace(/\.[^.]+$/, '').slice(0, 255);
      $('builder-media-status').textContent = 'Loading ' + file.name + '…';
      selectVideoMarker(0);
      setBuilderMediaControls();
      renderVideoPlan();
      renderDraftControls();
    });
    video.addEventListener('loadedmetadata', () => {
      if (builder.youtube) return;
      setBuilderMediaControls();
      renderVideoTimeline();
      $('builder-media-status').textContent = videoReady() ? 'Video duration: ' + markerTime(video.duration)
        : 'This video does not have a usable duration.';
      renderVideoPlan();
    });
    video.addEventListener('error', () => {
      if (builder.youtube) return;
      setBuilderMediaControls();
      renderVideoTimeline();
      $('builder-media-status').textContent = 'This browser could not play the video. Try an MP4 encoded as H.264.';
      renderVideoPlan();
    });
    for (const event of ['play', 'pause', 'ended']) video.addEventListener(event, setBuilderMediaControls);
    for (const event of ['timeupdate', 'seeking']) video.addEventListener(event, updateVideoPlayhead);
    video.addEventListener('seeking', () => { if (!builder.youtube) builder.reviewTime = null; });
    video.addEventListener('ended', () => { if (!builder.youtube) reviewVideoPlayback(); });
    $('builder-play').addEventListener('click', () => toggleBuilderVideo());
    $('builder-review').addEventListener('change', () => resetVideoReview());
    $('builder-title').addEventListener('input', () => { builder.dirty = true; });
    $('builder-add').addEventListener('click', addVideoMarker);
    $('builder-retime').addEventListener('click', () => {
      const index = builder.selected;
      if (!index || !videoReady()) return;
      const time = roundMarkerTime(builderMedia().currentTime), limits = markerLimits(index);
      if (!limits || time < limits[0] || time > limits[1]) {
        $('builder-review-status').textContent = 'Choose a new time between the neighbouring hit points.';
        return;
      }
      const changed = builder.markers[index].time !== time;
      moveVideoMarker(index, time, true);
      $('builder-review-status').textContent = changed ? 'Hit point moved to ' + markerTime(time) + '.'
        : 'Seek to a different time first, or drag the hit point on the timeline.';
    });
    $('builder-markers').addEventListener('change', (e) => selectVideoMarker(Number(e.target.value), true));
    $('builder-marker-label').addEventListener('change', (e) => {
      const selected = builder.selected;
      const marker = builder.markers[selected];
      const label = e.target.value.trim().toUpperCase();
      if (!/^[0-9]{1,9}[A-Z]{0,8}$/.test(label)) {
        e.target.value = marker.label;
        toast('Enter a hit point number, optionally followed by letters (e.g. 16A).');
        return;
      }
      let labels;
      try {
        labels = planMarkerRenumber(builder.markers, selected, label, $('builder-renumber-following').checked);
      } catch (error) {
        e.target.value = marker.label;
        toast(error.message);
        return;
      }
      let following = 0;
      builder.markers.forEach((point, index) => {
        if (point.label === labels[index]) return;
        point.label = labels[index];
        builder.dirty = true;
        if (index > selected) following++;
      });
      renderSelectedMarkerHeading();
      renderVideoMarkers();
      renderVideoTimeline();
      renderVideoPlan();
      if (following) toast('Renumbered ' + following + ' following hit point' + (following === 1 ? '.' : 's.'));
    });
    for (const field of markerFields) {
      $('builder-' + field).addEventListener('change', (e) => {
        builder.markers[builder.selected][field] = e.target.value;
        builder.dirty = true;
        if (field === 'bar') renderSelectedSkip();
        $('builder-bpm-field').hidden = builder.markers[builder.selected].shape === 'steady';
        renderVideoMarkers();
        renderVideoTimeline();
        renderVideoPlan();
      });
    }
    $('builder-skip').addEventListener('input', (e) => {
      if (!e.target.validity.valid) $('builder-result').textContent = 'Enter skipped score bars as a whole number from 0 to 9998.';
      else renderVideoPlan();
      renderDraftControls();
    });
    $('builder-skip').addEventListener('change', (e) => {
      if (e.target.value === '') e.target.value = '0';
      if (!e.target.reportValidity()) return;
      const next = nextMusicalMarker(builder.selected);
      if (!next) {
        $('builder-result').textContent = 'Add a following score-bar hit point before setting skipped bars.';
        return;
      }
      next.skip = e.target.value;
      builder.dirty = true;
      renderVideoMarkers();
      renderVideoPlan();
    });
    $('builder-visual-cue').addEventListener('change', (e) => {
      builder.markers[builder.selected].visualCue = e.target.checked;
      builder.dirty = true;
      renderVideoMarkers();
      renderVideoPlan();
    });
    $('builder-time').addEventListener('change', (e) => {
      const marker = builder.markers[builder.selected];
      const parsed = parseTimecode(e.target.value), time = parsed == null ? null : roundMarkerTime(parsed);
      if (!videoReady() || builder.selected === 0 || time == null || time > roundMarkerTime(builderMedia().duration)
        || builder.markers.some((m) => m !== marker && m.time === time)) {
        e.target.value = markerTime(marker.time);
        toast('Enter a unique video time within the clip.');
        return;
      }
      moveVideoMarker(builder.selected, time, true);
    });
    $('builder-remove').addEventListener('click', () => {
      if (builder.selected === 0) return;
      builder.markers.splice(builder.selected, 1);
      builder.dirty = true;
      selectVideoMarker(builder.selected - 1);
      $('builder-review-status').textContent = 'Hit point removed. Resume video to continue from here.';
      renderVideoPlan();
    });
    async function saveVideoTrack(existing = null, stayInBuilder = false) {
      if (!videoReady() || builder.draftsBusy) return;
      const action = existing ? 'save over “' + existing.title + '”' : 'create track';
      const countIn = $('builder-count-in');
      if (!countIn.reportValidity()) return;
      try {
        const { offset, secs } = planVideoTrack();
        const startingCountIn = countIn.value === '' ? firstClickSection(existing)?.countIn ?? null : countIn.valueAsNumber;
        if (existing && !window.confirm('Replace all sections and the video offset in “' + existing.title
          + '”? Manual section edits will be replaced; the track title and notes will stay.'
          + (!builder.youtube && existing.videoDraftId ? ' This also removes the old YouTube draft link.' : ''))) return;
        let draftId = null;
        if (builder.youtube) {
          builder.draftsBusy = true;
          renderDraftControls();
          setStatus('Saving video draft…');
          if (!(await saveYoutubeDraft())) {
            toast('The draft changed while saving. Review your edits, then try saving the track again.');
            setStatus('Track not updated');
            return;
          }
          draftId = builder.draftId;
        }
        if (existing && !tracks.includes(existing)) throw new Error('The selected track was removed.');
        const sections = secs.map(({ section }) => section);
        sections[0].countIn = startingCountIn;
        const track = existing || makeTrack($('builder-title').value.trim() || 'Click track from video', sections);
        track.videoOffset = offset;
        if (existing) track.sections = sections.map(normSection);
        if (!builder.youtube) {
          builder.localTrack = track;
          builder.localSnapshot = {
            markers: builder.markers.map((marker) => ({ ...marker })),
            title: $('builder-title').value, countIn: countIn.value,
          };
        }
        if (existing && (track.videoDraftId ?? null) !== draftId) track._linkDirty = true;
        track.videoDraftId = draftId;
        if (existing) {
          markDirty(track);
          selectTrack(track.id);
        } else addTrack(track);
        builder.returnTrack = track;
        refreshBuilderTargets(track.id);
        if (!builder.youtube) builder.dirty = false;
        if (!stayInBuilder && !$('view-video').hidden) switchView('edit');
        toast(existing ? 'Updated “' + track.title + '” from video hit points' : 'Created click track from video hit points');
        return track;
      } catch (e) {
        $('builder-result').textContent = 'Could not ' + action + ': ' + e.message;
        setStatus('Track not updated');
      } finally {
        builder.draftsBusy = false;
        renderDraftControls();
      }
    }
    $('builder-create').addEventListener('click', () => saveVideoTrack());
    $('builder-target-track').addEventListener('change', renderDraftControls);
    $('builder-overwrite').addEventListener('click', () => {
      const track = builderTargetTrack();
      if (track) saveVideoTrack(track);
    });
    const performDialog = $('builder-perform-dialog'), performStatus = $('builder-perform-status');
    let performBar = null;
    let performBusy = false;
    function setPerformBusy(busy) {
      performBusy = busy;
      performDialog.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
    }
    performDialog.addEventListener('cancel', (event) => { if (performBusy) event.preventDefault(); });
    function locateBar(track, bar) {
      const starts = startBars(track.sections);
      const matches = track.sections.map((section, index) => ({ section, index, start: starts[index] }))
        .filter(({ section, start }) => start != null && bar >= start && bar < start + section.bars);
      if (matches.length !== 1) throw new Error('The selected score bar does not uniquely match a playable section.');
      return { index: matches[0].index, offset: bar - matches[0].start };
    }
    function performTrack(track, bar) {
      const target = locateBar(track, bar);
      performDialog.close();
      selectTrack(track.id);
      switchView('perform');
      selectIdx(target.index);
      start(target.index, null, false, target.offset);
    }
    $('builder-perform').addEventListener('click', () => {
      const marker = builder.markers[builder.selected];
      const bar = Number(marker?.bar);
      if (!marker || marker.bar === '' || !Number.isInteger(bar) || bar < 1) {
        $('builder-result').textContent = 'Select a musical hit point with a score bar to perform from.';
        return;
      }
      performBar = bar;
      performStatus.textContent = '';
      performDialog.showModal();
    });
    $('builder-perform-cancel').addEventListener('click', () => performDialog.close());
    $('builder-perform-preview').addEventListener('click', () => {
      try {
        if (!videoReady()) throw new Error('Wait for the video to load.');
        if (!$('builder-count-in').checkValidity()) throw new Error('Close this dialog and enter a valid starting count-in.');
        const plan = planVideoTrack();
        const preview = makeTrack('Preview: ' + (builder.returnTrack?.title || $('builder-title').value), plan.secs.map((item) => item.section));
        preview.videoOffset = plan.offset;
        preview.sections[0].countIn = $('builder-count-in').value === '' ? firstClickSection(builder.returnTrack)?.countIn ?? null : $('builder-count-in').valueAsNumber;
        preview._dirty = false;
        preview._auto = true;
        preview._preview = true;
        preview._previewVideoId = builder.youtube?.id || null;
        preview._previewLocal = !builder.youtube && !!builder.url;
        locateBar(preview, performBar);
        tracks.push(preview);
        performTrack(preview, performBar);
        setStatus('Preview only — changes are not saved');
      } catch (e) { performStatus.textContent = 'Could not preview: ' + e.message; }
    });
    $('builder-perform-discard').addEventListener('click', async () => {
      const track = builder.returnTrack;
      if (performBusy) return;
      setPerformBusy(true);
      try {
        if (!tracks.includes(track)) throw new Error('The linked track is no longer available.');
        locateBar(track, performBar);
        if (builder.dirty) {
          if (!builder.youtube && builder.localSnapshot) {
            builder.markers = builder.localSnapshot.markers.map((marker) => ({ ...marker }));
            $('builder-title').value = builder.localSnapshot.title;
            $('builder-count-in').value = builder.localSnapshot.countIn;
            builder.dirty = false;
            selectVideoMarker(0);
            renderVideoPlan();
          } else {
            if (!builder.draftId || !builder.youtube) throw new Error('This draft has no saved version to restore. Save it or preview instead.');
            if (builder.draftsBusy) throw new Error('Wait for the current video operation to finish.');
            builder.draftsBusy = true;
            renderDraftControls();
            const draft = await draftRequest('GET', builder.draftId);
            await openYoutube(draft.videoId, draft);
          }
        }
        $('builder-count-in').value = builder.youtube ? '' : builder.localSnapshot?.countIn ?? '';
        performTrack(track, performBar);
      } catch (e) { performStatus.textContent = 'Could not discard and perform: ' + e.message; }
      finally { builder.draftsBusy = false; renderDraftControls(); setPerformBusy(false); }
    });
    $('builder-perform-save').addEventListener('click', async () => {
      if (performBusy) return;
      setPerformBusy(true);
      try {
        const track = builder.returnTrack;
        if (!tracks.includes(track)) throw new Error('The linked track is no longer available.');
        if (!videoReady()) throw new Error('Wait for the video to load before saving.');
        if (!$('builder-count-in').checkValidity()) throw new Error('Close this dialog and enter a valid starting count-in.');
        const plan = planVideoTrack();
        locateBar({ sections: plan.secs.map((item) => item.section) }, performBar);
        const saved = await saveVideoTrack(track, true);
        if (!saved) throw new Error('Save was cancelled or failed. Check the video editor status before retrying.');
        clearTimeout(saveTimer);
        if (!(await saveDirty())) throw new Error('The track is not saved yet. Check the save status and retry.');
        performTrack(saved, performBar);
      } catch (e) { performStatus.textContent = 'Could not save and perform: ' + e.message; }
      finally { setPerformBusy(false); }
    });
    window.addEventListener('beforeunload', (e) => {
      if (builder.dirty) { e.preventDefault(); e.returnValue = ''; }
    });
    selectVideoMarker(0);
    renderVideoPlan();
    renderDraftControls();
  }

  let performanceMode = false, nativeFullscreen = false;
  function setPerformanceMode(on) {
    performanceMode = on;
    document.body.classList.toggle('performance-mode', on);
    $('btn-exit-fullscreen').hidden = !on;
    $('performance-menu').open = false;
    if (!on) {
      $('rehearsal-panel').open = false;
      if ($('rehearsal-dialog').open) $('rehearsal-dialog').close();
    }
    renderRehearsalList();
    focusPerformancePedal();
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
    } finally {
      focusPerformancePedal();
    }
  }

  function switchView(name) {
    $('track-actions').open = false;
    cancelPendingPedal();
    if (name !== 'edit') stopTester();
    document.body.classList.toggle('editing', name === 'edit');
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === name));
    $('view-perform').hidden = name !== 'perform';
    $('view-edit').hidden = name !== 'edit';
    if (!CFG.offline) {
      $('view-video').hidden = name !== 'video';
      $('btn-video-track').setAttribute('aria-pressed', String(name === 'video'));
      if (name !== 'video') {
        resetVideoReview();
        builderMedia().pause();
      }
      if (name !== 'perform') closePracticeVideo();
      else { renderPracticeAvailability(); showPracticeVideo(); }
    }
    $('btn-pedal-setup').hidden = name !== 'edit';
    $('btn-editor-fullscreen').hidden = name !== 'edit';
    if (name === 'video') {
      stop(false);
      $('performance-menu').open = false;
      showPedalSetup(false);
      window.scrollTo(0, 0);
    } else if (name === 'edit') { $('performance-menu').open = false; renderEditor(); }
    else showPedalSetup(false);
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
    $('pedal-keys').textContent = Object.entries(PEDAL_ACTIONS).map(([action, label]) => {
      const keys = ['single', 'double'].flatMap((gesture) => Object.entries(prefs.pedalBindings[gesture])
        .filter(([, value]) => value === action).map(([key]) => (gesture === 'double' ? 'Double ' : '') + keyName(key)));
      return label + ': ' + (keys.join(', ') || 'Not assigned');
    }).join(' · ') + ' · Stop: Esc';
  }

  const pendingPedals = new Map();
  function cancelPendingPedal() {
    for (const { timer } of pendingPedals.values()) clearTimeout(timer);
    pendingPedals.clear();
  }
  function runPedal(action, key) {
    if ($('quick-edit-dialog').open || $('rehearsal-dialog').open) return;
    if ($('performance-menu').open && $('pre-show').open) {
      if (action) $('pre-show-pedal').textContent = keyName(key) + ' → ' + PEDAL_ACTIONS[action] + ' (test only).';
      return;
    }
    if (action === 'forward') advance();
    else if (action === 'back') back();
    else if (action === 'pause' && (E.playing || key === ' ')) togglePlay();
  }
  function pedalBinding(gesture, key) {
    const map = prefs.pedalBindings[gesture];
    return Object.hasOwn(map, key) ? map[key] : null;
  }
  function pressPedal(key, single, double) {
    const previous = pendingPedals.get(key);
    if (previous) {
      clearTimeout(previous.timer);
      pendingPedals.delete(key);
      if (performance.now() < previous.until) { runPedal(double, key); return; }
      runPedal(previous.single, previous.key);
    }
    if (!double) { runPedal(single, key); return; }
    if (!E.playing && (single === 'forward' || double === 'forward' || key === ' ')) ensureAudio();
    pendingPedals.set(key, { key, single, until: performance.now() + DOUBLE_PRESS_MS, timer: setTimeout(() => {
      pendingPedals.delete(key);
      runPedal(single, key);
    }, DOUBLE_PRESS_MS) });
  }

  let learning = false, learnTimer = null;
  function focusPerformancePedal() {
    if (!$('view-perform').hidden && $('pedal-setup').hidden
      && !$('quick-edit-dialog').open && !$('rehearsal-dialog').open) {
      $('performance-pedal-capture').focus({ preventScroll: true });
      renderPedalFocus();
    }
  }
  function renderPedalFocus() {
    const focused = document.hasFocus() && document.activeElement === $('pedal-capture');
    $('pedal-focus').textContent = focused
      ? 'Pedal input focused — press your pedal.'
      : 'Click the pedal input box or Learn pedal key to focus pedal input.';
    $('performance-pedal-focus').textContent = document.hasFocus() && document.activeElement === $('performance-pedal-capture')
      ? 'Pedal ready'
      : 'Tap the pedal input to enable pedal keys';
  }
  function showPedalSetup(on) {
    $('pedal-setup').hidden = !on;
    $('btn-pedal-setup').setAttribute('aria-expanded', String(on));
    if (!on) { setLearning(false); focusPerformancePedal(); }
    else {
      $('pedal-setup').scrollIntoView({ block: 'start' });
      $('pedal-capture').focus({ preventScroll: true });
      renderPedalFocus();
    }
  }
  function setLearning(on) {
    cancelPendingPedal();
    clearTimeout(learnTimer);
    learning = on;
    $('btn-learn').classList.toggle('learning', on);
    $('btn-learn').textContent = on ? 'Cancel learning' : 'Learn pedal key';
    if (on) {
      $('pedal-capture').focus({ preventScroll: true });
      $('pedal-input').textContent = 'Listening — press and release the pedal (Esc cancels). Space and arrow keys are supported.';
      learnTimer = setTimeout(() => {
        setLearning(false);
        $('pedal-input').textContent = 'No usable key received. Click the pedal input box, check keyboard mode and Bluetooth pairing, then try Learn again.';
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
    if (!hit || (!CFG.offline && !$('view-video').hidden)) return;
    if (learning) {
      setLearning(false);
      $('pedal-input').textContent = 'MIDI pedal detected. MIDI note-on / controller presses use Start / Next / resume.';
      toast('MIDI pedal detected ✓');
      return;
    }
    runPedal('forward', 'MIDI');
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
    if (t === $('pedal-capture') || t === $('performance-pedal-capture')) return false;
    if (t.tagName === 'TEXTAREA' || t.isContentEditable) return true;
    if (t.tagName === 'INPUT') return !['checkbox', 'range', 'button', 'file'].includes(t.type);
    return false;
  }

  function onKeyDown(e) {
    if ($('quick-edit-dialog').open) {
      if (e.key === 'Escape') {
        e.preventDefault();
        $('quick-edit-dialog').close();
      }
      return;
    }
    if ($('rehearsal-dialog').open) return;
    const k = pedalKey(e);
    const menu = $('track-actions');
    if (k === 'Escape' && menu.open) {
      e.preventDefault();
      menu.open = false;
      menu.firstElementChild.focus();
      return;
    }
    if (!CFG.offline && !$('view-video').hidden) {
      if (e.isComposing || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const onBar = e.target === $('builder-bar');
      const inTransport = $('builder-transport').contains(e.target);
      const canUseShortcut = inTransport || !e.target.closest?.(
        'input, textarea, select, button, a, summary, [contenteditable]'
      );
      if (k === 'Enter' && (onBar || canUseShortcut)) {
        e.preventDefault();
        if (!e.repeat && (!onBar || e.target.reportValidity())) toggleBuilderVideo(onBar);
      } else if (k === ' ' && canUseShortcut && videoReady()) {
        e.preventDefault();
        if (!e.repeat) addVideoMarker();
      } else if (k === ' ' && inTransport) {
        e.preventDefault();
      }
      return;
    }
    if (e.target === $('performance-pedal-capture') && !e.repeat) {
      $('performance-pedal-capture').value = k ? keyName(k) : 'Unidentified key';
    }
    if (e.target === $('pedal-capture') && !e.repeat) {
      $('pedal-capture').value = k ? keyName(k) : 'Unidentified key';
      $('pedal-input').textContent = 'Received: ' + (k ? keyName(k) : 'unidentified key')
        + (e.code ? ' (code: ' + e.code + ')' : '');
    }
    if (learning && e.target === $('pedal-capture')) {
      if (k === 'Escape' || k === 'Tab') {
        if (k === 'Escape') e.preventDefault();
        setLearning(false); $('pedal-input').textContent = 'Learning cancelled.';
        return;
      }
      e.preventDefault();
      if (e.repeat || !k || ['Shift', 'Control', 'Alt', 'Meta', 'AltGraph'].includes(k)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) {
        $('pedal-input').textContent = 'Modified shortcut received. Use an unmodified arrow, Page Up/Down, Space or Enter keyboard mode.';
        return;
      }
      const action = $('pedal-action').value, gesture = $('pedal-gesture').value;
      prefs.pedalBindings[gesture][k] = action;
      savePrefs(); renderPedalKeys(); setLearning(false);
      $('pedal-input').textContent = 'Learned ' + (gesture === 'double' ? 'double ' : '') + keyName(k)
        + ' for ' + PEDAL_ACTIONS[action] + '. Try it in Perform.';
      return;
    }
    if (e.target === $('pedal-capture')) {
      if (k !== 'Tab') e.preventDefault();
      return;
    }
    if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    if ((k === 'Enter' || k === ' ') && e.target.closest?.('button, summary, #practice-enable')) return;
    const editing = document.body.classList.contains('editing');
    const single = editing ? null : pedalBinding('single', k);
    const double = editing ? null : pedalBinding('double', k);
    if ((k === 't' || k === 'T') && !single && !double) {
      e.preventDefault();
      if (!e.repeat) tapTempo();
      return;
    }
    const handled = single || double || (editing && k === ' ') || k === 'Escape';
    if (!handled) return;
    e.preventDefault();
    if (e.repeat) return;
    if (k === 'Escape') {
      if (learning) { setLearning(false); $('pedal-input').textContent = 'Learning cancelled.'; return; }
      if ($('performance-menu').open && $('pre-show').open) { $('pre-show').open = false; focusPerformancePedal(); return; }
      stopTester();
      stop(false);
      if (performanceMode) toggleFullscreen();
    }
    else if (editing) {
      if (E.playing) stop(false);
      tester.running ? stopTester() : startTester();
    } else pressPedal(k, single, double);
  }

  function wire() {
    wireAudioOutputs();
    if (!CFG.offline) wireVideoBuilder();
    if (!CFG.offline) {
      $('practice-enable').addEventListener('change', (e) => {
        videoEnabled = e.target.checked;
        if (!videoEnabled) { closePracticeVideo(); return; }
        const available = hasPracticeVideo();
        renderPracticeAvailability();
        $('practice-video').hidden = false;
        $('perform-layout').classList.toggle('with-video', available);
        if (available) openPracticeVideo();
      });
      $('practice-sound').addEventListener('change', (e) => practice?.media?.mute(!e.target.checked));
      $('practice-resync').addEventListener('click', () => syncPracticeVideo(true));
      $('practice-from-start').addEventListener('click', () => {
        if (!practice?.media) return;
        if (outputBusy) { toast('Finish choosing an audio output before starting.'); return; }
        stop(false);
        practice.positionKey = practice.syncKey = null;
        start(Math.max(0, sections().findIndex((s) => s.type === 'click')), null, true);
      });
    }
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('focus', () => {
      if (document.activeElement === document.body || document.activeElement === $('performance-pedal-capture')) focusPerformancePedal();
      renderPedalFocus();
    });
    window.addEventListener('blur', () => { cancelPendingPedal(); renderPedalFocus(); });
    document.addEventListener('focusin', renderPedalFocus);
    document.addEventListener('focusout', renderPedalFocus);
    $('btn-pedal-setup').addEventListener('click', () => {
      const opening = $('pedal-setup').hidden;
      $('track-actions').open = false;
      showPedalSetup(opening);
      if (!opening) $('track-actions').firstElementChild.focus();
    });
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
    $('btn-exit-fullscreen').addEventListener('click', toggleFullscreen);
    $('btn-exit-stage').addEventListener('click', toggleFullscreen);
    $('rehearsal-play').addEventListener('click', () => { cancelPendingPedal(); togglePlay(); });
    $('btn-quick-edit').addEventListener('click', () => {
      if (CFG.offline || !current || !sections().length) return;
      if (transportBusy || outputBusy) { toast('Wait for the audio operation to finish, then retry.'); return; }
      cancelPendingPedal();
      if (E.playing) stop(false);
      quickEditIndex = selectedIdx;
      $('quick-edit-title').textContent = 'Edit ' + sectionTitle(sections()[quickEditIndex], quickEditIndex);
      $('quick-edit-dialog').showModal();
      renderSections();
      $('quick-edit-section').querySelector('[data-f="name"]')?.focus();
    });
    $('quick-edit-section').addEventListener('input', onFieldInput);
    $('quick-edit-section').addEventListener('change', onFieldInput);
    $('quick-edit-section').addEventListener('click', onSectionAction);
    $('quick-edit-done').addEventListener('click', () => $('quick-edit-dialog').close());
    $('quick-edit-perform').addEventListener('click', () => {
      if (quickEditIndex == null || !sections()[quickEditIndex]) return;
      const index = quickEditIndex;
      $('quick-edit-dialog').close();
      selectIdx(index);
      start(index);
    });
    $('quick-edit-dialog').addEventListener('close', () => {
      quickEditIndex = null;
      renderSections();
      focusPerformancePedal();
    });
    let rehearsalTarget = null;
    function openRehearsal(idx, offset, title) {
      if ((E.playing && !E.suspended) || transportBusy) {
        $('rehearsal-bar-status').textContent = 'Pause playback before choosing a restart.';
        return;
      }
      const section = sections()[idx];
      if (!section) return;
      rehearsalTarget = { track: current, index: idx, section, offset };
      $('rehearsal-target').textContent = title;
      const select = $('rehearsal-count-in');
      select.disabled = section.type === 'pause';
      select.value = '';
      const atBar = offset ? { ...section, bpm: bpmAt(section, offset / section.bars) } : section;
      select.options[0].textContent = select.disabled ? 'Silent cue — waits for pedal'
        : section.countIn === 0
          ? 'Automatic (' + countInLabel({ ...atBar, countIn: null }) + ')'
          : 'Use section setting (' + countInLabel(atBar) + ')';
      $('rehearsal-dialog-help').textContent = select.disabled
        ? 'This silent cue waits for the pedal; count-in does not apply.'
        : 'The count-in applies only to this restart.';
      $('rehearsal-dialog').showModal();
    }
    $('rehearsal-list').addEventListener('click', (e) => {
      const button = e.target.closest('button[data-index]');
      if (button) openRehearsal(Number(button.dataset.index), 0, button.textContent);
    });
    $('rehearsal-bar-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = $('rehearsal-bar');
      if (!input.reportValidity()) return;
      const target = input.valueAsNumber, secs = sections(), bars = startBars(secs);
      const matches = secs.map((section, idx) => ({ section, idx }))
        .filter(({ section, idx }) => section.type === 'click' && target >= bars[idx] && target < bars[idx] + section.bars);
      if (matches.length !== 1) {
        $('rehearsal-bar-status').textContent = matches.length
          ? 'That score bar appears in more than one section. Select the section from the list instead.'
          : 'That bar is not playable in this track; it may be a skipped score bar.';
        return;
      }
      $('rehearsal-bar-status').textContent = '';
      const { idx, section } = matches[0];
      openRehearsal(idx, target - bars[idx], 'Bar ' + target + ' · ' + sectionTitle(section, idx));
    });
    $('rehearsal-cancel').addEventListener('click', () => $('rehearsal-dialog').close());
    $('rehearsal-form').addEventListener('submit', (e) => {
      e.preventDefault();
      if (transportBusy || outputBusy) { toast('Wait for the audio operation to finish, then retry.'); return; }
      if (!rehearsalTarget || rehearsalTarget.track !== current
        || sections()[rehearsalTarget.index] !== rehearsalTarget.section || (E.playing && !E.suspended)) {
        $('rehearsal-dialog').close();
        toast('Pause playback and select the rehearsal point again.');
        return;
      }
      const idx = rehearsalTarget.index, countIn = $('rehearsal-count-in');
      const section = rehearsalTarget.section, offset = rehearsalTarget.offset;
      const override = countIn.disabled ? null : countIn.value !== '' ? Number(countIn.value)
        : section.countIn === 0
          ? effectiveCountIn({ ...section, bpm: bpmAt(section, offset / section.bars), countIn: null })
          : null;
      $('rehearsal-dialog').close();
      stop(false);
      selectIdx(idx);
      start(idx, override, false, offset);
      focusPerformancePedal();
    });
    $('performance-menu').addEventListener('toggle', () => {
      cancelPendingPedal();
      if ($('performance-menu').open) renderStageTools();
      else {
        $('pre-show').open = false;
        focusPerformancePedal();
      }
    });
    $('pre-show').addEventListener('toggle', () => {
      cancelPendingPedal();
      if ($('pre-show').open) {
        if (canRouteAudio && mediaDevices?.enumerateDevices) refreshAudioOutputs();
        $('pre-show-pedal').textContent = 'Press your pedal. No transport action will run during the check.';
        showWake(wakeLock ? 'Screen wake lock active.' : 'Screen wake lock not active. Tap Check screen awake to test.');
      } else if (!E.playing) {
        cancelClicks();
        $('pre-show-audio').textContent = '';
        releaseWake();
      }
    });
    $('btn-test-clicks').addEventListener('click', async () => {
      if (E.playing || outputBusy) return;
      const status = $('pre-show-audio');
      try {
        stopTester();
        cancelClicks();
        const revision = audioRevision;
        if (prefs.volume === 0) {
          status.textContent = 'App volume is muted. Turn up Volume, then run the test again.';
          return;
        }
        const audio = ensureAudio(false);
        status.textContent = 'Starting audio…';
        try {
          if (audio.state !== 'running') await withTimeout(audio.resume(), 'Audio did not start. Check your audio output and tap to retry.', 4000);
        } catch (e) {
          if (revision !== audioRevision) return;
          throw e;
        }
        if (revision !== audioRevision || E.playing || outputBusy) return;
        if (audio.state !== 'running') throw new Error('Audio is ' + audio.state + '. Check your audio output and retry.');
        if (prefs.volume === 0) {
          status.textContent = 'App volume is muted. Turn up Volume, then run the test again.';
          return;
        }
        const time = audio.currentTime + 0.15;
        for (let i = 0; i < 4; i++) playClick(time + i * 0.5, i === 0 ? 3 : 2);
        status.textContent = 'Playing four clicks. If you hear nothing, check your device volume and try System default or reconnect your Bluetooth output.';
      } catch (e) {
        status.textContent = 'Could not play test clicks: ' + e.message;
      }
    });
    $('btn-test-wake').addEventListener('click', requestWake);
    $('btn-test-pedal').addEventListener('click', focusPerformancePedal);
    $('btn-print-cues').addEventListener('click', printCues);
    $('btn-backup-json').addEventListener('click', exportTrack);
    $('btn-export-wav').addEventListener('click', exportWav);
    $('wav-run').addEventListener('change', () => { $('btn-export-wav').disabled = !$('wav-run').value || wavBusy || E.playing; });
    if (!CFG.offline) {
      $('btn-prepare-offline').addEventListener('click', prepareOffline);
      $('open-offline').hidden = !loadJSON(OFFLINE_KEY, null);
    } else {
      $('btn-remove-offline').addEventListener('click', () => {
        try {
          localStorage.removeItem(OFFLINE_KEY);
          location.reload();
        } catch (e) { toast('Could not remove offline copy: ' + e.message); }
      });
    }
    document.addEventListener('fullscreenchange', () => {
      if (!document.fullscreenElement && nativeFullscreen) {
        nativeFullscreen = false;
        setPerformanceMode(false);
      }
    });
    // Keep focus off buttons/selects so pedal keys and Space never re-trigger them.
    document.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (b) b.blur();
      if (!e.target.closest('input, select, textarea, a, summary, [contenteditable]')) focusPerformancePedal();
    });

    $('btn-play').addEventListener('click', () => { cancelPendingPedal(); togglePlay(); });
    $('btn-stop').addEventListener('click', () => stop(false));
    $('btn-next').addEventListener('click', () => { cancelPendingPedal(); E.playing ? advance() : selectIdx(selectedIdx + 1); });
    $('btn-prev').addEventListener('click', () => { cancelPendingPedal(); back(); });
    // pointerdown gives tighter timing than click for tapping.
    $('btn-tap').addEventListener('pointerdown', (e) => { e.preventDefault(); tapTempo(); });
    $('btn-tap').addEventListener('keydown', (e) => { if (e.key === 'Enter') tapTempo(); });
    $('setlist').addEventListener('click', (e) => { const li = e.target.closest('li[data-i]'); if (li) jumpTo(+li.dataset.i); });
    $('setlist').addEventListener('dblclick', (e) => { const li = e.target.closest('li[data-i]'); if (li && !E.playing) start(+li.dataset.i); });
    document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => switchView(t.dataset.view)));

    $('track-select').addEventListener('change', (e) => { selectTrack(e.target.value); e.target.blur(); });
    const menu = $('track-actions'), summary = menu.firstElementChild;
    const closeMenu = (restoreFocus = false) => {
      menu.open = false;
      if (restoreFocus) summary.focus();
    };
    document.addEventListener('click', (e) => {
      if (menu.open && !menu.contains(e.target)) closeMenu();
    });
    // No window.prompt here: some browsers (in-app / embedded) block it, which made New appear to do nothing.
    $('btn-new-track').addEventListener('click', () => {
      addTrack(makeTrack('New click track', [{}]));
      switchView('edit');
      const t = $('track-title');
      t.focus(); t.select();
    });
    $('btn-dup-track').addEventListener('click', () => {
      closeMenu(true);
      if (current) {
        const t = makeTrack(current.title + ' (copy)', current.sections.map((s) => Object.assign({}, s)), current.notes);
        t.videoOffset = current.videoOffset;
        addTrack(t);
      }
    });
    $('btn-export-track').addEventListener('click', () => { closeMenu(true); exportTrack(); });
    $('btn-import-track').addEventListener('click', () => { closeMenu(true); $('import-file').click(); });
    $('import-file').addEventListener('change', (e) => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; });
    $('btn-delete-track').addEventListener('click', () => { closeMenu(true); deleteTrack(); });

    $('track-title').addEventListener('input', (e) => {
      if (!current) return;
      current.title = e.target.value.trim().slice(0, 255) || 'Untitled';
      const o = $('track-select').querySelector('option[value="' + CSS.escape(String(current.id)) + '"]');
      if (o) o.textContent = current.title;
      markDirty();
    });
    $('track-title').addEventListener('change', refreshTrackSelect);
    $('track-notes').addEventListener('input', (e) => { if (current) { current.notes = e.target.value.slice(0, 2000); markDirty(); } });
    $('track-count-in').addEventListener('change', (e) => {
      const section = firstClickSection(current);
      if (!section || !e.target.reportValidity()) return;
      section.countIn = e.target.value === '' ? null : e.target.valueAsNumber;
      renderSections();
      markDirty();
      if (!E.playing) renderIdle();
    });
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
    $('pref-beat-visual').value = prefs.beatVisual;
    renderBeatVisual();
    $('pref-beat-visual').addEventListener('change', (e) => {
      prefs.beatVisual = e.target.value;
      renderBeatVisual();
      renderPendulum();
      savePrefs();
      e.target.blur();
    });
    $('pref-sound').value = prefs.sound;
    $('pref-flash').checked = !!prefs.flash;
    $('pref-midi').checked = !!prefs.midi;
    $('pref-volume').addEventListener('input', (e) => { prefs.volume = +e.target.value; applyVolume(); savePrefs(); });
    $('pref-volume').addEventListener('change', (e) => e.target.blur());
    $('pref-sound').addEventListener('change', (e) => {
      prefs.sound = e.target.value; savePrefs(); e.target.blur();
      if (E.playing || tester.running || outputBusy) return;
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
      prefs.pedalBindings = defaultBindings();
      savePrefs(); renderPedalKeys();
      $('pedal-input').textContent = 'Defaults restored: Page Down for Forward, Page Up for Pause / resume, and double Page Up for Back.';
      toast('Pedal keys reset');
    });
    $('btn-clear-pedal').addEventListener('click', () => {
      setLearning(false);
      const gesture = $('pedal-gesture').value, action = $('pedal-action').value;
      const map = prefs.pedalBindings[gesture];
      for (const key of Object.keys(map)) if (map[key] === action) delete map[key];
      savePrefs(); renderPedalKeys();
      $('pedal-input').textContent = 'Cleared ' + gesture + ' bindings for ' + PEDAL_ACTIONS[action] + '.';
    });
  }

  async function init() {
    wire();
    renderPedalKeys();
    renderAll();
    requestAnimationFrame(frame);
    if (CFG.offline) {
      if (Array.isArray(offlineShow?.track?.sections) && offlineShow.track.sections.length) {
        tracks = [normTrack(offlineShow.track)];
        selectTrack(tracks[0].id);
        const date = new Date(offlineShow.prepared);
        $('offline-copy-status').textContent = 'Offline copy · ' + date.toLocaleString();
        if (prefs.midi) setMidi(true);
      } else {
        $('offline-copy-status').textContent = 'No offline show saved. Sign in online and choose Prepare offline show.';
        $('btn-remove-offline').hidden = true;
        $('view-perform').hidden = true;
      }
      return;
    }
    await loadTracks();
    selectTrack(loadJSON(LS_LAST, null));
    if (prefs.midi) setMidi(true);
  }

  init();
})();
