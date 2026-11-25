import { $, api, config, element, limitPosition, message } from './common.js';
import { PresentationPlayer } from './player.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
player.muted = true;
let state = null, received = 0, queue = Promise.resolve(), connected = true;
const path = 'shows/' + config.id + '/live';
function buttons() {
  $('cue-buttons').replaceChildren();
  for (const cue of state.show.cues.filter(cue => cue.lane === 'picture').sort((a, b) => a.start - b.start)) {
    const button = element('button', cue.start.toFixed(1) + 's · ' + cue.name);
    button.onclick = () => command('seek', { position: cue.start });
    $('cue-buttons').append(button);
  }
}
function accept(value) {
  const changed = JSON.stringify(state?.show) !== JSON.stringify(value.show);
  state = value;
  received = performance.now();
  if (value.url) $('audience-link').href = value.url;
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
    if (button.dataset.action === 'start' && state?.active
      && !confirm('Restart the audience broadcast from the beginning with the latest saved timeline?')) return;
    command(button.dataset.action);
  };
});
$('seek-form').onsubmit = event => { event.preventDefault(); command('seek', { position: Number($('seek-time').value) }); };
$('monitor-sound').onchange = event => { player.muted = !event.target.checked; };
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
    accept(await api(path));
    message(state.active ? 'Connected to current broadcast.' : 'Ready. Start broadcast when you want the audience to see your show.');
    setInterval(() => { if (state?.active && connected) command('heartbeat'); }, 1000);
    function frame() {
      if (state) {
        const now = limitPosition(state.show.cues, state.position, (performance.now() - received) / 1000, state.playing && connected);
        player.render(state.show, now.position, now.playing, state.black || !state.active);
        $('clock').textContent = now.position.toFixed(1) + ' s · ' + (!connected ? 'Disconnected' : !state.active ? 'Offline' : now.playing ? 'Playing' : 'Paused / held');
      }
      requestAnimationFrame(frame);
    }
    frame();
  } catch (error) { connected = false; message('Could not load controller: ' + error.message, true); }
}
init();
