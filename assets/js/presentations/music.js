// Musical events are ordered by section, then bar and meter unit, not score bars.
export function musicalEvent(music, position) {
  const at = event => [event.section, event.bar, event.beat];
  const compare = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  return music.events.filter(event => compare(at(event), at(position)) <= 0)
    .sort((a, b) => compare(at(b), at(a)))[0] || null;
}

export function musicalVisualState(music, position) {
  const running = position.playing && !position.suspended;
  const waiting = position.phase === 'pause';
  const countIn = ['countin', 'intro', 'stopped'].includes(position.phase);
  const event = countIn ? null : musicalEvent(music, position);
  const playing = running && !countIn && (!waiting || music.holds[String(position.section)] === 'continue');
  return { event, playing };
}
