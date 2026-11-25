(() => {
  'use strict';
  const host = document.getElementById('presentation-player');
  const status = document.getElementById('presentation-message');
  const unlock = document.getElementById('presentation-unlock');
  const parent = window.opener;
  const origin = window.location.origin;
  let media = null, player = null, desired = null, sourceKey = null, syncKey = null, generation = 0;
  let youtubeApi = null, lastState = Date.now(), loadTimer = null;
  const send = (type, message = '') => parent?.postMessage({ channel: 'clicktrack-presentation', type, message }, origin);
  const fail = (message) => { status.textContent = message; send('error', message); };
  function loadYoutube() {
    if (window.YT?.Player) return Promise.resolve(window.YT);
    if (!youtubeApi) {
      youtubeApi = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        const timer = setTimeout(() => reject(new Error('YouTube did not load.')), 12000);
        window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); resolve(window.YT); };
        script.onerror = () => { clearTimeout(timer); reject(new Error('YouTube did not load.')); };
        script.src = 'https://www.youtube.com/iframe_api';
        document.head.append(script);
      });
    }
    return youtubeApi;
  }
  function apply() {
    if (!media || !desired) return;
    media.mute(desired.muted);
    const time = media.time();
    if (syncKey !== desired.key || !Number.isFinite(time) || Math.abs(time - desired.time) > 1.5) {
      media.seek(desired.time);
      syncKey = desired.key;
    }
    if (desired.playing) media.play(); else media.pause();
  }
  async function openSource(source) {
    const version = ++generation;
    clearTimeout(loadTimer);
    media?.pause();
    player?.destroy();
    player = media = null;
    syncKey = null;
    host.replaceChildren();
    status.textContent = 'Loading video…';
    loadTimer = setTimeout(() => {
      if (generation === version && !media) fail('The presentation video did not respond. Close and reopen the window.');
    }, 15000);
    try {
      if (source.kind === 'local') {
        const video = document.createElement('video');
        video.playsInline = true;
        video.preload = 'metadata';
        video.muted = true;
        video.addEventListener('playing', () => { if (generation === version) { status.textContent = ''; send('ready'); } });
        video.addEventListener('loadedmetadata', () => {
          if (generation !== version) return;
          clearTimeout(loadTimer);
          media = {
            time: () => video.currentTime, seek: (time) => { video.currentTime = Math.min(time, video.duration); },
            play: () => { if (video.paused) video.play().catch(() => { unlock.hidden = false; fail('Playback blocked. Select Allow video playback.'); }); },
            pause: () => video.pause(), mute: (muted) => { video.muted = muted; },
          };
          status.textContent = '';
          send('ready');
          apply();
        }, { once: true });
        video.addEventListener('error', () => fail('Could not load the local video.'), { once: true });
        video.src = source.url;
        host.append(video);
      } else {
        const yt = await loadYoutube();
        if (generation !== version) return;
        clearTimeout(loadTimer);
        const frame = document.createElement('iframe');
        frame.title = 'Presentation video';
        frame.allow = 'autoplay; encrypted-media; fullscreen; picture-in-picture';
        frame.referrerPolicy = 'strict-origin-when-cross-origin';
        frame.src = 'https://www.youtube-nocookie.com/embed/' + source.id + '?'
          + new URLSearchParams({ enablejsapi: '1', playsinline: '1', origin });
        host.append(frame);
        player = new yt.Player(frame, { events: {
          onReady: ({ target }) => {
            if (generation !== version) return;
            media = {
              time: () => target.getCurrentTime(), seek: (time) => target.seekTo(time, true),
              play: () => { if (target.getPlayerState() !== 1 && target.getPlayerState() !== 3) target.playVideo(); },
              pause: () => { if (target.getPlayerState() !== 2) target.pauseVideo(); },
              mute: (muted) => muted ? target.mute() : target.unMute(),
            };
            status.textContent = '';
            send('ready');
            apply();
          },
          onAutoplayBlocked: () => { unlock.hidden = false; fail('Playback blocked. Select Allow video playback.'); },
          onStateChange: ({ data }) => { if (generation === version && data === 1) { status.textContent = ''; send('ready'); } },
          onError: ({ data }) => fail('YouTube could not play the presentation video (error ' + data + ').'),
        } });
      }
    } catch (error) { if (generation === version) { clearTimeout(loadTimer); fail(error.message); } }
  }
  window.addEventListener('message', (event) => {
    if (event.origin !== origin || event.source !== parent || event.data?.channel !== 'clicktrack-presentation') return;
    const data = event.data;
    if (data.type === 'close') { media?.pause(); window.close(); return; }
    if (data.type !== 'state') return;
    lastState = Date.now();
    desired = data;
    if (data.source.key !== sourceKey) {
      sourceKey = data.source.key;
      openSource(data.source);
    } else apply();
  });
  unlock.addEventListener('click', () => {
    unlock.hidden = true;
    status.textContent = '';
    apply();
  });
  setInterval(() => {
    if (!parent || parent.closed || Date.now() - lastState > 5000) {
      media?.pause();
      fail('Connection to the conductor was lost. Reopen the presentation window.');
    }
  }, 2000);
  if (parent) send('connected'); else fail('Open this window from the click track performance page.');
})();
