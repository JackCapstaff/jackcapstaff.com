import { element } from './common.js';

let youtube;
function loadYoutube() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (!youtube) {
    youtube = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timer = setTimeout(() => reject(new Error('YouTube did not load. Check the connection.')), 12000);
      window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); resolve(window.YT); };
      script.onerror = () => { clearTimeout(timer); reject(new Error('YouTube could not load.')); };
      script.src = 'https://www.youtube.com/iframe_api';
      document.head.append(script);
    });
  }
  return youtube;
}

export class PresentationPlayer {
  constructor(host, status, unlock) {
    this.host = host;
    this.status = status;
    this.unlock = unlock;
    this.layers = new Map();
    this.signature = '';
    this.source = null;
    this.lanes = new Map();
    this.muted = false;
    this.blocked = false;
    unlock.onclick = () => {
      this.blocked = false;
      unlock.hidden = true;
      this.status.textContent = '';
      for (const layer of this.layers.values()) layer.lastPlay = 0;
      this.render(this.lastShow, this.lastPosition, this.lastPlaying, this.lastBlack);
    };
  }
  error(message, blocked = false) {
    this.status.textContent = message;
    this.status.dataset.error = 'true';
    if (blocked) {
      this.blocked = true;
      this.unlock.hidden = false;
    }
  }
  clear() {
    for (const layer of this.layers.values()) {
      layer.disposed = true;
      clearTimeout(layer.timer);
      if (layer.video) { layer.video.pause(); layer.video.removeAttribute('src'); layer.video.load(); }
      layer.yt?.destroy();
      layer.node.remove();
    }
    this.layers.clear();
  }
  create(cue) {
    const node = element('div', undefined, 'media-layer');
    if (cue.lane === 'picture') node.style.background = '#000';
    const layer = { node, cue, disposed: false, lastSeek: 0, lastPlay: 0, ready: false };
    node.dataset.cue = cue.id;
    this.host.append(node);
    this.layers.set(cue.id, layer);
    const fail = message => {
      if (!layer.disposed) {
        layer.error = message;
        this.error(cue.name + ': ' + message);
      }
    };
    if (cue.kind === 'text') {
      node.classList.add('caption', cue.position);
      node.style.color = cue.color;
      node.style.fontSize = cue.fontSize + 'cqh';
      node.append(element('span', cue.text));
      layer.ready = true;
    } else if (cue.kind === 'blank') {
      node.style.background = '#000';
      layer.ready = true;
    } else if (cue.kind === 'image') {
      const image = new Image();
      image.alt = '';
      image.onload = () => { layer.ready = true; clearTimeout(layer.timer); };
      image.onerror = () => fail('Image could not load.');
      image.src = cue.asset.url;
      node.append(image);
    } else if (cue.kind === 'youtube') {
      loadYoutube().then(yt => {
        if (layer.disposed) return;
        const frame = element('iframe');
        frame.title = cue.name;
        frame.allow = 'autoplay; encrypted-media; fullscreen';
        frame.referrerPolicy = 'strict-origin-when-cross-origin';
        frame.src = 'https://www.youtube-nocookie.com/embed/' + cue.asset.url + '?' + new URLSearchParams({
          enablejsapi: '1', playsinline: '1', controls: '0', disablekb: '1', origin: location.origin,
        });
        node.append(frame);
        layer.yt = new yt.Player(frame, { events: {
          onReady: () => { layer.ready = true; clearTimeout(layer.timer); },
          onAutoplayBlocked: () => { if (!layer.disposed) this.error('Select Allow media playback and sound.', true); },
          onError: ({ data }) => fail('YouTube playback error ' + data + '.'),
        } });
      }).catch(error => fail(error.message));
    } else {
      const video = document.createElement(cue.kind === 'audio' ? 'audio' : 'video');
      layer.video = video;
      video.preload = 'auto';
      video.playsInline = true;
      video.muted = true;
      video.onloadedmetadata = () => { layer.ready = true; clearTimeout(layer.timer); };
      video.onerror = () => fail('Media could not load or the browser does not support its format.');
      video.src = cue.asset.url;
      node.append(video);
    }
    if (!layer.ready) layer.timer = setTimeout(() => fail('Media is still loading. Check the connection and media URL.'), 15000);
    return layer;
  }
  render(show = { cues: [] }, position = 0, playing = false, black = false, placeholder = null, frozen = false) {
    if (placeholder) {
      show = { cues: [{ id: 'placeholder-' + placeholder.id, name: placeholder.name, lane: 'picture', kind: 'image',
        start: 0, duration: 86400, offset: 0, volume: 0, transition: 'cut', fade: 0, fadeOut: 0, asset: placeholder }] };
      position = 0;
      playing = false;
    }
    this.lastShow = show;
    this.lastPosition = position;
    this.lastPlaying = playing;
    this.lastBlack = black;
    const signature = show === this.source ? this.signature : JSON.stringify(show);
    this.source = show;
    if (signature !== this.signature) {
      this.clear();
      this.signature = signature;
      this.status.textContent = '';
      this.lanes = new Map(['picture', 'audio', 'text'].map(lane => [
        lane, show.cues.filter(cue => cue.lane === lane).sort((a, b) => a.start - b.start),
      ]));
    }
    const wanted = new Map();
    for (const [lane, cues] of this.lanes) {
      const index = cues.findLastIndex(cue => position >= cue.start && position < cue.start + cue.duration);
      const active = cues[index];
      if (active) {
        const elapsed = position - active.start;
        const previous = cues[index - 1];
        const overlap = previous ? previous.start + previous.duration - active.start : 0;
        const blending = !frozen && overlap > 0.0001 && elapsed < overlap;
        const fade = frozen ? 1 : overlap > 0.0001 ? Math.min(1, elapsed / overlap)
          : active.transition !== 'cut' && active.fade > 0 ? Math.min(1, elapsed / active.fade) : 1;
        const out = active.fadeOut > 0 ? Math.min(1, (active.duration - elapsed) / active.fadeOut) : 1;
        if (blending) {
          wanted.set(previous.id, { cue: previous, time: previous.offset + position - previous.start,
            playing, opacity: lane === 'picture' ? 1 : 1 - fade,
            volume: previous.volume * (1 - fade), z: lane === 'text' ? 3 : 1 });
        } else if (active.transition === 'crossfade' && fade < 1 && previous
          && Math.abs(previous.start + previous.duration - active.start) < 0.002 && lane !== 'audio') {
          wanted.set(previous.id, { cue: previous, time: previous.offset + previous.duration - 0.03,
            playing: false, opacity: lane === 'text' ? (1 - fade) * out : out, volume: 0, z: lane === 'text' ? 3 : 1 });
        }
        wanted.set(active.id, { cue: active, time: active.offset + elapsed, playing,
          opacity: fade * out, volume: active.volume * fade * out, z: lane === 'text' ? 4 : 2 });
      }
      const upcoming = cues.find(cue => cue.start > position && cue.start <= position + 3);
      if (upcoming) wanted.set(upcoming.id, { cue: upcoming, time: upcoming.offset, playing: false,
        opacity: 0, volume: 0, z: 0 });
    }
    for (const [id, layer] of this.layers) {
      if (!wanted.has(id)) {
        layer.disposed = true;
        clearTimeout(layer.timer);
        layer.video?.pause();
        if (layer.video) { layer.video.removeAttribute('src'); layer.video.load(); }
        layer.yt?.destroy();
        layer.node.remove();
        this.layers.delete(id);
      }
    }
    for (const state of wanted.values()) {
      const layer = this.layers.get(state.cue.id) || this.create(state.cue);
      layer.node.style.opacity = String(black ? 0 : state.opacity);
      layer.node.style.zIndex = String(state.z);
      const volume = this.muted || black || state.cue.muted ? 0 : state.volume;
      if (!layer.ready) continue;
      const now = performance.now();
      if (layer.video) {
        const video = layer.video;
        video.muted = volume === 0;
        video.volume = volume;
        const target = Math.min(state.time, Math.max(0, video.duration - 0.02));
        if (Math.abs(video.currentTime - target) > (state.playing ? 0.35 : 0.03) && now - layer.lastSeek > 700) {
          video.currentTime = target;
          layer.lastSeek = now;
        }
        if (!state.playing) video.pause();
        else if (video.paused && now - layer.lastPlay > 1000 && !this.blocked) {
          layer.lastPlay = now;
          video.play().catch(error => {
            if (layer.disposed || error.name === 'AbortError') return;
            this.error('Playback could not start: ' + error.message + '. Select Allow media playback.', true);
          });
        }
      } else if (layer.yt) {
        const yt = layer.yt;
        if (layer.volume !== volume) {
          if (volume === 0) yt.mute(); else { yt.setVolume(volume * 100); yt.unMute(); }
          layer.volume = volume;
        }
        if (Math.abs(yt.getCurrentTime() - state.time) > (state.playing ? 0.7 : 0.05)
          && now - layer.lastSeek > 900) {
          yt.seekTo(state.time, true);
          layer.lastSeek = now;
        }
        const code = yt.getPlayerState();
        if (!state.playing && code !== 2 && code !== -1) yt.pauseVideo();
        else if (state.playing && code !== 1 && code !== 3 && now - layer.lastPlay > 1000 && !this.blocked) {
          layer.lastPlay = now;
          yt.playVideo();
        }
      }
    }
  }
}
