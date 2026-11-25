import { $, api, config, element, limitPosition, message } from './common.js';
import { PresentationPlayer } from './player.js';
import { setupPreflight } from './preflight.js';
import { musicalVisualState } from './music.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
player.muted = true;
let state = null, received = 0, queue = Promise.resolve(), connected = true;
let slides = [], nextId = null, cards = [];
let musicSession = null, musicKey = null, musicPlaying = false, musicLastSeen = 0, musicTimedOut = false;
let musicSuppressed = false;
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
    if (musicSession) {
      const event = state.show.music.events.find(event => event.cueId === cue.id);
      if (!event) { message('This cue has no musical event. Use a mapped thumbnail or the linked musical transport.', true); return; }
      musicSuppressed = false;
      sendMusic('jump', event);
    } else command('jump', { cueId: cue.id });
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
  $('save-placeholder').disabled = !connected || state.placeholder;
}
function accept(value) {
  const changed = JSON.stringify(state?.show) !== JSON.stringify(value.show);
  const placeholderChanged = !state || state.placeholderAsset?.id !== value.placeholderAsset?.id;
  state = value;
  const session = value.active ? value.show.music?.session || null : null;
  if (musicSession !== session) {
    musicSession = session;
    musicKey = null;
    musicPlaying = false;
    musicSuppressed = false;
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
  if (changed) buttons();
}
function command(action, extra = {}) {
  queue = queue.then(async () => {
    if (!connected || !state) return;
    try {
      accept(await api(path, 'PUT', { version: state.version, action, ...extra }));
      if (action !== 'heartbeat') message(action === 'revoke' ? 'Audience link replaced. Copy the new link.' : 'Broadcast updated.');
    } catch (error) {
      connected = false;
      message('Controller disconnected: ' + error.message + ' Reload to reconnect or take control. Audience will go black if updates stop.', true);
    }
  });
  return queue;
}
document.querySelectorAll('[data-action]').forEach(button => {
  button.onclick = () => {
    const action = button.dataset.action;
    if (musicSession && ['play', 'pause', 'next', 'previous'].includes(action)) {
      if (action === 'play') musicSuppressed = false;
      sendMusic(action);
      return;
    }
    if (musicSession && ['placeholder', 'stop', 'start'].includes(action)) {
      musicSuppressed = true;
      sendMusic('pause');
    }
    if (button.dataset.action === 'start' && state?.active
      && !confirm('Restart the audience broadcast from the beginning with the latest saved timeline?')) return;
    command(button.dataset.action);
  };
});
$('seek-form').onsubmit = event => {
  event.preventDefault();
  if (musicSession) { message('Seek using the linked musical rehearsal points or mapped slide thumbnails so music and visuals stay together.', true); return; }
  command('seek', { position: Number($('seek-time').value) });
};
$('monitor-sound').onchange = event => { player.muted = !event.target.checked; };
$('save-placeholder').onclick = () => command('setPlaceholder', {
  assetId: $('placeholder-asset').value ? Number($('placeholder-asset').value) : null,
});
$('copy-link').onclick = async () => {
  try {
    await navigator.clipboard.writeText($('audience-link').href);
    message('Audience link copied.');
  } catch (error) { message('Could not copy link: ' + error.message + '. Copy the Open audience view link instead.', true); }
};
$('revoke-link').onclick = () => {
  if (confirm('Disconnect viewers and replace the persistent audience link? You will need to share the new link.')) command('revoke');
};
async function init() {
  try {
    const [live, { assets }] = await Promise.all([api(path), api('assets')]);
    for (const asset of assets.filter(asset => asset.kind === 'image')) {
      const option = element('option', asset.name);
      option.value = asset.id;
      $('placeholder-asset').append(option);
    }
    accept(live);
    message(state.active ? 'Connected to current broadcast.' : 'Ready. Start broadcast when you want the audience to see your show.');
    setInterval(() => { if (state?.active && connected) command('heartbeat'); }, 1000);
    function frame() {
      if (state) {
        if (musicSession && !musicTimedOut && performance.now() - musicLastSeen > 4000) {
          musicTimedOut = true;
          musicSuppressed = true;
          musicPlaying = false;
          command('music', { session: musicSession, eventId: null, playing: false });
          $('music-connection').textContent = 'Musical transport disconnected. Visuals paused. Reconnect and use Play / resume.';
        }
        const now = limitPosition(state.show.cues, state.position, (performance.now() - received) / 1000, state.playing && connected, state.musicBoundary);
        player.render(state.show, now.position, now.playing, state.black || !state.active,
          state.placeholder ? state.placeholderAsset : null, state.musicFrozen);
        navigation(now.position);
        $('clock').textContent = now.position.toFixed(1) + ' s · ' + (!connected ? 'Disconnected' : !state.active ? 'Offline' : state.placeholder ? 'Placeholder / paused' : now.playing ? 'Playing' : 'Paused / held');
      }
      requestAnimationFrame(frame);
    }
    frame();
  } catch (error) { connected = false; message('Could not load controller: ' + error.message, true); }
}
init();
setupPreflight(async () => {
  return api('shows/' + config.id + '/compiled');
});
