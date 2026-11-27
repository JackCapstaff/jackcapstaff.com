export function applyOutput(host, output = {}, black = false) {
  const aspect = output.aspect || '16:9';
  const ratio = aspect === 'screen' ? null : Number(aspect.split(':')[0]) / Number(aspect.split(':')[1]);
  if (document.body.classList.contains('audience') || document.fullscreenElement === host) {
    const width = ratio ? Math.min(innerWidth, innerHeight * ratio) : innerWidth;
    const height = ratio ? width / ratio : innerHeight;
    Object.assign(host.style, { width: width + 'px', height: height + 'px',
      left: (innerWidth - width) / 2 + 'px', top: (innerHeight - height) / 2 + 'px', position: 'absolute' });
  } else Object.assign(host.style, { aspectRatio: ratio ? String(ratio) : '16 / 9',
    width: '', height: '', left: '', top: '', position: '' });
  host.style.transform = `translate(${output.x || 0}%, ${output.y || 0}%) scale(${(output.scale ?? 100) / 100})`;
  let overlay = host.querySelector('.output-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'output-overlay';
    host.append(overlay);
  }
  const pattern = black ? 'off' : output.pattern || 'off';
  overlay.dataset.pattern = pattern;
  overlay.classList.toggle('safe-area', !black && !!output.safeArea);
  overlay.textContent = pattern === 'grid' ? 'CENTRE / OUTPUT CALIBRATION' : '';
}

export function screenHealth(player) {
  const active = [...player.layers.values()].filter(layer => Number(layer.node.style.opacity) > 0);
  return { ready: active.every(layer => layer.ready && !layer.error), blocked: player.blocked,
    visible: document.visibilityState === 'visible',
    error: (active.find(layer => layer.error)?.error || (player.status.dataset.error === 'true' ? player.status.textContent : '')).slice(0, 500) };
}
