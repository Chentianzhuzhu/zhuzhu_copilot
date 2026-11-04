(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var clamp = function (v, lo, hi) { return Math.min(hi, Math.max(lo, v)); };

  var state = {
    server: null,
    voices: [],
    definitions: {},
    registers: {},
    defaults: { lookahead_ms: 1200, voice: "" },
    maxPolyphony: 64,
    defaultVoice: "",
    voiceId: "",
    registerKey: "",
    playbackId: "",
    playing: false,
    title: "",
    totalMs: 0,
    leadMs: 1200,
    range: [48, 72],
    progressBasePerf: 0,
    visualNotes: [],
    audioReady: false,
    es: null,
    lastUiAt: 0,
    unlocking: false
  };

  var els = {};
  var canvas = null;
  var g = null;
  var keyboard = { key: "", whites: [], blacks: [], rects: new Map(), top: 0, height: 0, keyW: 0 };

  // -------------------------------------------------------------------------
  // 音频引擎：按 voices.json 的泛音/包络/颤音数据实时合成，不加载任何音频文件
  // -------------------------------------------------------------------------
  var Sound = (function () {
    var ctx = null;
    var master = null;
    var limiter = null;
    var bus = null;
    var noiseBuf = null;
    var active = new Set();
    var volume = 0.85;
    var muted = false;

    function readSettings() {
      try {
        var v = parseFloat(localStorage.getItem("piano.volume"));
        if (isFinite(v)) volume = clamp(v, 0, 1);
        muted = localStorage.getItem("piano.muted") === "1";
      } catch (err) { /* 隐私模式下 localStorage 不可用时保持默认 */ }
    }

    function persist() {
      try {
        localStorage.setItem("piano.volume", String(volume));
        localStorage.setItem("piano.muted", muted ? "1" : "0");
      } catch (err) { /* 忽略 */ }
    }

    function applyGain() {
      if (!ctx || !master) return;
      var target = muted ? 0 : volume;
      master.gain.setTargetAtTime(target, ctx.currentTime, 0.02);
    }

    function makeNoise() {
      var length = Math.max(1, Math.floor(ctx.sampleRate * 0.12));
      var buffer = ctx.createBuffer(1, length, ctx.sampleRate);
      var data = buffer.getChannelData(0);
      for (var i = 0; i < length; i++) {
        data[i] = (Math.random() * 2 - 1) * (1 - i / length);
      }
      return buffer;
    }

    function ensure() {
      if (ctx) return ctx;
      var Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) throw new Error("当前浏览器不支持 Web Audio");
      ctx = new Ctor();
      master = ctx.createGain();
      limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -14;
      limiter.knee.value = 24;
      limiter.ratio.value = 6;
      limiter.attack.value = 0.003;
      limiter.release.value = 0.25;
      master.connect(limiter);
      limiter.connect(ctx.destination);
      noiseBuf = makeNoise();
      applyGain();
      return ctx;
    }

    function isReady() {
      return !!ctx && ctx.state === "running";
    }

    function unlock() {
      try {
        ensure();
      } catch (err) {
        return Promise.reject(err);
      }
      return ctx.resume().then(function () {
        if (ctx.state !== "running") throw new Error("音频上下文未能启动（" + ctx.state + "）");
        applyGain();
        return true;
      });
    }

    function resumeIfNeeded() {
      if (ctx && ctx.state === "suspended") {
        ctx.resume().catch(function () { /* 等下一次用户点击 */ });
      }
    }

    function setVolume(v) {
      volume = clamp(v, 0, 1);
      applyGain();
      persist();
    }

    function setMuted(m) {
      muted = !!m;
      applyGain();
      persist();
    }

    function isMuted() { return muted; }
    function getVolume() { return volume; }

    function beginPlayback() {
      if (!ctx) return;
      stopAll();
      bus = ctx.createGain();
      bus.gain.value = 1;
      bus.connect(master);
    }

    function stopAll() {
      if (!ctx) return;
      var oldBus = bus;
      bus = null;
      if (oldBus) {
        try {
          oldBus.gain.cancelScheduledValues(ctx.currentTime);
          oldBus.gain.setValueAtTime(Math.max(0.0001, oldBus.gain.value), ctx.currentTime);
          oldBus.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.06);
        } catch (err) { /* 忽略 */ }
        setTimeout(function () {
          try { oldBus.disconnect(); } catch (err) { /* 忽略 */ }
        }, 140);
      }
      var groups = Array.from(active);
      active.clear();
      // 留出 70ms 让总线淡出，避免硬切产生爆音
      setTimeout(function () {
        groups.forEach(function (group) {
          group.sources.forEach(function (src) {
            try { src.stop(); } catch (err) { /* 已经停止 */ }
          });
        });
      }, 70);
    }

    function pitchDecayFactor(voice, freq) {
      var power = clamp(Number(voice.pitch_decay) || 0, 0, 1.2);
      if (!power) return 1;
      return clamp(Math.pow(261.63 / freq, power), 0.32, 4);
    }

    function scheduleNote(event) {
      if (!isReady() || !bus) return;
      var voice = state.definitions[event.voice] || state.definitions[state.defaultVoice];
      if (!voice || !Array.isArray(voice.partials) || !voice.partials.length) return;

      var t0 = ctx.currentTime + Math.max(0, Number(event.in_ms) || 0) / 1000;
      var durSec = Math.max(0.05, (Number(event.dur_ms) || 200) / 1000);
      var velocity = clamp(Number(event.velocity) || 0.85, 0.05, 1);
      var env = voice.envelope || {};
      var attack = clamp(Number(env.attack) || 0.005, 0.002, 1);
      var sustain = clamp(Number(env.sustain) || 0, 0, 1);
      var release = clamp(Number(env.release) || 0.3, 0.03, 4);
      var peak = Math.max(0.0002, velocity * (Number(voice.gain) || 0.7) * 0.18);

      var notes = Array.isArray(event.notes) ? event.notes : [];
      for (var n = 0; n < notes.length; n++) {
        if (active.size >= state.maxPolyphony) break;
        var midi = Number(notes[n].midi);
        if (!isFinite(midi)) continue;
        var freq = 440 * Math.pow(2, (midi - 69) / 12);
        if (freq < 18 || freq > 12000) continue;

        var decay = clamp(Number(env.decay) || 1, 0.05, 8) * pitchDecayFactor(voice, freq);
        var noteGain = ctx.createGain();
        var attackEnd = t0 + attack;
        var decayEnd = attackEnd + decay;
        var releaseAt = t0 + Math.max(durSec, attack + 0.02);
        var sustainLevel = Math.max(0.0001, peak * (sustain > 0 ? sustain : 0.0001));
        var endAt = releaseAt + release + 0.05;

        try {
          noteGain.gain.setValueAtTime(0.0001, t0);
          noteGain.gain.exponentialRampToValueAtTime(peak, attackEnd);
          noteGain.gain.exponentialRampToValueAtTime(sustainLevel, decayEnd);
          if (typeof noteGain.gain.cancelAndHoldAtTime === "function") {
            noteGain.gain.cancelAndHoldAtTime(releaseAt);
          } else {
            // 旧浏览器没有 cancelAndHoldAtTime：给释放点一个可闻的近似保持值
            var held = sustain > 0 ? sustainLevel : Math.max(0.0001, peak * 0.2);
            noteGain.gain.setValueAtTime(held, releaseAt);
          }
          noteGain.gain.exponentialRampToValueAtTime(0.0001, releaseAt + release);
        } catch (err) {
          // 极短音符可能让自动化端点重叠，退回简单包络，保证音符仍然发声
          noteGain.gain.setValueAtTime(peak, t0);
          noteGain.gain.exponentialRampToValueAtTime(0.0001, endAt);
        }

        var panner = null;
        if (typeof ctx.createStereoPanner === "function") {
          panner = ctx.createStereoPanner();
          panner.pan.value = clamp((Number(voice.pan) || 0) + (midi - 60) / 96, -1, 1);
          noteGain.connect(panner);
          panner.connect(bus);
        } else {
          noteGain.connect(bus);
        }

        var group = { sources: [], gain: noteGain };
        var partialOscs = [];
        var partials = voice.partials;
        for (var p = 0; p < partials.length; p++) {
          var part = partials[p] || {};
          var ratio = Number(part.ratio) || 1;
          if (freq * ratio > ctx.sampleRate * 0.45) continue;
          var osc = ctx.createOscillator();
          var type = String(part.type || "sine");
          if (type !== "sine" && type !== "triangle" && type !== "sawtooth" && type !== "square") {
            type = "sine";
          }
          osc.type = type;
          osc.frequency.setValueAtTime(freq * ratio, t0);
          osc.detune.setValueAtTime((Number(part.detune) || 0) + (Number(voice.detune) || 0), t0);
          var partialGain = ctx.createGain();
          partialGain.gain.value = Math.max(0, Number(part.gain) || 1);
          osc.connect(partialGain);
          partialGain.connect(noteGain);
          osc.start(t0);
          osc.stop(endAt);
          group.sources.push(osc);
          partialOscs.push(osc);
        }

        var vib = voice.vibrato || {};
        if ((Number(vib.depth) || 0) > 0 && (Number(vib.rate) || 0) > 0 && partialOscs.length) {
          var lfo = ctx.createOscillator();
          var lfoGain = ctx.createGain();
          lfo.frequency.value = Number(vib.rate) || 5;
          lfoGain.gain.value = Number(vib.depth) || 4;
          lfo.connect(lfoGain);
          partialOscs.forEach(function (osc) { lfoGain.connect(osc.detune); });
          var lfoStart = t0 + Math.max(0, Number(vib.delay) || 0);
          if (lfoStart < endAt) {
            lfo.start(lfoStart);
            lfo.stop(endAt);
            group.sources.push(lfo);
          }
        }

        if ((Number(voice.noise) || 0) > 0 && noiseBuf) {
          var noise = ctx.createBufferSource();
          noise.buffer = noiseBuf;
          var filter = ctx.createBiquadFilter();
          filter.type = "bandpass";
          filter.frequency.value = clamp(freq * 3, 400, 9000);
          filter.Q.value = 0.8;
          var noiseGain = ctx.createGain();
          noiseGain.gain.setValueAtTime(Math.max(0.0001, Number(voice.noise) * velocity), t0);
          noiseGain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.09);
          noise.connect(filter);
          filter.connect(noiseGain);
          noiseGain.connect(noteGain);
          noise.start(t0);
          noise.stop(t0 + 0.12);
          group.sources.push(noise);
        }

        if (group.sources.length) {
          active.add(group);
          group.sources[0].onended = function () { active.delete(group); };
        }
      }
    }

    readSettings();

    return {
      unlock: unlock,
      resumeIfNeeded: resumeIfNeeded,
      isReady: isReady,
      beginPlayback: beginPlayback,
      stopAll: stopAll,
      scheduleNote: scheduleNote,
      setVolume: setVolume,
      setMuted: setMuted,
      isMuted: isMuted,
      getVolume: getVolume
    };
  })();

  // -------------------------------------------------------------------------
  // 声音开关
  // -------------------------------------------------------------------------
  function unlockAudio() {
    if (state.audioReady || state.unlocking) return;
    state.unlocking = true;
    els.gateBtn.disabled = true;
    els.gateError.textContent = "";
    Sound.unlock().then(function () {
      state.audioReady = true;
      state.unlocking = false;
      els.gate.classList.add("hidden");
      els.gateBtn.disabled = false;
      if (state.playing) Sound.beginPlayback();
      connectEvents();
      setConnection("online", "已连接 · 声音开启");
    }).catch(function (err) {
      state.unlocking = false;
      els.gateBtn.disabled = false;
      els.gateError.textContent = String(err && err.message ? err.message : err);
    });
  }

  // -------------------------------------------------------------------------
  // 连接与状态
  // -------------------------------------------------------------------------
  function setConnection(kind, text) {
    els.conn.classList.remove("online", "error", "waiting");
    if (kind) els.conn.classList.add(kind);
    els.connText.textContent = text;
  }

  function connectEvents() {
    if (state.es) {
      var old = state.es;
      state.es = null;
      old.close();
    }
    var es = new EventSource("/api/events?audio=" + (state.audioReady ? "1" : "0") +
                             "&v=" + Date.now());
    state.es = es;
    setConnection("waiting", "正在连接");

    es.onopen = function () {
      if (state.es !== es) return;
      setConnection("online", state.audioReady ? "已连接 · 声音开启" : "已连接");
    };
    es.onerror = function () {
      if (state.es !== es) return;
      setConnection("error", "连接中断，正在重试");
    };

    function on(eventName, handler) {
      es.addEventListener(eventName, function (event) {
        if (state.es !== es) return;
        var payload;
        try { payload = JSON.parse(event.data); } catch (err) { return; }
        handler(payload);
      });
    }

    on("hello", function (payload) {
      if (payload.definitions) applyDefinitions(payload.definitions);
      var snapshot = payload.state || {};
      if (snapshot.voices && !state.voices.length) {
        applyVoices({
          voices: snapshot.voices,
          registers: snapshot.registers,
          max_polyphony: snapshot.max_polyphony,
          default: snapshot.defaults && snapshot.defaults.voice,
          definitions: payload.definitions
        });
      }
      applyServer(snapshot.server);
      updateCounts(snapshot.listeners, snapshot.audio_ready);
      if (snapshot.playback && snapshot.playback.playing) {
        applySnapshot(snapshot.playback);
      } else if (!state.playing) {
        resetPlaybackUI(false);
      }
    });

    on("start", function (data) {
      state.playing = true;
      state.playbackId = data.playback_id || "";
      state.title = data.title || "AI 演奏";
      state.voiceId = data.voice || state.voiceId;
      state.registerKey = data.register || "";
      state.totalMs = Number(data.total_ms) || 0;
      state.leadMs = Number(data.lead_ms) || state.defaults.lookahead_ms || 1200;
      state.range = Array.isArray(data.range) ? data.range : state.range;
      state.visualNotes = [];
      state.progressBasePerf = performance.now() + state.leadMs;
      if (state.audioReady) Sound.beginPlayback();
      setPlaybackHeader({
        title: state.title,
        subtitle: (data.voice_label || data.voice) + " · " +
                  (data.register_label || "音区") + " · " +
                  (data.note_count || 0) + " 个音",
        voice: data.voice_label || data.voice || "音色",
        register: data.register_label || "音区",
        count: data.note_count || 0
      });
      els.elapsed.textContent = "0:00";
      els.duration.textContent = formatTime(state.totalMs);
      els.progressBar.style.width = "0%";
      highlightVoice(state.voiceId);
      highlightRegister(state.registerKey);
    });

    on("note", function (data) {
      if (data.playback_id && data.playback_id !== state.playbackId) return;
      var now = performance.now();
      var hitAt = now + Math.max(0, Number(data.in_ms) || 0);
      var notes = Array.isArray(data.notes) ? data.notes : [];
      for (var i = 0; i < notes.length; i++) {
        state.visualNotes.push({
          midi: Number(notes[i].midi),
          name: String(notes[i].name || ""),
          hitAt: hitAt,
          durMs: Number(data.dur_ms) || 200,
          velocity: clamp(Number(data.velocity) || 0.85, 0.05, 1)
        });
      }
      if (state.visualNotes.length > 900) {
        state.visualNotes.splice(0, state.visualNotes.length - 900);
      }
      Sound.scheduleNote(data);
    });

    on("end", function (data) {
      if (data.playback_id && data.playback_id !== state.playbackId) return;
      state.playing = false;
      els.subtitle.textContent = "演奏结束 · 等待下一段旋律";
      els.progressBar.style.width = "100%";
      els.elapsed.textContent = formatTime(state.totalMs);
    });

    on("stop", function (data) {
      if (data.playback_id && state.playbackId && data.playback_id !== state.playbackId) return;
      Sound.stopAll();
      resetPlaybackUI(false);
      els.title.textContent = "已停止演奏";
      els.subtitle.textContent = "让 Copilot 弹一段旋律，音符会实时出现在这里";
    });

    on("listeners", function (data) {
      updateCounts(data.listeners, data.audio_ready);
    });
  }

  function applyServer(server) {
    if (!server) return;
    state.server = server;
    var url = (server.lan_urls && server.lan_urls.length) ? server.lan_urls[0] : (server.local_url || "");
    if (url) {
      els.listenLink.href = url;
      els.listenLink.textContent = url;
      els.footerUrl.textContent = url;
    } else {
      els.listenLink.removeAttribute("href");
      els.listenLink.textContent = "服务未启动";
      els.footerUrl.textContent = "服务未启动";
    }
  }

  function updateCounts(total, audio) {
    els.listenerCount.textContent = String(Number(total) || 0);
    els.audioCount.textContent = String(Number(audio) || 0);
  }

  function applySnapshot(playback) {
    state.playing = true;
    state.playbackId = playback.playback_id || "";
    state.title = playback.title || "AI 演奏";
    state.voiceId = playback.voice || state.voiceId;
    state.registerKey = playback.register || "";
    state.totalMs = Number(playback.total_ms) || 0;
    state.leadMs = Number(playback.lead_ms) || state.defaults.lookahead_ms || 1200;
    state.range = Array.isArray(playback.range) ? playback.range : state.range;
    state.progressBasePerf = performance.now() - (Number(playback.elapsed_ms) || 0);
    setPlaybackHeader({
      title: state.title,
      subtitle: (playback.voice_label || playback.voice) + " · " +
                (playback.register_label || "音区") + " · " +
                (playback.note_count || 0) + " 个音",
      voice: playback.voice_label || playback.voice || "音色",
      register: playback.register_label || "音区",
      count: playback.note_count || 0
    });
    els.duration.textContent = formatTime(state.totalMs);
    highlightVoice(state.voiceId);
    highlightRegister(state.registerKey);
    if (state.audioReady) Sound.beginPlayback();
  }

  function setPlaybackHeader(info) {
    els.title.textContent = info.title;
    els.subtitle.textContent = info.subtitle;
    els.voiceBadge.textContent = "音色 " + info.voice;
    els.registerBadge.textContent = "音区 " + info.register;
    els.countBadge.textContent = info.count + " 个音";
  }

  function resetPlaybackUI(keepTitle) {
    state.playing = false;
    state.playbackId = "";
    state.totalMs = 0;
    state.visualNotes = [];
    state.range = [48, 72];
    state.registerKey = "";
    state.voiceId = state.defaultVoice || state.voiceId;
    if (!keepTitle) {
      els.title.textContent = "等待 AI 演奏";
      els.subtitle.textContent = "让 Copilot 弹一段旋律，音符会实时出现在这里";
    }
    els.voiceBadge.textContent = "音色 —";
    els.registerBadge.textContent = "音区 —";
    els.countBadge.textContent = "0 个音";
    els.elapsed.textContent = "0:00";
    els.duration.textContent = "0:00";
    els.progressBar.style.width = "0%";
    highlightVoice(state.voiceId);
    highlightRegister("");
  }

  // -------------------------------------------------------------------------
  // 音色 / 音区侧栏
  // -------------------------------------------------------------------------
  function applyDefinitions(definitions) {
    if (!Array.isArray(definitions)) return;
    var map = {};
    definitions.forEach(function (voice) {
      if (voice && voice.id) map[voice.id] = voice;
    });
    if (Object.keys(map).length) state.definitions = map;
  }

  function applyVoices(data) {
    if (!data) return;
    if (Array.isArray(data.voices) && data.voices.length) state.voices = data.voices;
    if (data.registers) state.registers = data.registers;
    if (data.definitions) applyDefinitions(data.definitions);
    if (Number(data.max_polyphony)) state.maxPolyphony = Number(data.max_polyphony);
    if (data.default) state.defaultVoice = String(data.default);
    if (!state.defaultVoice && state.voices.length) state.defaultVoice = state.voices[0].id;
    if (!state.voiceId) state.voiceId = state.defaultVoice;
    renderVoiceList();
    renderRegisters();
  }

  function renderVoiceList() {
    els.voiceList.textContent = "";
    state.voices.forEach(function (voice) {
      var item = document.createElement("li");
      item.className = "voice-item";
      item.dataset.id = voice.id;
      var line = document.createElement("div");
      line.className = "voice-line";
      var name = document.createElement("strong");
      name.textContent = voice.label || voice.id;
      var code = document.createElement("code");
      code.textContent = voice.id;
      line.appendChild(name);
      line.appendChild(code);
      var desc = document.createElement("div");
      desc.className = "voice-desc";
      desc.textContent = voice.description || "";
      item.appendChild(line);
      item.appendChild(desc);
      els.voiceList.appendChild(item);
    });
    els.voiceCount.textContent = state.voices.length + " 种";
    highlightVoice(state.voiceId);
  }

  function highlightVoice(voiceId) {
    var children = els.voiceList.children;
    for (var i = 0; i < children.length; i++) {
      children[i].classList.toggle("active", children[i].dataset.id === voiceId);
    }
  }

  function renderRegisters() {
    els.registerList.textContent = "";
    ["low", "mid", "high"].forEach(function (key) {
      var item = state.registers[key];
      if (!item) return;
      var box = document.createElement("div");
      box.className = "register-item";
      box.dataset.key = key;
      var label = document.createElement("strong");
      label.textContent = item.label || key;
      var octave = document.createElement("small");
      octave.textContent = "八度 " + item.octave;
      box.appendChild(label);
      box.appendChild(octave);
      els.registerList.appendChild(box);
    });
    highlightRegister(state.registerKey);
  }

  function highlightRegister(key) {
    var children = els.registerList.children;
    for (var i = 0; i < children.length; i++) {
      children[i].classList.toggle("active", !!key && children[i].dataset.key === key);
    }
  }

  // -------------------------------------------------------------------------
  // 画布：落键瀑布 + 键盘高亮
  // -------------------------------------------------------------------------
  var BLACK_PCS = { 1: true, 3: true, 6: true, 8: true, 10: true };

  function resizeCanvas() {
    if (!canvas || !g) return;
    var rect = els.canvasWrap.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var width = Math.max(1, Math.round(rect.width * dpr));
    var height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      keyboard.key = "";
    }
  }

  function rebuildKeyboard(width, height) {
    var keyId = width + "x" + height + "x" + state.range[0] + "-" + state.range[1];
    if (keyboard.key === keyId) return keyboard;

    var kbHeight = clamp(height * 0.24, 64, 148);
    var top = height - kbHeight;
    var low = clamp(Number(state.range[0]) - 2, 21, 108);
    var high = clamp(Number(state.range[1]) + 2, 21, 108);
    if (high - low < 18) {
      var mid = Math.round((low + high) / 2);
      low = clamp(mid - 9, 21, 108);
      high = clamp(low + 18, 21, 108);
      low = clamp(high - 18, 21, 108);
    }

    var whiteCount = 0;
    for (var m = low; m <= high; m++) if (!BLACK_PCS[m % 12]) whiteCount++;
    var keyW = width / Math.max(1, whiteCount);
    var whites = [];
    var blacks = [];
    var index = 0;
    for (var note = low; note <= high; note++) {
      if (!BLACK_PCS[note % 12]) {
        whites.push({ midi: note, x: index * keyW, w: keyW });
        index++;
      }
    }
    for (var black = low; black <= high; black++) {
      if (!BLACK_PCS[black % 12]) continue;
      var previous = null;
      for (var i = 0; i < whites.length; i++) {
        if (whites[i].midi < black) previous = whites[i];
        else break;
      }
      if (previous) {
        var bw = Math.max(4, keyW * 0.62);
        blacks.push({
          midi: black,
          x: clamp(previous.x + previous.w - bw / 2, 0, width - bw),
          w: bw
        });
      }
    }

    var rects = new Map();
    whites.forEach(function (key) {
      rects.set(key.midi, { x: key.x, w: key.w, black: false });
    });
    blacks.forEach(function (key) {
      rects.set(key.midi, { x: key.x, w: key.w, black: true });
    });

    keyboard = {
      key: keyId, whites: whites, blacks: blacks, rects: rects,
      top: top, height: kbHeight, keyW: keyW
    };
    return keyboard;
  }

  function roundRect(x, y, w, h, r) {
    var radius = Math.max(0, Math.min(r, w / 2, h / 2));
    g.beginPath();
    g.moveTo(x + radius, y);
    g.lineTo(x + w - radius, y);
    g.quadraticCurveTo(x + w, y, x + w, y + radius);
    g.lineTo(x + w, y + h - radius);
    g.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    g.lineTo(x + radius, y + h);
    g.quadraticCurveTo(x, y + h, x, y + h - radius);
    g.lineTo(x, y + radius);
    g.quadraticCurveTo(x, y, x + radius, y);
    g.closePath();
  }

  function draw(now) {
    if (!canvas || !g) return;
    var width = canvas.clientWidth || 1;
    var height = canvas.clientHeight || 1;
    if (!width || !height) return;
    var kb = rebuildKeyboard(width, height);

    g.clearRect(0, 0, width, height);

    // 背景细网格（极淡，避免视觉噪声）
    g.strokeStyle = "rgba(255, 255, 255, 0.022)";
    g.lineWidth = 1;
    var step = Math.max(40, kb.top / 7);
    for (var y = step; y < kb.top; y += step) {
      g.beginPath();
      g.moveTo(0, Math.round(y) + 0.5);
      g.lineTo(width, Math.round(y) + 0.5);
      g.stroke();
    }

    var activeKeys = new Set();
    var alive = [];
    var visibleCount = 0;
    for (var i = 0; i < state.visualNotes.length; i++) {
      var note = state.visualNotes[i];
      if (now > note.hitAt + note.durMs + 600) continue;
      alive.push(note);
      if (now < note.hitAt - state.leadMs) continue;
      if (now >= note.hitAt && now <= note.hitAt + Math.max(140, Math.min(note.durMs, 3200))) {
        activeKeys.add(note.midi);
      }
      if (visibleCount >= 700) continue;
      var rect = kb.rects.get(note.midi);
      if (!rect) continue;
      var progress = clamp((now - (note.hitAt - state.leadMs)) / Math.max(1, state.leadMs), 0, 1);
      var bottom = progress * kb.top;
      var barHeight = clamp((note.durMs / Math.max(1, state.leadMs)) * kb.top * 0.85,
                            8, kb.top * 0.62);
      var barTop = bottom - barHeight;
      if (barTop > kb.top || bottom < 0) continue;
      var alpha = 0.16 + 0.5 * note.velocity;
      g.fillStyle = "rgba(74, 116, 240, " + alpha.toFixed(3) + ")";
      roundRect(rect.x + 1, barTop, Math.max(2, rect.w - 2), barHeight, 3);
      g.fill();
      g.fillStyle = "rgba(188, 207, 255, 0.92)";
      g.fillRect(rect.x + 1, bottom - 2, Math.max(2, rect.w - 2), 2);
      visibleCount++;
    }
    state.visualNotes = alive;

    // 键盘
    g.lineWidth = 1;
    kb.whites.forEach(function (key) {
      var isActive = activeKeys.has(key.midi);
      g.fillStyle = isActive ? "#dde5f4" : "#0b0d12";
      g.fillRect(key.x, kb.top, Math.max(1, key.w - 1), kb.height);
      g.strokeStyle = "#1a1f29";
      g.strokeRect(key.x + 0.5, kb.top + 0.5, Math.max(1, key.w - 1), kb.height - 1);
      if (isActive && key.w > 20) {
        g.fillStyle = "#0a0c11";
        g.font = "10px " + '"SFMono-Regular", "Consolas", monospace';
        g.textAlign = "center";
        g.textBaseline = "bottom";
        var name = key.midi % 12 === 0 ? "C" + (Math.floor(key.midi / 12) - 1) : "";
        if (name) g.fillText(name, key.x + key.w / 2, kb.top + kb.height - 6);
      } else if (key.w > 26 && key.midi % 12 === 0) {
        g.fillStyle = "rgba(155, 165, 182, 0.62)";
        g.font = "10px " + '"SFMono-Regular", "Consolas", monospace';
        g.textAlign = "center";
        g.textBaseline = "bottom";
        g.fillText("C" + (Math.floor(key.midi / 12) - 1), key.x + key.w / 2, kb.top + kb.height - 6);
      }
    });

    kb.blacks.forEach(function (key) {
      var isActive = activeKeys.has(key.midi);
      var h = kb.height * 0.62;
      g.fillStyle = isActive ? "#2f5fe8" : "#030406";
      g.fillRect(key.x, kb.top, key.w, h);
      g.strokeStyle = isActive ? "#5b83f5" : "#141a24";
      g.strokeRect(key.x + 0.5, kb.top + 0.5, key.w - 1, h - 1);
    });

    g.strokeStyle = "rgba(79, 124, 255, 0.38)";
    g.beginPath();
    g.moveTo(0, kb.top + 0.5);
    g.lineTo(width, kb.top + 0.5);
    g.stroke();

    if (!state.playing && visibleCount === 0) {
      g.fillStyle = "rgba(120, 130, 148, 0.45)";
      g.font = "13px system-ui, -apple-system, 'Segoe UI', sans-serif";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("等待 AI 演奏", width / 2, kb.top * 0.36);
    }
  }

  function formatTime(ms) {
    var total = Math.max(0, Math.round((Number(ms) || 0) / 1000));
    var minutes = Math.floor(total / 60);
    var seconds = total % 60;
    return minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    draw(now);
    if (state.playing && state.totalMs > 0) {
      var elapsed = Math.max(0, now - state.progressBasePerf);
      var ratio = clamp(elapsed / state.totalMs, 0, 1);
      if (now - state.lastUiAt > 100) {
        state.lastUiAt = now;
        els.progressBar.style.width = (ratio * 100).toFixed(2) + "%";
        els.elapsed.textContent = formatTime(Math.min(elapsed, state.totalMs));
      }
      if (elapsed > state.totalMs + 1500) {
        state.playing = false;
        els.progressBar.style.width = "100%";
        els.elapsed.textContent = formatTime(state.totalMs);
        els.subtitle.textContent = "演奏结束 · 等待下一段旋律";
      }
    }
  }

  // -------------------------------------------------------------------------
  // 配置加载
  // -------------------------------------------------------------------------
  function loadVoices() {
    return fetch("/api/voices", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data || !data.ok) throw new Error("音色接口返回异常");
        applyVoices(data);
      });
  }

  function refreshState() {
    return fetch("/api/state", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data || !data.ok) return;
        applyServer(data.server);
        updateCounts(data.listeners, data.audio_ready);
        if (data.voices && !state.voices.length) applyVoices(data);
        if (data.playback && data.playback.playing) applySnapshot(data.playback);
      });
  }

  // -------------------------------------------------------------------------
  // 界面事件
  // -------------------------------------------------------------------------
  function bindUI() {
    els.gate.addEventListener("click", unlockAudio);
    els.reconnectBtn.addEventListener("click", function () {
      connectEvents();
      refreshState().catch(function () { /* 下一次重连会再取 */ });
    });

    els.volume.value = String(Math.round(Sound.getVolume() * 100));
    if (Sound.isMuted()) els.muteBtn.classList.add("muted");
    els.volume.addEventListener("input", function () {
      Sound.setVolume(Number(els.volume.value) / 100);
      if (Sound.isMuted()) {
        Sound.setMuted(false);
        els.muteBtn.classList.remove("muted");
      }
    });
    els.muteBtn.addEventListener("click", function () {
      var next = !Sound.isMuted();
      Sound.setMuted(next);
      els.muteBtn.classList.toggle("muted", next);
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) Sound.resumeIfNeeded();
    });
    document.addEventListener("pointerdown", function () {
      if (state.audioReady) Sound.resumeIfNeeded();
    });

    if (window.ResizeObserver) {
      var pending = false;
      new ResizeObserver(function () {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () {
          pending = false;
          resizeCanvas();
        });
      }).observe(els.canvasWrap);
    } else {
      window.addEventListener("resize", resizeCanvas);
    }
  }

  function boot() {
    els = {
      conn: $("conn"), connDot: $("connDot"), connText: $("connText"),
      title: $("title"), subtitle: $("subtitle"),
      voiceBadge: $("voiceBadge"), registerBadge: $("registerBadge"), countBadge: $("countBadge"),
      canvasWrap: $("canvasWrap"), gate: $("gate"), gateBtn: $("gateBtn"),
      gateError: $("gateError"), elapsed: $("elapsed"), duration: $("duration"),
      progressBar: $("progressBar"), muteBtn: $("muteBtn"), volume: $("volume"),
      reconnectBtn: $("reconnectBtn"), voiceList: $("voiceList"), voiceCount: $("voiceCount"),
      registerList: $("registerList"), listenLink: $("listenLink"),
      listenerCount: $("listenerCount"), audioCount: $("audioCount"), footerUrl: $("footerUrl")
    };
    canvas = $("stageCanvas");
    g = canvas.getContext("2d");

    bindUI();
    resizeCanvas();
    requestAnimationFrame(frame);

    loadVoices().catch(function () {
      setTimeout(function () { loadVoices().catch(function () { /* 等 SSE hello 兜底 */ }); }, 2000);
    });
    refreshState().catch(function () { /* SSE hello 会补状态 */ });
    connectEvents();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
