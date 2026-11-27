import { $, element } from './common.js';
import { PresentationPlayer } from './player.js';

async function checkAsset(cues, signal) {
  const cue = cues[0];
  if (!cue.asset?.url) throw new Error('Missing library media. Re-add the asset to this cue.');
  const host = element('div', undefined, 'preflight-probe');
  host.setAttribute('aria-hidden', 'true');
  document.body.append(host);
  const player = new PresentationPlayer(host, element('p'), element('button'));
  player.muted = true;
  try {
    const layer = player.create(cue);
    await new Promise((resolve, reject) => {
      const start = performance.now();
      const finish = error => {
        clearInterval(timer);
        signal.removeEventListener('abort', cancelled);
        if (error) reject(error); else resolve();
      };
      const cancelled = () => finish(new Error('Checks cancelled.'));
      const timer = setInterval(() => {
        if (layer.error) finish(new Error(layer.error));
        else if (layer.ready && (!layer.video || layer.video.readyState >= 3)) finish();
        else if (performance.now() - start > 12000) finish(new Error('Timed out loading/buffering media. Check the URL, codec and network.'));
      }, 100);
      signal.addEventListener('abort', cancelled, { once: true });
      if (signal.aborted) cancelled();
    });
    const length = layer.video?.duration ?? layer.yt?.getDuration?.();
    if (['video', 'audio', 'youtube'].includes(cue.kind)) {
      if (!Number.isFinite(length) || length <= 0) return 'Warning: player loaded, but duration and excerpt bounds could not be verified. Rehearse this cue.';
      const invalid = cues.find(item => item.offset + item.duration > length + 0.05);
      if (invalid) throw new Error(`"${invalid.name}" extends beyond this media (${length.toFixed(1)} seconds). Shorten it or change the in point.`);
    }
    return cue.kind === 'youtube'
      ? 'YouTube embed ready and excerpt bounds checked. Playback, sound and buffering still need a rehearsal.'
      : cue.kind === 'image' ? 'Image loaded.' : 'Media loaded with initial playback data; excerpt bounds checked. Full playback is not pre-buffered.';
  } finally {
    player.clear();
    host.remove();
  }
}

export function setupPreflight(getShow) {
  let running = null;
  $('cancel-preflight').onclick = () => running?.abort();
  $('run-preflight').onclick = async () => {
    if (running) return;
    const controller = new AbortController();
    running = controller;
    $('run-preflight').disabled = true;
    $('cancel-preflight').hidden = false;
    $('preflight-results').replaceChildren();
    $('preflight-status').textContent = 'Loading timeline...';
    try {
      const show = await getShow();
      if (!show.cues.length) throw new Error('Add timeline cues before running checks.');
      const groups = new Map();
      for (const cue of show.cues) {
        if (!['image', 'video', 'audio', 'youtube'].includes(cue.kind)) continue;
        const key = cue.assetId ?? cue.id;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(cue);
      }
      let failed = 0, warned = 0, checked = 0;
      for (const warning of show.readinessWarnings || []) {
        $('preflight-results').append(element('li', 'Needs attention: ' + warning));
        warned += 1;
      }
      for (const cues of groups.values()) {
        if (controller.signal.aborted) break;
        const row = element('li', cues[0].name + ': checking...');
        $('preflight-results').append(row);
        $('preflight-status').textContent = `Checking media ${++checked} of ${groups.size}...`;
        try {
          const result = await checkAsset(cues, controller.signal);
          row.textContent = cues[0].name + ': ' + result;
          if (result.startsWith('Warning:')) warned += 1;
        } catch (error) {
          if (controller.signal.aborted) { row.textContent = 'Checks cancelled.'; break; }
          failed += 1;
          row.textContent = cues[0].name + ': FAILED: ' + error.message;
          row.classList.add('danger');
        }
      }
      $('preflight-status').textContent = controller.signal.aborted ? 'Checks cancelled. Results are incomplete.'
        : `Media checks finished: ${failed} failed, ${warned} warnings. ${failed || warned ? 'Needs attention.' : 'Ready for rehearsal.'} ${groups.size ? '' : 'No external media to load. '}Test sound and rehearse on every audience device.`;
    } catch (error) {
      $('preflight-status').textContent = 'Could not check presentation: ' + error.message;
    } finally {
      running = null;
      $('run-preflight').disabled = false;
      $('cancel-preflight').hidden = true;
    }
  };
  $('test-sound').onclick = async () => {
    const button = $('test-sound');
    let audio;
    button.disabled = true;
    try {
      audio = new AudioContext();
      await audio.resume();
      if (audio.state !== 'running') throw new Error('The browser has not allowed audio playback.');
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.connect(gain).connect(audio.destination);
      gain.gain.setValueAtTime(0, audio.currentTime);
      gain.gain.linearRampToValueAtTime(0.1, audio.currentTime + 0.03);
      gain.gain.linearRampToValueAtTime(0, audio.currentTime + 0.5);
      oscillator.frequency.value = 440;
      oscillator.start();
      oscillator.stop(audio.currentTime + 0.5);
      await new Promise(resolve => { oscillator.onended = resolve; });
      $('sound-check-status').textContent = 'Test tone sent. Confirm you heard it on the correct speakers. This does not verify video/YouTube autoplay or audience devices.';
    } catch (error) {
      $('sound-check-status').textContent = 'Sound check failed: ' + error.message;
    } finally {
      if (audio) await audio.close();
      button.disabled = false;
    }
  };
}
