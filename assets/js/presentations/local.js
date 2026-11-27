import { $, config, duration, element, limitPosition, message } from './common.js';
import { PresentationPlayer } from './player.js';
import { applyOutput } from './output.js';

const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
let show = structuredClone(config.show), position = 0, at = performance.now(), playing = false, black = false, muted = false;
const originals = [...new Set(show.cues.filter(cue => cue.asset && cue.kind !== 'youtube').map(cue => cue.asset.url))];
const cacheName = 'media-centre-local-' + config.id;
const urls = new Map();
let busy = false;
$('local-heading').textContent = config.title + ' · local playback';
$('local-seek').max = duration(show.cues);
const internet = show.cues.filter(cue => cue.kind === 'youtube');
message(config.embedded ? 'Embedded offline show ready. Rehearse video/audio format support on this device.'
  : internet.length ? internet.length + ' YouTube cues require internet and prevent a fully offline download.'
    : 'Cache media, then download the offline show. Opening this page alone does not make it available offline.');
function now() { return limitPosition(show.cues, position, (performance.now() - at) / 1000, playing); }
function anchor() { const value = now(); position = value.position; playing = value.playing; at = performance.now(); }
function jump(value) { position = value; at = performance.now(); playing = true; }
for (const cue of show.cues.filter(cue => cue.lane === 'picture' && cue.marker)) {
  const button = element('button', cue.name);
  button.onclick = () => jump(cue.start);
  $('local-points').append(button);
}
function runLocal(action) {
  anchor();
  if (action === 'play') playing = true;
  else if (action === 'pause') playing = false;
  else if (action === 'black') black = !black;
  else if (action === 'mute') muted = !muted;
  else if (action === 'return') { black = false; muted = false; config.output.pattern = 'off'; }
  else {
    const starts = show.cues.filter(cue => cue.lane === 'picture').map(cue => cue.start).sort((a,b) => a-b);
    const next = action === 'next' ? starts.find(start => start > position + 0.001) : starts.findLast(start => start < position - 0.05);
    if (next !== undefined) jump(next);
    else playing = false;
  }
}
document.querySelectorAll('[data-local]').forEach(button => button.onclick = () => runLocal(button.dataset.local));
window.addEventListener('keydown', event => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select')) return;
  const action = { Space: playing ? 'pause' : 'play', ArrowRight: 'next', ArrowLeft: 'previous',
    KeyB: 'black', KeyM: 'mute', KeyR: 'return' }[event.code];
  if (action) { event.preventDefault(); runLocal(action); }
});
$('local-seek').oninput = event => { position = Number(event.target.value); at = performance.now(); playing = false; };
$('fullscreen-local').onclick = async () => {
  try {
    $('preview').tabIndex = 0;
    await $('preview').requestFullscreen();
    $('preview').focus();
  }
  catch (error) { message('Could not open fullscreen: ' + error.message, true); }
};
function controls(value) {
  busy = value;
  for (const id of ['cache-local', 'download-local', 'clear-local']) $(id).disabled = value || config.embedded;
}
async function cachedShow(cache) {
  const next = structuredClone(config.show);
  for (const url of originals) {
    const response = await cache.match(url);
    if (!response?.ok) throw new Error('Media not cached: ' + url);
    if (!urls.has(url)) urls.set(url, URL.createObjectURL(await response.blob()));
  }
  if (!config.embedded && window.caches) {
    caches.open(cacheName).then(async cache => {
      const matches = await Promise.all(originals.map(url => cache.match(url)));
      if (matches.every(response => response?.ok)) {
        await cachedShow(cache);
        message('Using cached uploaded media on this device. ' + (internet.length ? 'YouTube still needs internet.' : 'Download the show for offline reopening.'));
      }
    }).catch(error => message('Could not read existing device cache: ' + error.message, true));
  }
  for (const cue of next.cues) if (cue.asset && urls.has(cue.asset.url)) cue.asset.url = urls.get(cue.asset.url);
  show = next;
}
$('cache-local').onclick = async () => {
  if (busy) return;
  controls(true);
  anchor(); playing = false;
  try {
    if (!window.caches) throw new Error('Device caching requires HTTPS and browser storage access.');
    const cache = await caches.open(cacheName);
    let total = 0, count = 0;
    for (const url of originals) {
      message('Caching media ' + (++count) + ' of ' + originals.length + '...');
      let response = await cache.match(url);
      if (!response?.ok) {
        const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 120000);
        try {
          response = await fetch(url, { credentials: 'omit', mode: 'cors', signal: abort.signal });
          if (!response.ok) throw new Error('HTTP ' + response.status + ': ' + url);
          const blob = await response.blob();
          if (blob.size > 100 * 1024 * 1024) throw new Error('Media exceeds 100 MB: ' + url);
          response = new Response(blob, { headers: { 'Content-Type': blob.type } });
        } finally { clearTimeout(timer); }
        await cache.put(url, response.clone());
      }
      total += (await response.blob()).size;
      if (total > 250 * 1024 * 1024) throw new Error('Local package exceeds the 250 MB total-media limit.');
    }
    await cachedShow(cache);
    message('Uploaded media cached. ' + (internet.length ? internet.length + ' YouTube cues still require internet.' :
      'Download the self-contained show now; browser caches alone may be evicted.'));
  } catch (error) { message('Caching stopped: ' + error.message + '. Completed files remain cached for retry.', true); }
  finally { controls(false); }
};
function dataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('Could not read cached media.'));
    reader.readAsDataURL(blob);
  });
}
$('download-local').onclick = async () => {
  if (busy) return;
  controls(true);
  try {
    if (internet.length) throw new Error('Replace YouTube cues with legally obtained uploaded videos before exporting an offline show.');
    if (!window.caches && originals.length) throw new Error('Cache media over HTTPS first.');
    const cache = originals.length ? await caches.open(cacheName) : null;
    const packed = structuredClone(config);
    let total = 0;
    const data = new Map();
    for (const url of originals) {
      const response = await cache.match(url);
      if (!response?.ok) throw new Error('Cache all media first: ' + url);
      const blob = await response.blob();
      total += blob.size;
      if (total > 250 * 1024 * 1024) throw new Error('Offline export is limited to 250 MB of media.');
      data.set(url, await dataUrl(blob));
    }
    for (const cue of packed.show.cues) if (cue.asset && data.has(cue.asset.url)) cue.asset.url = data.get(cue.asset.url);
    packed.embedded = true;
    const documentCopy = document.documentElement.cloneNode(true);
    documentCopy.querySelectorAll('[data-runtime],.media-layer,.output-overlay').forEach(node => node.remove());
    documentCopy.querySelector('#local-config').textContent = 'window.PRESENTATION_CONFIG = ' +
      JSON.stringify(packed).replace(/</g, '\\u003c') + ';';
    const file = URL.createObjectURL(new Blob(['<!doctype html>\n', documentCopy.outerHTML], { type: 'text/html' }));
    const link = element('a');
    link.href = file;
    link.download = 'performance-show-' + config.id + '.html';
    link.click();
    setTimeout(() => URL.revokeObjectURL(file), 60000);
    message('Offline file downloaded. Open it and rehearse with internet disconnected before relying on it.');
  } catch (error) { message('Could not export offline show: ' + error.message, true); }
  finally { controls(false); }
};
$('clear-local').onclick = async () => {
  if (!confirm('Remove this show\'s cached media from this device? Downloaded files are unaffected.')) return;
  controls(true);
  try {
    if (!window.caches) throw new Error('Browser device caching is unavailable.');
    await caches.delete(cacheName);
    player.clear();
    for (const url of urls.values()) URL.revokeObjectURL(url);
    urls.clear();
    show = structuredClone(config.show);
    message('Device cache cleared. This tab will use online media again.');
  } catch (error) { message('Could not clear cache: ' + error.message, true); }
  finally { controls(false); }
};
controls(false);
function frame() {
  const value = now();
  player.muted = muted || config.output.pattern && config.output.pattern !== 'off';
  player.render(show, value.position, value.playing, black);
  applyOutput($('preview'), config.output, black);
  $('clock').textContent = value.position.toFixed(1) + ' s · ' + (value.playing ? 'Playing' : 'Held');
  $('local-seek').value = value.position;
  $('operator-note').textContent = show.cues.findLast(cue => cue.lane === 'picture' &&
    cue.start <= value.position && cue.start + cue.duration > value.position)?.note || '';
  requestAnimationFrame(frame);
}
frame();
