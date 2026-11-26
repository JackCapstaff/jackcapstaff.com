export const config = window.PRESENTATION_CONFIG;
export const $ = id => document.getElementById(id);
export function message(text, error = false, id = 'status') {
  const element = $(id);
  element.textContent = text;
  element.dataset.error = String(error);
}
export async function api(path, method = 'GET', data) {
  const response = await fetch('/presentations/api/' + path, {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!response.ok) {
    const text = response.headers.get('content-type')?.includes('application/json')
      ? (await response.json()).error : 'HTTP ' + response.status;
    throw new Error(text || 'Request failed.');
  }
  return response.json();
}
export function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export function duration(cues) {
  return Math.max(0, ...cues.map(cue => cue.start + cue.duration));
}
export function overlapError(cues) {
  for (const lane of ['picture', 'text', 'audio']) {
    const items = cues.filter(cue => cue.lane === lane).sort((a, b) => a.start - b.start);
    for (let index = 1; index < items.length; index++) {
      const cue = items[index], previous = items[index - 1];
      if (previous.start + previous.duration <= cue.start + 0.0001) continue;
      if (cue.start <= previous.start + 0.0001 ||
          cue.start + cue.duration <= previous.start + previous.duration + 0.0001 ||
          (index > 1 && items[index - 2].start + items[index - 2].duration > cue.start + 0.0001)) {
        return 'Use partial overlaps between two cues in the ' + lane + ' lane, not contained cues or three-way overlaps.';
      }
      return null;
    }
  }
}
export function limitPosition(cues, base, elapsed, playing, boundary = Infinity) {
  const end = duration(cues);
  const holds = cues.filter(cue => cue.lane === 'picture' && cue.advance === 'manual'
    && cue.start + cue.duration > base + 0.002).map(cue => cue.start + cue.duration);
  const limit = Math.min(end, boundary, ...holds);
  const position = base + (playing ? elapsed : 0);
  return { position: Math.max(0, Math.min(position, limit - (playing && position >= limit ? 0.001 : 0))),
    playing: playing && position < limit };
}
