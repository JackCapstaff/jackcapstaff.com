import { $, api, config, duration, element, limitPosition, message } from './common.js';
import { PresentationPlayer } from './player.js';

let show = null, assets = [], selected = null, dirty = false, formDirty = false, editVersion = 0, compiledShow = null;
let preview = { position: 0, playing: false, at: performance.now() };
const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
const form = $('cue-form');
const numeric = new Set(['start', 'duration', 'fade', 'fadeOut', 'offset', 'volume', 'fontSize']);
function changed() {
  dirty = true;
  editVersion += 1;
  compiledShow = null;
  message('Unsaved timeline changes. Apply cue properties, then Save presentation.');
}
function canLeaveCue() {
  return !formDirty || confirm('Discard unapplied cue properties? Use Apply cue changes to keep them.');
}
function validate(cues) {
  for (const cue of cues) {
    if (cue.duration <= 0 || cue.start < 0 || cue.start + cue.duration > 86400) throw new Error('Cues need positive durations and must fit within 24 hours.');
    if (cue.fade > cue.duration || cue.fadeOut > cue.duration) throw new Error('A fade cannot exceed its cue duration.');
    const asset = assets.find(asset => asset.id === cue.assetId);
    if (asset?.duration && cue.kind !== 'image' && cue.offset + cue.duration > asset.duration + 0.05) throw new Error('The cue extends beyond its media. Shorten it or change the in point.');
  }
  for (const lane of ['picture', 'text', 'audio']) {
    const items = cues.filter(cue => cue.lane === lane).sort((a, b) => a.start - b.start);
    if (items.some((cue, index) => index && items[index - 1].start + items[index - 1].duration > cue.start + 0.0001)) {
      throw new Error('Cues overlap in the ' + lane + ' lane. Place them end to end.');
    }
  }
}
function compiled() {
  if (!compiledShow) compiledShow = { title: $('show-title').value, cues: (show?.cues || []).map(cue => ({
    ...cue, ...(cue.assetId ? { asset: assets.find(asset => asset.id === cue.assetId) } : {}),
  })) };
  return compiledShow;
}
function select(id) {
  if (!canLeaveCue()) return;
  selected = id;
  const cue = show.cues.find(cue => cue.id === id);
  $('cue-empty').hidden = !!cue;
  form.hidden = !cue;
  formDirty = false;
  if (cue) {
    for (const field of form.elements) {
      if (field.name) field.value = cue[field.name] ?? ({ text: '', fontSize: 6, color: '#ffffff', position: 'bottom' }[field.name] ?? '');
    }
    $('cue-advance').hidden = cue.lane !== 'picture';
    $('cue-text-fields').hidden = cue.kind !== 'text';
    $('cue-media-fields').hidden = !['video', 'youtube', 'audio'].includes(cue.kind);
    const crossfade = form.elements.transition.querySelector('[value=crossfade]');
    crossfade.disabled = cue.lane === 'audio';
  }
  renderTimeline();
}
function renderTimeline() {
  if (!show) return;
  const scale = Number($('timeline-zoom').value);
  const end = Math.max(30, duration(show.cues) + 10);
  $('timeline').style.width = Math.max(600, end * scale) + 'px';
  $('time-ruler').replaceChildren();
  const step = scale < 10 ? 30 : 10;
  for (let second = 0; second <= end; second += step) {
    const label = element('span', second + 's');
    label.style.left = second * scale + 'px';
    $('time-ruler').append(label);
  }
  document.querySelectorAll('.lane').forEach(lane => {
    lane.querySelectorAll('button').forEach(button => button.remove());
    for (const cue of show.cues.filter(cue => cue.lane === lane.dataset.lane)) {
      const button = element('button', cue.name, 'timeline-cue' + (cue.id === selected ? ' selected' : ''));
      button.style.left = cue.start * scale + 'px';
      button.style.width = cue.duration * scale + 'px';
      button.title = `${cue.name}: ${cue.start}s, ${cue.duration}s. Click to edit or drag to move.`;
      button.setAttribute('aria-label', button.title);
      button.dataset.id = cue.id;
      button.draggable = true;
      button.onclick = () => select(cue.id);
      button.ondragstart = event => {
        event.dataTransfer.setData('application/x-studio-cue', cue.id);
        event.dataTransfer.effectAllowed = 'move';
      };
      lane.append(button);
    }
  });
}
function add(kind, asset, lane, start) {
  if (!canLeaveCue()) return;
  const seconds = kind === 'youtube' ? 10 : kind === 'video' || kind === 'audio' ? asset.duration : 5;
  const cue = { id: Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join(''),
    kind, lane, name: (asset?.name || (kind === 'text' ? 'Text overlay' : 'Black cue')).slice(0, 120),
    start, duration: Math.min(seconds || 10, 180), transition: 'cut', fade: Math.min(0.8, seconds || 10), fadeOut: 0,
    advance: 'auto', offset: 0, volume: kind === 'audio' ? 1 : 0,
    ...(asset ? { assetId: asset.id } : {}),
    ...(kind === 'text' ? { text: 'Your text here', color: '#ffffff', fontSize: 6, position: 'bottom' } : {}) };
  try {
    validate([...show.cues, cue]);
    show.cues.push(cue);
    changed();
    formDirty = false;
    select(cue.id);
  } catch (error) { message(error.message, true); }
}
function endOfLane(lane) { return duration(show.cues.filter(cue => cue.lane === lane)); }
function renderAssets() {
  $('asset-list').replaceChildren();
  for (const asset of assets) {
    const card = element('div', undefined, 'asset-card');
    card.draggable = true;
    card.dataset.id = asset.id;
    card.ondragstart = event => event.dataTransfer.setData('application/x-studio-asset', String(asset.id));
    if (asset.kind === 'image') {
      const image = element('img');
      image.src = asset.url;
      image.alt = '';
      card.append(image);
    }
    card.append(element('div', asset.name), element('p', asset.kind + (asset.duration ? ' · ' + asset.duration.toFixed(1) + 's' : ''), 'hint'));
    const button = element('button', 'Add to ' + (asset.kind === 'audio' ? 'audio' : 'picture'));
    button.onclick = () => {
      const lane = asset.kind === 'audio' ? 'audio' : 'picture';
      add(asset.kind, asset, lane, endOfLane(lane));
    };
    card.append(button);
    const remove = element('button', 'Delete media', 'danger');
    remove.onclick = async () => {
      if (show.cues.some(cue => cue.assetId === asset.id)) {
        message('Remove this media from the timeline and save before deleting it.', true);
        return;
      }
      if (!confirm('Permanently delete this media from Cloudinary and your library?')) return;
      try {
        await api('assets/' + asset.id, 'DELETE', {});
        assets = assets.filter(item => item.id !== asset.id);
        renderAssets();
        message('Media deleted.');
      } catch (error) { message('Could not delete media: ' + error.message, true); }
    };
    card.append(document.createTextNode(' '), remove);
    $('asset-list').append(card);
  }
}
async function upload(files) {
  $('media-files').disabled = true;
  try {
    for (const file of files) {
      if (file.size > 100 * 1024 * 1024) throw new Error(file.name + ' exceeds 100 MB.');
      message('Uploading ' + file.name + ' to Cloudinary…');
      const signed = await api('uploads/sign', 'POST', { name: file.name, size: file.size });
      const data = new FormData();
      data.append('file', file);
      for (const [key, value] of Object.entries(signed.parameters)) data.append(key, value);
      const response = await fetch(signed.endpoint, { method: 'POST', body: data, credentials: 'omit' });
      if (!response.ok) {
        const detail = await response.json();
        throw new Error(detail.error?.message || 'Cloudinary upload failed (HTTP ' + response.status + ').');
      }
      const stored = await response.json();
      assets.unshift(await api('assets', 'POST', { publicId: stored.public_id, name: file.name }));
      renderAssets();
    }
    message('Uploads saved in your media library. Drag them to the timeline or choose Add.');
  } catch (error) { message('Upload stopped: ' + error.message, true); }
  finally { $('media-files').disabled = false; $('media-files').value = ''; }
}
$('media-files').onchange = event => upload([...event.target.files]);
$('upload-zone').ondragover = event => { event.preventDefault(); $('upload-zone').classList.add('drag-over'); };
$('upload-zone').ondragleave = () => $('upload-zone').classList.remove('drag-over');
$('upload-zone').ondrop = event => {
  event.preventDefault();
  $('upload-zone').classList.remove('drag-over');
  upload([...event.dataTransfer.files]);
};
$('youtube-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    assets.unshift(await api('assets/youtube', 'POST', { name: $('youtube-name').value, url: $('youtube-url').value }));
    renderAssets();
    message('YouTube video added. Set its excerpt duration and in point on the timeline (up to 3 minutes per cue).');
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
};
document.querySelectorAll('.lane').forEach(lane => {
  lane.ondragover = event => event.preventDefault();
  lane.ondrop = event => {
    event.preventDefault();
    if (!canLeaveCue()) return;
    const start = Math.max(0, Math.round((event.clientX - lane.getBoundingClientRect().left) / Number($('timeline-zoom').value) * 10) / 10);
    const cueId = event.dataTransfer.getData('application/x-studio-cue');
    if (cueId) {
      const cue = show.cues.find(cue => cue.id === cueId);
      if (!cue || cue.lane !== lane.dataset.lane) { message('Move this cue within its own lane.', true); return; }
      const next = show.cues.map(item => item === cue ? { ...item, start } : item);
      try {
        validate(next);
        show.cues = next;
        formDirty = false;
        changed();
        select(cueId);
      } catch (error) { message(error.message, true); }
    } else {
      const asset = assets.find(asset => String(asset.id) === event.dataTransfer.getData('application/x-studio-asset'));
      if (!asset) return;
      const expected = asset.kind === 'audio' ? 'audio' : 'picture';
      if (expected !== lane.dataset.lane) { message('This asset belongs in the ' + expected + ' lane.', true); return; }
      add(asset.kind, asset, expected, start);
    }
  };
});
$('add-text').onclick = () => add('text', null, 'text', preview.position);
$('add-blank').onclick = () => add('blank', null, 'picture', endOfLane('picture'));
$('timeline-zoom').oninput = renderTimeline;
form.oninput = () => { formDirty = true; message('Cue properties changed. Choose Apply cue changes to update the timeline.'); };
form.onsubmit = event => {
  event.preventDefault();
  const cue = show.cues.find(cue => cue.id === selected);
  const candidate = { ...cue };
  for (const [key, value] of new FormData(form)) {
    if (!form.elements[key].closest('[hidden]')) candidate[key] = numeric.has(key) ? Number(value) : value;
  }
  try {
    const next = show.cues.map(item => item === cue ? candidate : item);
    validate(next);
    show.cues = next;
    formDirty = false;
    changed();
    select(selected);
  } catch (error) { message(error.message, true); }
};
$('delete-cue').onclick = () => {
  if (!confirm('Remove this cue from the timeline? Library media will be kept.')) return;
  show.cues = show.cues.filter(cue => cue.id !== selected);
  formDirty = false;
  changed();
  select(null);
};
$('show-title').oninput = changed;
$('save-show').onclick = async () => {
  if (formDirty) { message('Apply cue changes first, then save the presentation.', true); return; }
  const button = $('save-show');
  button.disabled = true;
  const savingVersion = editVersion;
  try {
    const saved = await api('shows/' + config.id, 'PUT', {
      title: $('show-title').value, cues: show.cues, revision: show.revision,
    });
    if (editVersion === savingVersion) { show = saved; compiledShow = null; dirty = false; }
    else show.revision = saved.revision;
    message(dirty || formDirty ? 'Saved the earlier version. New edits remain unsaved.' : 'Presentation saved. Restart a running broadcast to use this version.');
  } catch (error) { message('Not saved: ' + error.message, true); }
  finally { button.disabled = false; }
};
function previewNow() { return limitPosition(show?.cues || [], preview.position, (performance.now() - preview.at) / 1000, preview.playing); }
$('preview-play').onclick = () => {
  const now = previewNow();
  preview = { position: now.position >= duration(show.cues) - 0.002 ? 0 : now.position,
    playing: !now.playing, at: performance.now() };
};
$('preview-time').onchange = event => {
  if (!event.target.reportValidity()) return;
  preview = { position: Math.min(duration(show.cues), Number(event.target.value)), playing: false, at: performance.now() };
};
$('preview-next').onclick = () => {
  const now = previewNow();
  const next = show.cues.filter(cue => cue.lane === 'picture' && cue.start > now.position + 0.0001).sort((a, b) => a.start - b.start)[0];
  if (next) preview = { position: next.start, playing: true, at: performance.now() };
};
window.addEventListener('beforeunload', event => { if (dirty || formDirty) { event.preventDefault(); event.returnValue = ''; } });
async function init() {
  try {
    [show, { assets }] = await Promise.all([api('shows/' + config.id), api('assets')]);
    renderAssets();
    renderTimeline();
    message('Drag media onto the timeline, or add text and black cues below.');
    function frame() {
      const now = previewNow();
      player.render(compiled(), now.position, now.playing);
      $('preview-play').textContent = now.playing ? 'Pause preview' : 'Play preview';
      if (document.activeElement !== $('preview-time')) $('preview-time').value = now.position.toFixed(1);
      requestAnimationFrame(frame);
    }
    frame();
  } catch (error) { message('Could not open presentation: ' + error.message, true); }
}
init();
