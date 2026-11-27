import { $, element, message } from './common.js';

export function setupVisualInput(run) {
  const actions = ['next', 'previous', 'pause', 'play', 'placeholder', 'black', 'mute', 'return'];
  let bindings = {}, learning = null, last = -Infinity, midi = null;
  const held = new Set(), key = 'media-centre-visual-input';
  try {
    bindings = JSON.parse(localStorage.getItem(key) || '{}');
    if (!bindings || typeof bindings !== 'object' || Array.isArray(bindings) ||
      Object.entries(bindings).some(([action, value]) => !actions.includes(action) || typeof value !== 'string')) {
      bindings = {};
      throw new Error('Stored mappings are invalid; learn the inputs again.');
    }
  }
  catch (error) { message('Could not read pedal settings: ' + error.message, true, 'visual-input-status'); }
  function render() {
    $('visual-bindings').replaceChildren();
    for (const action of actions) {
      const group = element('div');
      group.append(element('span', action + ': ' + (bindings[action] || 'unassigned') + ' '));
      const learn = element('button', 'Learn'), clear = element('button', 'Clear');
      learn.onclick = () => { learning = action; message('Press input for ' + action + '.', false, 'visual-input-status'); };
      clear.onclick = () => { delete bindings[action]; save(); };
      group.append(learn, clear);
      $('visual-bindings').append(group);
    }
  }
  function save() {
    try { localStorage.setItem(key, JSON.stringify(bindings)); }
    catch (error) { message('Could not save pedal mapping: ' + error.message, true, 'visual-input-status'); }
    render();
  }
  function receive(value) {
    if (learning) {
      for (const action of actions) if (bindings[action] === value) delete bindings[action];
      bindings[learning] = value;
      learning = null;
      save();
      message('Input assigned.', false, 'visual-input-status');
      return;
    }
    const action = actions.find(action => bindings[action] === value);
    if (!action || !$('visual-input-enabled').checked || performance.now() - last < 300) return;
    last = performance.now();
    run(action);
  }
  window.addEventListener('keydown', event => {
    if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input:not([type=checkbox]),textarea,select,[contenteditable=true]')) return;
    const value = 'key:' + event.code;
    if (learning || $('visual-input-enabled').checked && Object.values(bindings).includes(value)) {
      event.preventDefault();
      receive(value);
    }
  });
  function attach() {
    held.clear();
    for (const input of midi.inputs.values()) input.onmidimessage = event => {
      const [status, number, velocity] = event.data;
      const type = status & 0xf0;
      if (![0x80, 0x90, 0xb0].includes(type)) return;
      const value = 'midi:' + input.id + ':' + (status & 15) + ':' + (type === 0x80 ? 0x90 : type) + ':' + number;
      const down = type === 0x90 && velocity > 0 || type === 0xb0 && velocity >= 64;
      if (!down) held.delete(value);
      else if (!held.has(value)) { held.add(value); receive(value); }
    };
    message(midi.inputs.size + ' MIDI input(s) connected.', false, 'visual-input-status');
  }
  $('enable-visual-midi').onclick = async () => {
    try {
      if (!navigator.requestMIDIAccess) throw new Error('Web MIDI is unavailable. Use Chrome/Edge over HTTPS or a keyboard pedal.');
      midi = await navigator.requestMIDIAccess();
      midi.onstatechange = attach;
      attach();
    } catch (error) { message('MIDI could not connect: ' + error.message, true, 'visual-input-status'); }
  };
  render();
}
