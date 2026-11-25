import { $, config, limitPosition } from './common.js';
import { PresentationPlayer } from './player.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
let state = null, received = 0, lastSuccess = 0, ended = false;
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
    if (state.playing) state.position += (received - start) / 2000;
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
    const now = limitPosition(state.show.cues, state.position, (performance.now() - received) / 1000, state.playing);
    player.render(state.show, now.position, now.playing, state.black || !state.active);
  }
  requestAnimationFrame(frame);
}
poll();
frame();
