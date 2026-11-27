import { $, api, config, duration, element, limitPosition, message, overlapError } from './common.js';
import { PresentationPlayer } from './player.js';
import { setupPreflight } from './preflight.js';
import { folderFiles, powerpointFiles } from './import.js';

let show = null, assets = [], selected = null, dirty = false, formDirty = false, editVersion = 0, compiledShow = null;
let preview = { position: 0, playing: false, at: performance.now() };
const player = new PresentationPlayer($('preview'), $('player-status'), $('allow-playback'));
const form = $('cue-form');
const numeric = new Set(['start', 'duration', 'fade', 'fadeOut', 'offset', 'volume', 'fontSize']);
const undo = [], redo = [];
let lastEdit = null;
let musicTracks = [], musicEditing = null;
let availableShows = [], concertPreview = { cues: [] };
let uploading = false, importItems = [], importStop = false;
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
function editSnapshot() { return { title: $('show-title').value, cues: structuredClone(show.cues), programme: structuredClone(show.programme), music: structuredClone(show.music) }; }
function historyButtons() {
  $('undo-edit').disabled = !undo.length;
  $('redo-edit').disabled = !redo.length;
}
function changed() {
  const next = editSnapshot();
  if (lastEdit && JSON.stringify(lastEdit) !== JSON.stringify(next)) {
    undo.push(lastEdit);
    if (undo.length > 100) undo.shift();
    redo.length = 0;
  }
  lastEdit = next;
  historyButtons();
  dirty = true;
  editVersion += 1;
  compiledShow = null;
  if (show.programme) concertPreview = { cues: [] };
  message('Unsaved timeline changes. Apply cue properties, then Save presentation.');
}
function canLeaveCue() {
  return !formDirty || confirm('Discard unapplied cue properties? Use Apply cue changes to keep them.');
}
function validate(cues) {
  if (cues.length > 500) throw new Error('Use at most 500 cues.');
  for (const cue of cues) {
    if (!Number.isFinite(cue.start) || !Number.isFinite(cue.duration) || cue.duration < 0.1 || cue.start < 0 || cue.start + cue.duration > 86400) throw new Error('Cues need durations of at least 0.1 seconds and must fit within 24 hours.');
    if (cue.fade > cue.duration || cue.fadeOut > cue.duration) throw new Error('A fade cannot exceed its cue duration.');
    const asset = assets.find(asset => asset.id === cue.assetId);
    if (asset?.duration && cue.kind !== 'image' && cue.offset + cue.duration > asset.duration + 0.05) throw new Error('The cue extends beyond its media. Shorten it or change the in point.');
  }
  const overlap = overlapError(cues);
  if (overlap) throw new Error(overlap);
}
function compiled() {
  if (show?.programme) return concertPreview;
  if (!compiledShow) compiledShow = { title: $('show-title').value, cues: (show?.cues || []).map(cue => ({
    ...cue, ...(cue.assetId ? { asset: assets.find(asset => asset.id === cue.assetId) } : {}),
  })) };
  return compiledShow;
}
function select(id, redraw = true) {
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
    $('cue-muted').checked = cue.muted || cue.volume === 0;
    $('cue-marker').checked = !!cue.marker;
    $('cue-marker-label').hidden = cue.lane === 'audio';
    volumeLabel();
  }
  if (redraw) renderTimeline();
  else document.querySelectorAll('.timeline-cue').forEach(button => button.classList.toggle('selected', button.dataset.id === id));
}
function renderTimeline() {
  if (!show) return;
  const scale = Number($('timeline-zoom').value);
  const end = Math.max(30, duration(show.cues) + 10);
  $('timeline').style.width = Math.max(600, end * scale) + 'px';
  $('timeline-scrub').max = duration(show.cues);
  $('preview-time').max = duration(show.cues);
  $('time-ruler').replaceChildren();
  const step = Math.max(scale < 10 ? 30 : 10, Math.ceil(end / 3000) * 10);
  for (let second = 0; second <= end; second += step) {
    const label = element('span', second + 's');
    label.style.left = second * scale + 'px';
    $('time-ruler').append(label);
  }
  document.querySelectorAll('.lane').forEach(lane => {
    lane.querySelectorAll('button').forEach(button => button.remove());
    const cues = show.cues.filter(cue => cue.lane === lane.dataset.lane).sort((a, b) => a.start - b.start);
    for (const [index, cue] of cues.entries()) {
      const button = element('button', cue.name, 'timeline-cue' + (cue.id === selected ? ' selected' : ''));
      button.style.left = cue.start * scale + 'px';
      button.style.width = cue.duration * scale + 'px';
      button.style.top = index % 2 ? '50px' : '24px';
      button.title = `${cue.name}: ${cue.start}s, ${cue.duration}s. Click to select, double-click for properties or drag to move.`;
      button.setAttribute('aria-label', button.title);
      button.dataset.id = cue.id;
      button.draggable = true;
      button.onclick = () => { if (selected !== cue.id) select(cue.id, false); };
      button.ondblclick = () => {
        if (selected !== cue.id) select(cue.id, false);
        if (selected !== cue.id) return;
        $('cue-properties-panel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        form.elements.name.focus({ preventScroll: true });
      };
      button.ondragstart = event => {
        event.dataTransfer.setData('application/x-studio-cue', cue.id);
        event.dataTransfer.effectAllowed = 'move';
      };
      for (const edge of ['in', 'out']) button.append(fadeHandle(cue, edge, scale, button));
      lane.append(button);
    }
  });
  renderMusic();
}
function fadeHandle(cue, edge, scale, button) {
  const field = edge === 'in' ? 'fade' : 'fadeOut';
  const handle = element('span', undefined, 'fade-handle fade-' + edge);
  handle.tabIndex = 0;
  handle.setAttribute('role', 'slider');
  handle.setAttribute('aria-label', cue.name + ' fade ' + edge + ' duration');
  handle.setAttribute('aria-valuemin', '0');
  handle.setAttribute('aria-valuemax', String(cue.duration));
  const refresh = () => {
    const value = edge === 'in' && cue.transition === 'cut' ? 0 : cue[field];
    handle.style[edge === 'in' ? 'left' : 'right'] = Math.max(0, Math.min(value * scale, cue.duration * scale - 16)) + 'px';
    handle.setAttribute('aria-valuenow', String(value));
    handle.setAttribute('aria-valuetext', value.toFixed(2) + ' seconds');
    handle.title = 'Drag to set fade ' + edge + ': ' + value.toFixed(2) + 's. Arrow keys adjust by 0.1s.';
    button.style.setProperty('--fade-' + edge, value * scale + 'px');
  };
  const set = value => {
    cue[field] = Math.max(0, Math.min(cue.duration, Math.round(value * 1000) / 1000));
    if (edge === 'in' && cue.transition !== 'crossfade') cue.transition = cue[field] ? 'fade' : 'cut';
    compiledShow = null;
    refresh();
  };
  handle.onclick = handle.ondblclick = event => event.stopPropagation();
  handle.ondragstart = event => { event.stopPropagation(); event.preventDefault(); };
  handle.onpointerdown = event => {
    event.stopPropagation();
    event.preventDefault();
    if (event.button !== 0 || !canLeaveCue()) return;
    formDirty = false;
    const startX = event.clientX, original = cue[field], transition = cue.transition;
    const initial = edge === 'in' && transition === 'cut' ? 0 : original;
    handle.setPointerCapture(event.pointerId);
    handle.onpointermove = move => set(initial + (move.clientX - startX) / scale * (edge === 'in' ? 1 : -1));
    const finish = cancelled => {
      handle.onpointermove = handle.onpointerup = handle.onpointercancel = null;
      if (cancelled) { cue[field] = original; cue.transition = transition; compiledShow = null; }
      else { formDirty = false; changed(); }
      select(cue.id);
    };
    handle.onpointerup = () => finish(false);
    handle.onpointercancel = () => finish(true);
  };
  handle.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (!canLeaveCue()) return;
    const value = edge === 'in' && cue.transition === 'cut' ? 0 : cue[field];
    set(event.key === 'Home' ? 0 : event.key === 'End' ? cue.duration :
      value + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 1 : 0.1));
    formDirty = false;
    changed();
    select(cue.id);
    document.querySelector(`.timeline-cue[data-id="${CSS.escape(cue.id)}"] .fade-${edge}`)?.focus();
  };
  refresh();
  return handle;
}
function add(kind, asset, lane, start) {
  if (show.programme) { message('Add assets to a set presentation; the concert links those sets.', true); return; }
  if (!canLeaveCue()) return;
  const seconds = kind === 'youtube' ? 10 : kind === 'video' || kind === 'audio' ? asset.duration : 5;
  const cue = { id: newId(),
    kind, lane, name: (asset?.name || (kind === 'text' ? 'Text overlay' : 'Black cue')).slice(0, 120),
    start, duration: seconds || 10, transition: 'cut', fade: Math.min(0.8, seconds || 10), fadeOut: 0,
    advance: 'auto', offset: 0, volume: 1, muted: kind !== 'audio',
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
        undo.length = redo.length = 0;
        lastEdit = editSnapshot();
        historyButtons();
        renderAssets();
        message('Media deleted.');
      } catch (error) { message('Could not delete media: ' + error.message, true); }
    };
    card.append(document.createTextNode(' '), remove);
    $('asset-list').append(card);
  }
  renderProgramme();
}
function renderProgramme() {
  if (!show) return;
  $('concert-mode').checked = !!show.programme;
  $('concert-settings').hidden = !show.programme;
  $('music-links-panel').hidden = !!show.programme;
  $('timeline-panel').hidden = !!show.programme;
  $('cue-properties-panel').hidden = !!show.programme;
  $('preview-next').textContent = show.programme ? 'Next concert cue' : 'Next picture cue';
  $('concert-placeholder').replaceChildren(element('option', 'Choose an image from the library'));
  $('concert-placeholder').firstChild.value = '';
  for (const asset of assets.filter(asset => asset.kind === 'image')) {
    const option = element('option', asset.name);
    option.value = asset.id;
    $('concert-placeholder').append(option);
  }
  $('concert-placeholder').value = show.programme?.placeholderId || '';
  $('concert-set').replaceChildren();
  for (const item of availableShows.filter(item => item.id !== show.id)) {
    const option = element('option', item.title);
    option.value = item.id;
    $('concert-set').append(option);
  }
  $('concert-sets').replaceChildren();
  for (const [index, id] of (show.programme?.sets || []).entries()) {
    const item = availableShows.find(item => item.id === id);
    const row = element('li');
    const label = element('a', item?.title || 'Missing presentation');
    if (item) { label.href = item.editUrl; label.target = '_blank'; label.rel = 'noopener'; }
    row.append(label, document.createTextNode(' '));
    for (const [text, action, disabled] of [
      ['Move up', () => [index - 1, index], index === 0],
      ['Move down', () => [index, index + 1], index === show.programme.sets.length - 1],
      ['Remove', () => null, false],
    ]) {
      const button = element('button', text);
      button.disabled = disabled;
      button.onclick = () => {
        const pair = action();
        if (pair) {
          const sets = show.programme.sets;
          [sets[pair[0]], sets[pair[1]]] = [sets[pair[1]], sets[pair[0]]];
        } else show.programme.sets.splice(index, 1);
        changed();
        renderProgramme();
      };
      row.append(button, document.createTextNode(' '));
    }
    $('concert-sets').append(row);
  }
}
$('concert-mode').onchange = () => {
  if (!show || !canLeaveCue() || ($('concert-mode').checked && (show.cues.length || show.music))) {
    $('concert-mode').checked = !!show?.programme;
    message('Use an empty presentation for a concert programme. Keep the existing timeline as a separate set.', true);
    return;
  }
  if (!$('concert-mode').checked && !confirm('Remove the concert links from this presentation? The linked sets themselves will be kept.')) {
    $('concert-mode').checked = true;
    return;
  }
  show.programme = $('concert-mode').checked ? { sets: [], placeholderId: null } : null;
  formDirty = false;
  concertPreview = { cues: [] };
  changed();
  renderProgramme();
};
function renderMusic() {
  if (!show) return;
  const sectionValue = $('music-section').value;
  const cueValue = $('music-cue').value;
  if (!show.music?.events.some(event => event.id === musicEditing)) musicEditing = null;
  $('add-music-event').textContent = musicEditing ? 'Apply musical event changes' : 'Add musical event';
  $('cancel-music-event').hidden = !musicEditing;
  $('music-track').replaceChildren(element('option', 'No linked click track'));
  $('music-track').firstChild.value = '';
  for (const track of musicTracks) {
    const option = element('option', track.title);
    option.value = track.id;
    $('music-track').append(option);
  }
  $('music-track').value = show.music?.trackId || '';
  $('music-event-fields').hidden = !show.music;
  const track = musicTracks.find(track => track.id === show.music?.trackId);
  $('music-review').textContent = show.music && (!track || track.signature !== show.music.signature)
    ? 'Track sections have changed or the track is missing. Reload/review the positions before saving or broadcasting.'
    : 'Linked events use saved musical sections. Edits in another tab require Reload tracks and a review.';
  $('music-section').replaceChildren();
  for (const [index, section] of (track?.sections || []).entries()) {
    const option = element('option', (index + 1) + '. ' + (section.name || section.type));
    option.value = index;
    $('music-section').append(option);
  }
  if ([...$('music-section').options].some(option => option.value === sectionValue)) $('music-section').value = sectionValue;
  $('music-cue').replaceChildren();
  for (const cue of show.cues.filter(cue => ['picture', 'text'].includes(cue.lane))) {
    const option = element('option', cue.name + ' (' + cue.start + 's)');
    option.value = cue.id;
    $('music-cue').append(option);
  }
  if ([...$('music-cue').options].some(option => option.value === cueValue)) $('music-cue').value = cueValue;
  musicSectionFields();
  musicVideoFields();
  $('music-events').replaceChildren();
  for (const event of show.music?.events || []) {
    const row = element('li', `Section ${event.section + 1}, bar ${event.bar}, beat ${event.beat} → ${show.cues.find(cue => cue.id === event.cueId)?.name || 'Missing cue'}${event.mediaTime == null ? '' : ' at source ' + event.mediaTime + 's'} (${event.mode}) `);
    const edit = element('button', 'Edit event');
    edit.onclick = () => {
      musicEditing = event.id;
      $('music-section').value = event.section;
      $('music-cue').value = event.cueId;
      $('music-bar').value = event.bar;
      $('music-beat').value = event.beat;
      $('music-mode').value = event.mode;
      $('music-media-time').value = event.mediaTime ?? '';
      renderMusic();
    };
    const remove = element('button', 'Remove event');
    remove.onclick = () => {
      show.music.events = show.music.events.filter(item => item.id !== event.id);
      changed();
      renderMusic();
    };
    row.append(edit, remove);
    $('music-events').append(row);
  }
  function musicVideoFields() {
    const cue = show?.cues.find(cue => cue.id === $('music-cue').value);
    const video = cue && ['video', 'youtube'].includes(cue.kind);
    $('music-video-fields').hidden = !video;
    if (!video) return;
    $('music-media-time').min = cue.offset;
    $('music-media-time').max = (cue.offset + cue.duration - 0.001).toFixed(3);
    $('music-media-time').placeholder = String(cue.offset);
    $('music-video-zero').textContent = `${cue.name}: source ${cue.offset}s is timeline ${cue.start}s. Excerpt ends at source ${(cue.offset + cue.duration).toFixed(3)}s.`;
  }
  $('music-cue').onchange = () => { $('music-media-time').value = ''; musicVideoFields(); };
  $('cancel-music-event').onclick = () => { musicEditing = null; $('music-media-time').value = ''; renderMusic(); };
  $('capture-music-hit').onclick = () => {
    const cue = show.cues.find(cue => cue.id === $('music-cue').value);
    const position = previewNow().position;
    if (!cue || position < cue.start || position >= cue.start + cue.duration) {
      message('Scrub the preview inside the selected video cue before capturing its hit time.', true); return;
    }
    $('music-media-time').value = (cue.offset + position - cue.start).toFixed(3);
  };
  $('preview-music-hit').onclick = () => {
    const cue = show.cues.find(cue => cue.id === $('music-cue').value);
    if (!cue || !$('music-media-time').reportValidity()) return;
    seekPreview(cue.start + ($('music-media-time').value === '' ? 0 : Number($('music-media-time').value) - cue.offset));
  };
}
function musicSectionFields() {
  const section = musicTracks.find(track => track.id === show?.music?.trackId)?.sections[Number($('music-section').value)];
  $('music-bar').max = section?.type === 'click' ? section.bars : 1;
  $('music-beat').max = section?.type === 'click' ? section.num : 1;
  $('music-hold').value = show?.music?.holds?.[$('music-section').value] || 'pause';
}
$('music-section').onchange = musicSectionFields;
$('music-track').onchange = () => {
  if (show.music?.events.length && !confirm('Replace this musical link and remove its mapped events?')) { renderMusic(); return; }
  const track = musicTracks.find(track => track.id === Number($('music-track').value));
  show.music = track ? { trackId: track.id, signature: track.signature, events: [], holds: {} } : null;
  changed();
  renderMusic();
};
$('reload-music').onclick = async () => {
  try {
    ({ tracks: musicTracks } = await api('music-tracks'));
    const track = musicTracks.find(track => track.id === show.music?.trackId);
    if (track && track.signature !== show.music.signature &&
        confirm('The musical sections changed. Keep existing event positions for review against the new sections? Review each event before saving.')) {
      show.music.signature = track.signature;
      changed();
    }
    renderMusic();
  } catch (error) { message('Could not reload click tracks: ' + error.message, true); }
};
$('create-music-track').onclick = async () => {
  if (show.music && !confirm('Create a new track and replace this musical link and its mapped events?')) return;
  $('create-music-track').disabled = true;
  try {
    const response = await fetch('/live/api/tracks', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: $('show-title').value + ' - click track',
        sections: [{ name: 'Opening', bpm: 120, num: 4, den: 4, bars: 4, countIn: 0 }] }) });
    const track = await response.json();
    if (!response.ok) throw new Error(track.error || 'Could not create track.');
    ({ tracks: musicTracks } = await api('music-tracks'));
    const saved = musicTracks.find(item => item.id === track.id);
    if (!saved) throw new Error('The track was created but could not be reloaded. Reload tracks before trying again.');
    show.music = { trackId: saved.id, signature: saved.signature, events: [], holds: {} };
    changed();
    renderMusic();
    message('Click track created and linked locally. Edit it in Click Tracks, then reload/review music links here and save.');
  } catch (error) { message('Could not create linked track: ' + error.message, true); }
  finally { $('create-music-track').disabled = false; }
};
$('add-music-event').onclick = () => {
  if (!show.music || !['music-bar', 'music-beat'].every(id => $(id).reportValidity())) return;
  const event = { id: musicEditing || newId(), section: Number($('music-section').value), bar: Number($('music-bar').value),
    beat: Number($('music-beat').value), cueId: $('music-cue').value, mode: $('music-mode').value };
  if (!event.cueId) { message('Add a picture or text cue before linking musical events.', true); return; }
  if (!$('music-video-fields').hidden && $('music-media-time').value !== '') {
    if (!$('music-media-time').reportValidity()) return;
    event.mediaTime = Number($('music-media-time').value);
  }
  if (show.music.events.some(item => item.id !== event.id && item.section === event.section && item.bar === event.bar && item.beat === event.beat)) {
    message('There is already an event at this musical position. Remove it before adding its replacement.', true); return;
  }
  show.music.events = [...show.music.events.filter(item => item.id !== event.id), event];
  musicEditing = null;
  changed();
  renderMusic();
};
$('music-hold').onchange = () => {
  if (!show.music) return;
  show.music.holds[$('music-section').value] = $('music-hold').value;
  changed();
};
$('add-concert-set').onclick = () => {
  const id = Number($('concert-set').value);
  if (!id) { message('Create a separate set presentation first, then reload this editor.', true); return; }
  show.programme.sets.push(id);
  changed();
  renderProgramme();
};
$('concert-placeholder').onchange = () => {
  show.programme.placeholderId = Number($('concert-placeholder').value) || null;
  changed();
};
$('load-concert-preview').onclick = async () => {
  if (dirty || formDirty) { message('Save the concert before loading its preview.', true); return; }
  try {
    concertPreview = await api('shows/' + config.id + '/compiled');
    $('preview-time').max = duration(concertPreview.cues);
    seekPreview(0);
    message('Concert preview loaded from the latest saved sets. Intervals wait for Next.');
  } catch (error) { message('Could not load concert: ' + error.message, true); }
};
function uploadControls(busy) {
  uploading = busy;
  for (const id of ['media-files', 'media-folder', 'powerpoint-file', 'run-import', 'import-select-all',
    'import-select-none', 'import-to-timeline', 'import-image-duration']) $(id).disabled = busy;
  $('run-import').disabled = busy || !importItems.some(item => item.checkbox.checked && !item.asset);
  for (const item of importItems) item.checkbox.disabled = busy || !!item.asset;
}
async function uploadFile(file, pending = null) {
  if (!file.size || file.size > 100 * 1024 * 1024) throw new Error(file.name + ' must contain between 1 byte and 100 MB.');
  let publicId = pending?.publicId;
  if (!publicId) {
    const signed = await api('uploads/sign', 'POST', { name: file.name, size: file.size });
    const data = new FormData();
    data.append('file', file);
    for (const [key, value] of Object.entries(signed.parameters)) data.append(key, value);
    const response = await fetch(signed.endpoint, { method: 'POST', body: data, credentials: 'omit' });
    if (!response.ok) {
      const detail = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
      throw new Error(detail?.error?.message || 'Cloudinary upload failed (HTTP ' + response.status + ').');
    }
    const stored = await response.json();
    if (!stored.public_id) throw new Error('Cloudinary did not return the uploaded asset ID.');
    publicId = stored.public_id;
    if (pending) pending.publicId = publicId;
  }
  let asset;
  try { asset = await api('assets', 'POST', { publicId, name: file.name }); }
  catch (error) {
    if (pending && [400, 404].includes(error.status)) pending.publicId = null;
    throw error;
  }
  if (!assets.some(item => item.id === asset.id)) assets.unshift(asset);
  renderAssets();
  return asset;
}
async function upload(files) {
  if (uploading) { message('Wait for the current import/upload to finish.', true); return; }
  uploadControls(true);
  try {
    for (const file of files) {
      message('Uploading ' + file.name + ' to Cloudinary…');
      await uploadFile(file);
    }
    message('Uploads saved in your media library. Drag them to the timeline or choose Add.');
  } catch (error) { message('Upload stopped: ' + error.message, true); }
  finally { uploadControls(false); $('media-files').value = ''; }
}
async function prepareImport(source) {
  if (uploading) { message('Wait for the current import/upload to finish.', true); return; }
  uploadControls(true);
  importItems = [];
  $('import-files').replaceChildren();
  $('import-notes').replaceChildren();
  $('import-notices').hidden = true;
  message('Reading import in this browser...', false, 'import-status');
  try {
    const result = await source();
    const notes = [...result.skipped.map(name => 'Unsupported/skipped: ' + name), ...(result.warnings || [])];
    $('import-notices').hidden = !notes.length;
    for (const note of notes) $('import-notes').append(element('li', note));
    importItems = result.files.map(item => {
      const row = element('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = true;
      const status = element('span', item.label + ' (' + (item.file.size / 1024 / 1024).toFixed(2) + ' MB)');
      checkbox.onchange = () => uploadControls(false);
      row.append(checkbox, status);
      $('import-files').append(row);
      return { ...item, checkbox, status, asset: null };
    });
    message(importItems.length ? `${importItems.length} assets ready to review. ${result.skipped.length} unsupported/skipped files. Nothing uploaded yet.`
      : 'No supported embedded media/assets found. Export PowerPoint slides as PNGs if you need slide pictures.', !importItems.length, 'import-status');
    $('import-to-timeline').checked = false;
  } catch (error) { message('Could not read import: ' + error.message, true, 'import-status'); }
  finally {
    uploadControls(false);
    $('powerpoint-file').value = $('media-folder').value = '';
  }
}
$('powerpoint-file').onchange = event => {
  const file = event.target.files[0];
  if (file) prepareImport(() => powerpointFiles(file));
};
$('media-folder').onchange = event => {
  const files = [...event.target.files];
  if (files.length) prepareImport(() => folderFiles(files));
};
for (const [id, checked] of [['import-select-all', true], ['import-select-none', false]]) {
  $(id).onclick = () => {
    for (const item of importItems) if (!item.asset) item.checkbox.checked = checked;
    uploadControls(false);
  };
}
$('stop-import').onclick = () => { importStop = true; message('Stopping after the current file finishes...', false, 'import-status'); };
$('run-import').onclick = async () => {
  if (uploading) return;
  const selectedItems = importItems.filter(item => item.checkbox.checked && !item.asset);
  if (!selectedItems.length) { message('Select assets to import.', true, 'import-status'); return; }
  const append = $('import-to-timeline').checked, imageDuration = Number($('import-image-duration').value);
  if (append && (!$('import-image-duration').reportValidity() || !canLeaveCue())) return;
  if (append && show.programme) { message('Import to the library, or open a standalone set to append timeline cues.', true, 'import-status'); return; }
  if (append) {
    const pictures = selectedItems.filter(item => !/\.(mp3|wav|m4a|ogg)$/i.test(item.file.name));
    if (show.cues.length + pictures.length > 500) { message('Appending these assets would exceed 500 timeline cues.', true, 'import-status'); return; }
  }
  uploadControls(true);
  importStop = false;
  $('stop-import').hidden = false;
  const imported = [];
  let completed = 0, failure = null;
  try {
    for (const item of selectedItems) {
      if (importStop) break;
      message(`Uploading ${completed + 1} of ${selectedItems.length}: ${item.label}`, false, 'import-status');
      try {
        item.asset = await uploadFile(item.file, item);
        imported.push(item.asset);
        item.file = null;
        item.status.textContent = item.label + ' — imported';
        completed += 1;
      } catch (error) {
        item.status.textContent = item.label + ' — failed: ' + error.message;
        throw error;
      }
    }
  } catch (error) { failure = error.message; }
  finally {
    if (append && imported.length) {
      try {
        if (show.programme) throw new Error('The presentation is now a concert programme. Append media to one of its standalone sets.');
        let start = endOfLane('picture');
        const cues = imported.filter(asset => asset.kind !== 'audio').map(asset => {
          const seconds = asset.kind === 'image' ? imageDuration : asset.duration;
          const cue = { id: newId(), name: asset.name.slice(0, 120), kind: asset.kind, lane: 'picture', assetId: asset.id,
            start, duration: seconds, offset: 0, volume: 1, muted: true, transition: 'cut', fade: 0, fadeOut: 0, advance: 'auto' };
          start += seconds;
          return cue;
        });
        validate([...show.cues, ...cues]);
        show.cues.push(...cues);
        if (cues.length) {
          changed();
          if (formDirty) renderTimeline();
          else select(cues[0].id);
        }
      } catch (error) { failure = (failure ? failure + ' ' : '') + 'Assets are in the library, but timeline append failed: ' + error.message; }
    }
    const total = importItems.filter(item => item.asset).length;
    message(`${completed} imported this run (${total} total). ` + (failure ? 'Import stopped: ' + failure + '. Retry skips completed uploads; failed/unfinalised uploads may need cleaning in Cloudinary.'
      : importStop ? 'Stopped. Import selected assets again to continue without re-uploading completed files.'
        : append ? 'Timeline sequence added locally where possible; Save presentation to keep it. Audio is library-only.'
          : 'Assets saved in the library. Drag them to a lane or choose Add.'), !!failure, 'import-status');
    uploadControls(false);
    $('stop-import').hidden = true;
  }
};
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
    message('YouTube video added. Set its duration and in point on the timeline; full-length videos are supported within the 24-hour timeline.');
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
};
function snappedStart(raw, cue, bypass) {
  const start = Math.max(0, Math.round(raw * 1000) / 1000);
  if (!$('timeline-snap').checked || bypass) return start;
  const edges = [0, previewNow().position, ...show.cues.filter(item => item.id !== cue.id).flatMap(item => [item.start, item.start + item.duration])];
  const tolerance = 8 / Number($('timeline-zoom').value);
  const options = edges.flatMap(edge => [edge, edge - cue.duration]).filter(value =>
    value >= 0 && Math.abs(value - start) <= tolerance &&
    !overlapError([...show.cues.filter(item => item.id !== cue.id), { ...cue, start: value }]));
  options.sort((a, b) => Math.abs(a - start) - Math.abs(b - start));
  return Math.round((options[0] ?? start) * 1000) / 1000;
}
document.querySelectorAll('.lane').forEach(lane => {
  lane.ondragover = event => event.preventDefault();
  lane.ondrop = event => {
    event.preventDefault();
    if (!canLeaveCue()) return;
    const raw = (event.clientX - lane.getBoundingClientRect().left) / Number($('timeline-zoom').value);
    const cueId = event.dataTransfer.getData('application/x-studio-cue');
    if (cueId) {
      const cue = show.cues.find(cue => cue.id === cueId);
      if (!cue || cue.lane !== lane.dataset.lane) { message('Move this cue within its own lane.', true); return; }
      const start = snappedStart(raw, cue, event.shiftKey);
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
      const seconds = ['video', 'audio'].includes(asset.kind) ? asset.duration : asset.kind === 'youtube' ? 10 : 5;
      add(asset.kind, asset, expected, snappedStart(raw, { lane: expected, duration: seconds }, event.shiftKey));
    }
  };
});
$('add-text').onclick = () => add('text', null, 'text', previewNow().position);
$('add-blank').onclick = () => add('blank', null, 'picture', endOfLane('picture'));
$('timeline-zoom').oninput = renderTimeline;
form.oninput = () => { formDirty = true; message('Cue properties changed. Choose Apply cue changes to update the timeline.'); };
function volumeLabel() {
  $('cue-volume-value').textContent = Math.round(Number(form.elements.volume.value) * 100) + '%';
}
form.elements.volume.oninput = () => {
  if (Number(form.elements.volume.value) > 0) $('cue-muted').checked = false;
  volumeLabel();
};
$('cue-muted').onchange = () => {
  if (!$('cue-muted').checked && Number(form.elements.volume.value) === 0) {
    form.elements.volume.value = 1;
    volumeLabel();
  }
};
form.onsubmit = event => {
  event.preventDefault();
  const cue = show.cues.find(cue => cue.id === selected);
  const candidate = { ...cue };
  for (const [key, value] of new FormData(form)) {
    if (!form.elements[key].closest('[hidden]')) candidate[key] = numeric.has(key) ? Number(value) : value;
  }
  if (!$('cue-media-fields').hidden) candidate.muted = $('cue-muted').checked;
  candidate.marker = cue.lane !== 'audio' && $('cue-marker').checked;
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
  if (show.music) show.music.events = show.music.events.filter(event => event.cueId !== selected);
  formDirty = false;
  changed();
  select(null);
};
$('show-title').oninput = changed;
function restoreEdit(from, to) {
  if (!show || !from.length || !canLeaveCue()) return;
  to.push(editSnapshot());
  const value = from.pop();
  show.cues = structuredClone(value.cues);
  show.programme = structuredClone(value.programme);
  show.music = structuredClone(value.music);
  concertPreview = { cues: [] };
  $('show-title').value = value.title;
  lastEdit = editSnapshot();
  dirty = true;
  editVersion += 1;
  compiledShow = null;
  formDirty = false;
  seekPreview(Math.min(previewNow().position, duration(show.cues)));
  select(show.cues.some(cue => cue.id === selected) ? selected : null);
  historyButtons();
  renderProgramme();
  message('Edit restored. Save presentation to keep this version.');
}
$('undo-edit').onclick = () => restoreEdit(undo, redo);
$('redo-edit').onclick = () => restoreEdit(redo, undo);
window.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target.closest('input, textarea, select, [contenteditable=true]')) return;
  if (event.key.toLowerCase() === 'z' || event.key.toLowerCase() === 'y') {
    event.preventDefault();
    restoreEdit(event.shiftKey || event.key.toLowerCase() === 'y' ? redo : undo,
      event.shiftKey || event.key.toLowerCase() === 'y' ? undo : redo);
  }
});
function transformSelected(transform) {
  if (!canLeaveCue()) return;
  const cue = show.cues.find(item => item.id === selected);
  if (!cue) return;
  try {
    const { cues, id, music } = transform(cue);
    validate(cues);
    show.cues = cues;
    if (music) show.music = music;
    formDirty = false;
    changed();
    select(id);
  } catch (error) { message(error.message, true); }
}
$('duplicate-cue').onclick = () => transformSelected(cue => {
  const copy = { ...cue, id: newId(), name: (cue.name.slice(0, 113) + ' (copy)'), start: endOfLane(cue.lane) };
  return { cues: [...show.cues, copy], id: copy.id };
});
$('split-cue').onclick = () => transformSelected(cue => {
  const time = Math.round(previewNow().position * 1000) / 1000;
  const firstDuration = Math.round((time - cue.start) * 1000) / 1000;
  const secondDuration = Math.round((cue.duration - firstDuration) * 1000) / 1000;
  if (firstDuration < 0.1 || secondDuration < 0.1) throw new Error('Place the playhead inside this cue, at least 0.1 seconds from either end.');
  const first = { ...cue, duration: firstDuration, fade: Math.min(cue.fade, firstDuration), fadeOut: 0, advance: 'auto' };
  const second = { ...cue, id: newId(), start: time, duration: secondDuration, transition: 'cut', fade: 0,
    fadeOut: Math.min(cue.fadeOut, secondDuration),
    offset: ['video', 'youtube', 'audio'].includes(cue.kind) ? Math.round((cue.offset + firstDuration) * 1000) / 1000 : cue.offset };
  const music = show.music ? { ...show.music, events: show.music.events.map(event =>
    event.cueId === cue.id && event.mediaTime != null && event.mediaTime >= second.offset
      ? { ...event, cueId: second.id } : event) } : null;
  return { cues: show.cues.flatMap(item => item === cue ? [first, second] : [item]), id: second.id, music };
});
$('save-show').onclick = async () => {
  if (formDirty) { message('Apply cue changes first, then save the presentation.', true); return; }
  const button = $('save-show');
  button.disabled = true;
  const savingVersion = editVersion;
  try {
    const saved = await api('shows/' + config.id, 'PUT', {
      title: $('show-title').value, cues: show.cues, programme: show.programme, music: show.music, revision: show.revision,
    });
    if (editVersion === savingVersion) { show = saved; compiledShow = null; dirty = false; lastEdit = editSnapshot(); }
    else show.revision = saved.revision;
    message(dirty || formDirty ? 'Saved the earlier version. New edits remain unsaved.' : 'Presentation saved. Restart a running broadcast to use this version.');
  } catch (error) { message('Not saved: ' + error.message, true); }
  finally { button.disabled = false; }
};
function previewNow() { return limitPosition(compiled().cues, preview.position, (performance.now() - preview.at) / 1000, preview.playing); }
function seekPreview(position) {
  if (!show) return;
  preview = { position: Math.max(0, Math.min(duration(compiled().cues), position)), playing: false, at: performance.now() };
}
for (const id of ['time-ruler', 'timeline-playhead']) {
  const target = $(id);
  const scrub = event => seekPreview((event.clientX - $('timeline').getBoundingClientRect().left) / Number($('timeline-zoom').value));
  target.onpointerdown = event => {
    if (event.button !== 0 || !show) return;
    event.preventDefault();
    target.setPointerCapture(event.pointerId);
    scrub(event);
  };
  target.onpointermove = event => { if (target.hasPointerCapture(event.pointerId)) scrub(event); };
  target.onpointerup = event => {
    if (!target.hasPointerCapture(event.pointerId)) return;
    scrub(event);
    target.releasePointerCapture(event.pointerId);
  };
}
$('timeline-scrub').oninput = event => seekPreview(Number(event.target.value));
$('preview-play').onclick = () => {
  const now = previewNow();
  preview = { position: now.position >= duration(compiled().cues) - 0.002 ? 0 : now.position,
    playing: !now.playing, at: performance.now() };
};
$('preview-time').onchange = event => {
  if (!event.target.reportValidity()) return;
  seekPreview(Number(event.target.value));
};
$('preview-next').onclick = () => {
  const now = previewNow();
  const next = compiled().cues.filter(cue => cue.lane === 'picture' && cue.start > now.position + 0.0001).sort((a, b) => a.start - b.start)[0];
  if (next) preview = { position: next.start, playing: true, at: performance.now() };
};
window.addEventListener('beforeunload', event => { if (dirty || formDirty) { event.preventDefault(); event.returnValue = ''; } });
async function init() {
  try {
    [show, { assets }, { shows: availableShows }, { tracks: musicTracks }] = await Promise.all([
      api('shows/' + config.id), api('assets'), api('shows'), api('music-tracks')]);
    lastEdit = editSnapshot();
    renderAssets();
    renderTimeline();
    message('Drag media onto the timeline, or add text and black cues below.');
    function frame() {
      const now = previewNow();
      player.render(compiled(), now.position, now.playing);
      $('preview-play').textContent = now.playing ? 'Pause preview' : 'Play preview';
      if (document.activeElement !== $('preview-time')) $('preview-time').value = now.position.toFixed(1);
      $('timeline-scrub').value = now.position;
      $('timeline-position').textContent = now.position.toFixed(1) + ' s';
      $('timeline-playhead').style.left = now.position * Number($('timeline-zoom').value) + 'px';
      requestAnimationFrame(frame);
    }
    frame();
  } catch (error) { message('Could not open presentation: ' + error.message, true); }
}
init();
setupPreflight(async () => {
  if (!show || formDirty) throw new Error('Apply cue changes before running checks.');
  if (show.programme) {
    if (dirty) throw new Error('Save concert changes before running checks.');
    return api('shows/' + config.id + '/readiness');
  }
  return { ...compiled(), readinessWarnings: dirty ? ['Unsaved editor changes: save before performance.'] : [] };
});
