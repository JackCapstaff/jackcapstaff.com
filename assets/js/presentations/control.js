import { $, api, config, element, limitPosition, message } from './common.js';
import { PresentationPlayer } from './player.js';
import { setupPreflight } from './preflight.js';
import { musicalVisualState } from './music.js';
import { applyOutput } from './output.js';
import { setupVisualInput } from './input.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
player.muted = true;
let state = null, received = 0, queue = Promise.resolve(), connected = true;
let slides = [], nextId = null, cards = [];
let musicSession = null, musicKey = null, musicPlaying = false, musicLastSeen = 0, musicTimedOut = false;
let musicSuppressed = false;
let visualOverride = false;
let outputLoaded = false;
let screensRendered = 0;
const path = 'shows/' + config.id + '/live';
function sendMusic(action, extra = {}) {
  $('music-performer').contentWindow?.postMessage({
    channel: 'media-centre-control', session: musicSession, action, ...extra,
  }, location.origin);
}
window.addEventListener('message', event => {
  if (event.origin === location.origin && event.source === $('music-performer').contentWindow &&
      event.data?.channel === 'media-centre-music-error' && event.data.session === musicSession) {
    musicSuppressed = true;
    musicPlaying = false;
    command('music', { session: musicSession, eventId: null, playing: false });
    message('Linked musical control failed: ' + event.data.message, true);
    return;
  }
  if (event.origin !== location.origin || event.source !== $('music-performer').contentWindow ||
      event.data?.channel !== 'media-centre-music' || event.data.session !== musicSession || !state?.active || !connected) return;
  if (visualOverride) { musicLastSeen = performance.now(); return; }
  const value = event.data;
  if (![value.section, value.bar, value.beat, value.run].every(Number.isInteger) ||
      !Number.isFinite(value.delay) || !Number.isFinite(value.sent)) return;
  musicLastSeen = performance.now();
  if (musicSuppressed && value.playing && !value.suspended && !musicTimedOut && state.placeholder) {
    // Placeholder stays selected until the operator explicitly resumes from this controller.
    sendMusic('pause');
  }
  musicTimedOut = false;
  $('music-connection').textContent = 'Click-track transport connected. ' + (value.suspended ? 'Paused.' : value.phase === 'pause' ? 'Waiting for pedal.' : value.playing ? 'Playing.' : 'Ready to start.');
  const visual = musicalVisualState(state.show.music, value);
  const key = visual.event ? value.run + ':' + visual.event.id : null;
  const newEvent = key !== null && key !== musicKey && value.playing && !value.suspended && !musicSuppressed;
  const playing = visual.playing && !!(visual.event || musicKey) && !musicSuppressed;
  if (newEvent || playing !== musicPlaying) {
    musicKey = newEvent ? key : musicKey;
    musicPlaying = playing;
    command('music', { session: musicSession, eventId: newEvent ? visual.event.id : null,
      elapsed: Math.max(0, Math.min(86400, (value.eventId === visual.event?.id && Number.isFinite(value.eventElapsed)
        ? value.eventElapsed : value.delay) + (Date.now() - value.sent) / 1000)), playing });
  }
});
function cueCard(cue) {
  const button = element('button', undefined, 'cue-card');
  button.dataset.cue = cue.id;
  button.setAttribute('aria-label', 'Go to ' + cue.name);
  const thumbnail = element('div', undefined, 'cue-thumbnail');
  if (cue.kind === 'image' || cue.kind === 'youtube') {
    const image = element('img');
    image.alt = '';
    image.loading = 'lazy';
    image.src = cue.kind === 'youtube' ? 'https://i.ytimg.com/vi/' + cue.asset.url + '/mqdefault.jpg' : cue.asset.url;
    image.onerror = () => thumbnail.replaceChildren(element('span', 'Thumbnail unavailable'));
    thumbnail.append(image);
  } else if (cue.kind === 'video') {
    const video = element('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.onloadedmetadata = () => { video.currentTime = Math.min(cue.offset, Math.max(0, video.duration - 0.02)); };
    video.onerror = () => thumbnail.replaceChildren(element('span', 'Video thumbnail unavailable'));
    video.src = cue.asset.url;
    thumbnail.append(video);
  } else {
    thumbnail.textContent = cue.kind === 'text' ? cue.text : 'Black slide';
    if (cue.kind === 'text') thumbnail.style.color = cue.color;
  }
  button.append(thumbnail, element('strong', cue.name), element('span', cue.start.toFixed(1) + 's · ' + cue.kind));
  if (cue.setName) button.append(element('small', cue.setName));
  button.onclick = () => {
    if (musicSession && $('jump-scope').value !== 'visual') {
      const event = state.show.music.events.find(event => event.cueId === cue.id);
      if (!event) { message('This cue has no musical event. Use a mapped thumbnail or the linked musical transport.', true); return; }
      musicSuppressed = false;
      visualOverride = false;
      sendMusic('jump', event);
    } else visualCommand('jump', { cueId: cue.id });
  };
  return button;
}
function buttons() {
  $('cue-buttons').replaceChildren();
  slides = state.show.cues.filter(cue => ['picture', 'text'].includes(cue.lane)).sort((a, b) => a.start - b.start);
  cards = slides.map(cue => {
    const button = cueCard(cue);
    $('cue-buttons').append(button);
    return { cue, button };
  });
  nextId = null;
  $('up-next').replaceChildren();
  $('rehearsal-points').replaceChildren();
  for (const cue of slides.filter(cue => cue.marker)) {
    const button = element('button', cue.name);
    button.onclick = () => cards.find(item => item.cue.id === cue.id).button.click();
    $('rehearsal-points').append(button);
  }
}
function navigation(position) {
  for (const { cue, button } of cards) {
    button.disabled = !state.active || !connected;
    button.setAttribute('aria-current', String(!state.placeholder && position >= cue.start && position < cue.start + cue.duration));
    button.classList.toggle('past', position >= cue.start + cue.duration);
  }
  const next = slides.find(cue => cue.start > position + 0.0001);
  if ((next?.id || '') !== nextId) {
    nextId = next?.id || '';
    $('up-next').replaceChildren(...(next ? [cueCard(next)] : []));
  }
  $('up-next').querySelectorAll('button').forEach(button => { button.disabled = !state.active || !connected; });
  $('up-next-status').textContent = !state.active ? 'Start a broadcast to navigate its slides.' : next ? 'Next slide at ' + next.start.toFixed(1) + 's' : 'No more slides.';
  $('show-placeholder').disabled = !state.active || !state.placeholderAsset || state.placeholder || !connected;
  if ($('save-placeholder')) $('save-placeholder').disabled = !connected || state.placeholder;
  const current = slides.findLast(cue => cue.lane === 'picture' && cue.start <= position && cue.start + cue.duration > position);
  $('operator-note').textContent = state.show.cues.filter(cue => cue.note && cue.start <= position &&
    cue.start + cue.duration > position).map(cue => cue.name + ': ' + cue.note).join('\n');
  $('cue-countdown').textContent = current ? `${current.name}: ${(current.start + current.duration - position).toFixed(1)}s remaining · ${
    state.placeholder ? 'Placeholder / waiting' : state.show.music && !visualOverride ? 'Musical control' : current.advance === 'manual' ? 'Hold at end / manual Next' : 'Automatic transition'}` : 'No current picture cue.';
  $('blackout').setAttribute('aria-pressed', String(state.black));
  $('mute-all').setAttribute('aria-pressed', String(state.muted));
  $('blackout').textContent = state.black ? 'Clear blackout' : 'Blackout';
  $('mute-all').textContent = state.muted ? 'Unmute all media' : 'Mute all media';
  $('rehearsal-points').querySelectorAll('button').forEach(button => { button.disabled = !state.active || !connected; });
}
function renderScreens() {
  const reports = state.screens || [];
  $('screen-reports').replaceChildren(...reports.map(report => element('li',
    `${report.id.slice(0, 8)}: ${Date.now() / 1000 - report.seen > 6 ? 'STALE / disconnected' : !report.visible ? 'Hidden tab' :
      report.blocked ? 'Autoplay blocked' : report.ready ? 'Ready' : 'Loading / failed'} · ${report.position.toFixed(1)}s${report.error ? ' · ' + report.error : ''}`)));
  if (!reports.length) $('screen-reports').append(element('li', 'No audience screen has reported yet.'));
}
function accept(value) {
  const changed = JSON.stringify(state?.show) !== JSON.stringify(value.show);
  const placeholderChanged = !state || state.placeholderAsset?.id !== value.placeholderAsset?.id;
  state = value;
  $('show-heading').textContent = value.show.title || config.title;
  $('saved-changes').hidden = !value.hasSavedChanges;
  $('version-status').textContent = !value.active ? 'Previewing the latest saved presentation. Nothing is on air.'
    : value.hasSavedChanges ? 'On air: earlier saved version. Newer saved changes are available below.'
    : 'On air: latest saved presentation. Saving edits will not interrupt this broadcast.';
  const session = value.active ? value.show.music?.session || null : null;
  if (musicSession !== session) {
    musicSession = session;
    musicKey = null;
    musicPlaying = false;
    musicSuppressed = false;
    visualOverride = false;
    musicLastSeen = performance.now();
    $('linked-music').hidden = !session;
    $('music-performer').src = session ? '/presentations/' + config.id + '/music/' + session + '/' : 'about:blank';
    $('music-connection').textContent = session ? 'Connecting musical transport...' : '';
    if (session && value.playing) {
      command('music', { session, eventId: null, playing: false });
    }
  }
  received = performance.now();
  if (value.url) $('audience-link').href = value.url;
  if (placeholderChanged) $('placeholder-asset').value = value.placeholderAsset?.id || '';
  if ($('output-form') && (!outputLoaded || !state.active) && !document.activeElement?.closest('#output-form')) {
    for (const field of $('output-form').elements) if (field.name && value.output?.[field.name] !== undefined) {
      if (field.type === 'checkbox') field.checked = value.output[field.name];
      else field.value = value.output[field.name];
    }
    outputLoaded = true;
  }
  if (changed) buttons();
}
function command(action, extra = {}) {
  queue = queue.then(async () => {
    if (!connected || !state) return;
    if (['heartbeat', 'music'].includes(action) && !state.active) return;
    if (action === 'music' && extra.session !== musicSession) return;
    try {
      accept(await api(path, 'PUT', { version: state.version, action, ...extra }));
      if (action === 'start' && $('saved-versions')) loadVersions();
      if (action !== 'heartbeat') message(action === 'revoke' ? 'Audience link replaced. Copy the new link.' : 'Broadcast updated.');
    } catch (error) {
      connected = false;
      message('Controller disconnected: ' + error.message + ' Reload to reconnect or take control. Audience will go black if updates stop.', true);
    }
  });
  return queue;
}
function visualCommand(action, extra = {}) {
  if (!state?.active || !connected) { message('Start a connected broadcast before using visual controls.', true); return; }
  if (musicSession && ['play', 'pause', 'next', 'previous', 'jump', 'placeholder'].includes(action)) {
    visualOverride = true;
    musicSuppressed = true;
    sendMusic('pause');
    $('music-connection').textContent = 'Visual-only override. Linked music paused; use a mapped musical jump or Play / resume to reconnect.';
  }
  command(action, { ...extra, visualOnly: true });
}
document.querySelectorAll('[data-action]').forEach(button => {
  button.onclick = () => {
    const action = button.dataset.action;
    if (musicSession && ['play', 'pause', 'next', 'previous'].includes(action)) {
      if (action === 'play') musicSuppressed = false;
      visualOverride = false;
      sendMusic(action);
      return;
    }
    if (button.dataset.action === 'start' && state?.active
      && !confirm('Restart the audience broadcast from the beginning with the latest saved timeline?')) return;
    if (musicSession && ['placeholder', 'stop', 'start'].includes(action)) {
      musicSuppressed = true;
      sendMusic('pause');
    }
    command(button.dataset.action);
  };
});
$('seek-form').onsubmit = event => {
  event.preventDefault();
  if (musicSession) { message('Seek using the linked musical rehearsal points or mapped slide thumbnails so music and visuals stay together.', true); return; }
  command('seek', { position: Number($('seek-time').value) });
};
$('load-saved-changes').onclick = () => document.querySelector('[data-action="start"]').click();
$('monitor-sound').onchange = event => { player.muted = !event.target.checked; };
if ($('save-placeholder')) $('save-placeholder').onclick = () => command('setPlaceholder', {
  assetId: $('placeholder-asset').value ? Number($('placeholder-asset').value) : null,
});
$('copy-link').onclick = async () => {
  try {
    await navigator.clipboard.writeText($('audience-link').href);
    message('Audience link copied.');
  } catch (error) { message('Could not copy link: ' + error.message + '. Copy the Open audience view link instead.', true); }
};
if ($('revoke-link')) $('revoke-link').onclick = () => {
  if (confirm('Disconnect viewers and replace the persistent audience link? You will need to share the new link.')) command('revoke');
};
async function init() {
  try {
    const live = await api(path);
    for (const asset of live.images || []) {
      const option = element('option', asset.name);
      option.value = asset.id;
      $('placeholder-asset').append(option);
    }
    accept(live);
    message(state.active ? 'Connected to current broadcast.' : 'Ready. Start broadcast when you want the audience to see your show.');
    setInterval(() => {
      if (!state || !connected) return;
      if (state.active) command('heartbeat');
      else queue = queue.then(async () => {
        try { accept(await api(path)); }
        catch (error) { connected = false; message('Could not reload saved presentation: ' + error.message, true); }
      });
    }, 1000);
    function frame() {
      if (state) {
        if (musicSession && !visualOverride && !musicTimedOut && performance.now() - musicLastSeen > 4000) {
          musicTimedOut = true;
          musicSuppressed = true;
          musicPlaying = false;
          command('music', { session: musicSession, eventId: null, playing: false });
          $('music-connection').textContent = 'Musical transport disconnected. Visuals paused. Reconnect and use Play / resume.';
        }
        const now = limitPosition(state.show.cues, state.position, (performance.now() - received) / 1000, state.playing && connected, state.musicBoundary);
        player.muted = !$('monitor-sound').checked || state.muted || state.output?.pattern && state.output.pattern !== 'off';
        player.render(state.show, now.position, now.playing, state.black,
          state.placeholder ? state.placeholderAsset : null, state.musicFrozen);
        applyOutput($('preview'), state.output, state.black);
        navigation(now.position);
        if (performance.now() - screensRendered > 1000) {
          renderScreens();
          screensRendered = performance.now();
        }
        $('clock').textContent = now.position.toFixed(1) + ' s · ' + (!connected ? 'Disconnected' : !state.active ? 'Offline' : state.placeholder ? 'Placeholder / paused' : now.playing ? 'Playing' : 'Paused / held');
      }
      requestAnimationFrame(frame);
    }
    frame();
  } catch (error) { connected = false; message('Could not load controller: ' + error.message, true); }
}
init();
setupPreflight(async () => {
  return api('shows/' + config.id + '/readiness');
});
setupVisualInput(action => visualCommand(action));
if ($('output-form')) $('output-form').onsubmit = event => {
  event.preventDefault();
  const output = Object.fromEntries(new FormData(event.target));
  for (const key of ['scale', 'x', 'y']) output[key] = Number(output[key]);
  output.safeArea = event.target.elements.safeArea.checked;
  command('configure', { output });
};
if ($('operators-form')) {
  api('shows/' + config.id + '/operators').then(value => { $('operator-usernames').value = value.usernames.join('\n'); })
    .catch(error => message('Could not load operators: ' + error.message, true));
  $('operators-form').onsubmit = async event => {
    event.preventDefault();
    try {
      await api('shows/' + config.id + '/operators', 'PUT', { usernames: $('operator-usernames').value.split(/\r?\n/).map(name => name.trim()).filter(Boolean) });
      message('Operator access saved.');
    } catch (error) { message('Could not save operators: ' + error.message, true); }
  };
}
if ($('saved-versions')) {
  loadVersions();
  $('restore-version').onclick = async () => {
    if (!$('saved-versions').value || !confirm('Restore this saved timeline? A live broadcast will not change. Concert child edits are not restored.')) return;
    try {
      const latest = await api('shows/' + config.id);
      await api('shows/' + config.id + '/versions', 'POST', { revision: latest.revision, restore: Number($('saved-versions').value) });
      location.reload();
    } catch (error) { message('Could not restore: ' + error.message, true); }
  };
  $('start-version').onclick = () => {
    if ($('saved-versions').selectedOptions[0]?.dataset.snapshot !== 'true') { message('Choose a version with a performance snapshot.', true); return; }
    if (confirm('Restart the broadcast from this frozen performance snapshot?')) {
      if (musicSession) sendMusic('pause');
      command('start', { snapshotRevision: Number($('saved-versions').value) });
    }
  };
}
function loadVersions() {
  return api('shows/' + config.id + '/versions').then(value => {
    $('saved-versions').replaceChildren();
    for (const version of value.versions) {
      const option = element('option', `Revision ${version.revision}: ${version.title} (${version.savedAt})${version.hasSnapshot ? ' · performance snapshot' : ''}`);
      option.value = version.revision;
      option.dataset.snapshot = String(version.hasSnapshot);
      $('saved-versions').append(option);
    }
  }).catch(error => message('Could not load versions: ' + error.message, true));
}
