import { $, config, limitPosition } from './common.js';
import { PresentationPlayer } from './player.js';
import { applyOutput, screenHealth } from './output.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
let state = null, received = 0, lastSuccess = 0, ended = false, latency = 0;
const screenId = [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, '0')).join('');
let outputPosition = 0;
async function poll() {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 3500);
  try {
    const start = performance.now();
    const response = await fetch(config.stateUrl, { credentials: 'omit', cache: 'no-store', signal: abort.signal });
    if (response.status === 404) { ended = true; throw new Error('This presentation link has ended or been replaced.'); }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    state = await response.json();
    received = performance.now();
    latency = state.playing ? (received - start) / 2000 : 0;
    lastSuccess = received;
    $('connection-status').textContent = state.active ? '' : 'Waiting for the presenter…';
  } catch (error) {
    state = null;
    $('connection-status').textContent = 'Presentation unavailable: ' + error.message + (ended ? '' : ' Reconnecting…');
  } finally {
    clearTimeout(timer);
    if (!ended) setTimeout(poll, 500);
  }
}
function frame() {
  if (!state || performance.now() - lastSuccess > 5000) player.render({ cues: [] }, 0, false, true);
  else {
    const now = limitPosition(state.show.cues, state.position, (performance.now() - received) / 1000 + latency, state.playing, state.musicBoundary);
    outputPosition = now.position;
    player.muted = !!state.muted || state.output?.pattern !== undefined && state.output.pattern !== 'off';
    player.render(state.show, now.position, now.playing, state.black || !state.active,
      state.placeholder ? state.placeholderAsset : null, state.musicFrozen);
    applyOutput($('preview'), state.output, state.black || !state.active);
  }
  requestAnimationFrame(frame);
}
poll();
frame();
let reporting = false;
setInterval(async () => {
  if (reporting || ended || !state?.active) return;
  reporting = true;
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 3500);
  try {
    const response = await fetch(config.reportUrl, { method: 'POST', credentials: 'omit',
      headers: { 'Content-Type': 'application/json' }, signal: abort.signal,
      body: JSON.stringify({ id: screenId, position: outputPosition, ...screenHealth(player) }) });
    if (!response.ok) throw new Error('HTTP ' + response.status);
  } catch (error) { $('connection-status').textContent = 'Screen status reporting failed: ' + error.message; }
  finally { clearTimeout(timer); reporting = false; }
}, 2000);
