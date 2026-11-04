(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var clamp = function (v, lo, hi) { return Math.min(hi, Math.max(lo, v)); };

  var state = {
    server: null,
    voices: [],
    definitions: {},
    registers: {},
    sampleConfig: {},
    defaults: { lookahead_ms: 1200, voice: "" },
    maxPolyphony: 64,
    defaultVoice: "",
    voiceId: "",
    playbackId: "",
    playing: false,
    title: "",
    totalMs: 0,
    leadMs: 1200,
    range: [48, 72],
    score: [],
    scoreCursor: 0,
    visualCursor: 0,
    songStartPerf: 0,
    audioTimelineStart: null,
    pedal: false,
    tempo: null,
    timeSignature: null,
    tracks: 1,
    scheduleAheadMs: 8000,
    audioReady: false,
    samplesReady: false,
    samplesLoading: false,
    sampleFailed: false,
    es: null,
    lastUiAt: 0,
    lastLevelAt: 0,
    unlocking: false
  };

  var els = {};
  var canvas = null;
  var g = null;
  var keyboard = { key: "", whites: [], blacks: [], rects: new Map(), top: 0, height: 0, keyW: 0 };

  // -------------------------------------------------------------------------
  // 音频引擎：钢琴用离线渲染的多锚点采样（泛音列 + 非谐性 + 三弦拍音 + 踏板），
  // 其他音色仍用数据化泛音合成；主链路带卷积混响与限幅器。
  // -------------------------------------------------------------------------
  var Sound = (function () {
    var ctx = null;
    var master = null;
    var limiter = null;
    var bus = null;
    var reverbBuf = null;
    var analyser = null;
    var levelBuffer = null;
    var active = new Set();
    var sampleBank = {};
    var sampleReady = false;
    var preparePromise = null;
    var volume = 0.85;
    var muted = false;

    function readSettings() {
      try {
        var v = parseFloat(localStorage.getItem("piano.volume"));
        if (isFinite(v)) volume = clamp(v, 0, 1);
        muted = localStorage.getItem("piano.muted") === "1";
      } catch (err) { /* 忽略隐私模式限制 */ }
    }

    function persist() {
      try {
        localStorage.setItem("piano.volume", String(volume));
        localStorage.setItem("piano.muted", muted ? "1" : "0");
      } catch (err) { /* 忽略 */ }
    }

    function applyGain() {
      if (!ctx || !master) return;
      master.gain.setTargetAtTime(muted ? 0 : volume, ctx.currentTime, 0.02);
    }

    function midiFreq(midi) {
      return 440 * Math.pow(2, (midi - 69) / 12);
    }

    function makeImpulse(seconds, decay) {
      var sr = ctx.sampleRate;
      var length = Math.max(1, Math.floor(sr * seconds));
      var buffer = ctx.createBuffer(2, length, sr);
      var offsets = [0.006, 0.014, 0.027, 0.048, 0.072];
      for (var ch = 0; ch < 2; ch++) {
        var data = buffer.getChannelData(ch);
        for (var i = 0; i < length; i++) {
          data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay) * 0.55;
        }
        offsets.forEach(function (offset, index) {
          var pos = Math.min(length - 1, Math.floor((offset + ch * 0.004) * sr));
          data[pos] += (index % 2 === 0 ? 1 : -1) * 0.28;
        });
      }
      return buffer;
    }

    function ensure() {
      if (ctx) return ctx;
      var Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) throw new Error("当前浏览器不支持 Web Audio");
      ctx = new Ctor();
      master = ctx.createGain();
      master.gain.value = muted ? 0 : volume;
      limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -14;
      limiter.knee.value = 24;
      limiter.ratio.value = 6;
      limiter.attack.value = 0.003;
      limiter.release.value = 0.25;
      analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      levelBuffer = new Uint8Array(analyser.fftSize);
      master.connect(limiter);
      limiter.connect(analyser);
      analyser.connect(ctx.destination);
      reverbBuf = makeImpulse(0.32, 2.2);
      return ctx;
    }

    function isReady() {
      return !!ctx && ctx.state === "running";
    }

    function currentTime() {
      return ctx ? ctx.currentTime : 0;
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
        ctx.resume().catch(function () { /* 等下一次点击 */ });
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

    function getLevel() {
      if (!analyser || !levelBuffer) return 0;
      analyser.getByteTimeDomainData(levelBuffer);
      var sum = 0;
      for (var i = 0; i < levelBuffer.length; i++) {
        var value = (levelBuffer[i] - 128) / 128;
        sum += value * value;
      }
      var rms = Math.sqrt(sum / levelBuffer.length);
      return clamp(rms * 3.2, 0, 1);
    }

    function buildBus() {
      var dry = ctx.createGain();
      dry.gain.value = 1;
      var wet = ctx.createGain();
      wet.gain.value = 0.17;
      var convolver = null;
      if (reverbBuf) {
        convolver = ctx.createConvolver();
        convolver.normalize = true;
        convolver.buffer = reverbBuf;
      }
      var newBus = ctx.createGain();
      newBus.gain.value = 1;
      newBus.connect(dry);
      dry.connect(master);
      if (convolver) {
        newBus.connect(convolver);
        convolver.connect(wet);
        wet.connect(master);
      }
      return { bus: newBus, dry: dry, wet: wet, convolver: convolver };
    }

    function beginPlayback() {
      if (!ctx) return;
      stopAll();
      var built = buildBus();
      bus = built.bus;
      active = new Set();
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
        }, 160);
      }
      var groups = Array.from(active);
      active.clear();
      setTimeout(function () {
        groups.forEach(function (group) {
          group.sources.forEach(function (src) {
            try { src.stop(); } catch (err) { /* 已停止 */ }
          });
        });
      }, 70);
    }

    // ---- 离线采样渲染：把物理钢琴的模态结构烘成 6 个锚点 × 3 层力度 ----
    function normalize(buffer, peakTarget) {
      var data = buffer.getChannelData(0);
      var peak = 0;
      for (var i = 0; i < data.length; i++) {
        var abs = Math.abs(data[i]);
        if (abs > peak) peak = abs;
      }
      if (peak > 0.0001) {
        var scale = peakTarget / peak;
        for (var j = 0; j < data.length; j++) data[j] *= scale;
      }
      return buffer;
    }

    function renderAnchor(anchor, layerVelocity, config, voiceDef) {
      return new Promise(function (resolve, reject) {
        var Ctor = window.OfflineAudioContext || window.webkitOfflineAudioContext;
        if (!Ctor) {
          reject(new Error("浏览器不支持 OfflineAudioContext"));
          return;
        }
        try {
          var sr = 44100;
          var seconds = Math.max(0.7, Number(anchor.seconds) || 3);
          var off = new Ctor(1, Math.ceil(sr * seconds), sr);
          var out = off.createGain();
          out.gain.value = 1;
          out.connect(off.destination);
          var f0 = midiFreq(Number(anchor.midi) || 60);
          var inharm = Number(config.inharmonicity) || 0.0004;
          var gainPower = Number(config.mode_gain_power) || 1.32;
          var damping = Number(config.mode_damping) || 0.18;
          var attack = Math.max(0.0015, Number(config.attack) || 0.0035);
          var decay = Math.max(0.15, Number(anchor.decay) || 1.5);
          var modes = Math.max(4, Number(anchor.modes) || 20);
          var strings = config.strings || [{ detune: 0, gain: 1 }];

          strings.forEach(function (string) {
            var detune = 1 + (Number(string.detune) || 0);
            var stringGain = Number(string.gain) || 1;
            for (var n = 1; n <= modes; n++) {
              var ratio = n * Math.sqrt(1 + inharm * n * n);
              var freq = f0 * ratio * detune;
              if (freq >= sr * 0.45) break;
              var tilt = (0.95 - 0.58 * layerVelocity) * (n - 1) * 0.11;
              var amp = stringGain * Math.pow(1 / n, gainPower) * Math.exp(-tilt);
              amp *= 0.9 + Math.random() * 0.2;
              var tau = Math.max(attack + 0.02, decay / (1 + (n - 1) * damping));
              var end = Math.min(seconds, tau);
              var osc = off.createOscillator();
              osc.type = "sine";
              osc.frequency.value = freq;
              var gain = off.createGain();
              gain.gain.setValueAtTime(0.0001, 0);
              gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, amp), attack);
              gain.gain.exponentialRampToValueAtTime(0.0001, end);
              osc.connect(gain);
              gain.connect(out);
              osc.start(0);
              osc.stop(seconds);
            }
          });

          var hammer = Number(config.hammer_noise) || 0;
          if (hammer > 0) {
            var noiseMs = Math.max(6, Number(config.hammer_noise_ms) || 26);
            var noiseLength = Math.floor(sr * noiseMs / 1000);
            var noiseBuffer = off.createBuffer(1, noiseLength, sr);
            var noiseData = noiseBuffer.getChannelData(0);
            for (var i = 0; i < noiseLength; i++) {
              noiseData[i] = (Math.random() * 2 - 1) * (1 - i / noiseLength);
            }
            var noiseSource = off.createBufferSource();
            noiseSource.buffer = noiseBuffer;
            var lowpass = off.createBiquadFilter();
            lowpass.type = "lowpass";
            lowpass.frequency.value = clamp(f0 * 6, 300, 6000);
            lowpass.Q.value = 0.7;
            var noiseGain = off.createGain();
            noiseGain.gain.setValueAtTime(Math.max(0.0001, hammer * layerVelocity), 0);
            noiseGain.gain.exponentialRampToValueAtTime(0.0001, noiseLength / sr);
            noiseSource.connect(lowpass);
            lowpass.connect(noiseGain);
            noiseGain.connect(out);
            noiseSource.start(0);
          }

          off.startRendering().then(function (buffer) {
            resolve(normalize(buffer, 0.9));
          }).catch(reject);
        } catch (err) {
          reject(err);
        }
      });
    }

    function prepareSamples(config, definitions, onProgress) {
      if (sampleReady) return Promise.resolve(true);
      if (preparePromise) return preparePromise;
      var sampleConfig = config || {};
      var sampleVoices = (sampleConfig.voices || []).filter(function (voiceId) {
        var def = definitions && definitions[voiceId];
        return def && String(def.engine || "") === "sample";
      });
      if (!sampleVoices.length) {
        sampleReady = true;
        return Promise.resolve(true);
      }
      var anchors = sampleConfig.anchors || [];
      var layers = sampleConfig.velocity_layers || [0.35, 0.95];
      if (!anchors.length) {
        sampleReady = true;
        return Promise.resolve(true);
      }
      var total = anchors.length * layers.length * sampleVoices.length;
      var done = 0;
      var bank = {};
      preparePromise = (async function () {
        for (var vi = 0; vi < sampleVoices.length; vi++) {
          var voiceId = sampleVoices[vi];
          var def = definitions[voiceId];
          var rows = [];
          for (var ai = 0; ai < anchors.length; ai++) {
            var row = [];
            for (var li = 0; li < layers.length; li++) {
              var buffer = await renderAnchor(anchors[ai], layers[li], sampleConfig, def);
              row.push(buffer);
              done++;
              if (onProgress) onProgress(done / total);
            }
            rows.push(row);
          }
          bank[voiceId] = { anchors: anchors, layers: layers, buffers: rows };
        }
        sampleBank = bank;
        sampleReady = true;
        return true;
      })();
      preparePromise.catch(function () { preparePromise = null; });
      return preparePromise;
    }

    function nearestAnchor(anchors, midi) {
      var best = 0;
      var bestDistance = Infinity;
      for (var i = 0; i < anchors.length; i++) {
        var distance = Math.abs(Number(anchors[i].midi) - midi);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = i;
        }
      }
      return best;
    }

    function nearestLayer(layers, velocity) {
      var best = 0;
      var bestDistance = Infinity;
      for (var i = 0; i < layers.length; i++) {
        var distance = Math.abs(Number(layers[i]) - velocity);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = i;
        }
      }
      return best;
    }

    function scheduleSample(event, voiceId, voiceDef, midi, startTime, pedal) {
      var bank = sampleBank[voiceId];
      if (!bank || !ctx || !bus) return false;
      var anchorIndex = nearestAnchor(bank.anchors, midi);
      var layerIndex = nearestLayer(bank.layers, event.velocity);
      var buffer = bank.buffers[anchorIndex][layerIndex];
      if (!buffer) return false;
      var anchorMidi = Number(bank.anchors[anchorIndex].midi);
      var rate = Math.pow(2, (midi - anchorMidi) / 12);
      rate *= 1 + (Math.random() - 0.5) * 0.0016;
      var source = ctx.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = rate;
      var gain = ctx.createGain();
      var velocityGain = 0.28 + 0.72 * clamp(Number(event.velocity) || 0.85, 0.05, 1);
      gain.gain.setValueAtTime(velocityGain, Math.max(ctx.currentTime, startTime));
      var releaseMs = Math.max(20, Number(state.sampleConfig.release_ms) || 70);
      var panner = null;
      if (typeof ctx.createStereoPanner === "function") {
        panner = ctx.createStereoPanner();
        panner.pan.value = clamp((Number(voiceDef.pan) || 0) + (midi - 60) / 72, -0.9, 0.9);
        gain.connect(panner);
        panner.connect(bus);
      } else {
        gain.connect(bus);
      }
      source.connect(gain);
      var group = { sources: [source], gain: gain };
      active.add(group);
      source.onended = function () { active.delete(group); };
      var safeStart = Math.max(ctx.currentTime, startTime);
      try {
        source.start(safeStart);
      } catch (err) {
        active.delete(group);
        return false;
      }
      if (!pedal) {
        var releaseAt2 = safeStart + Math.max(0.03, event.dur / 1000);
        gain.gain.setValueAtTime(velocityGain, releaseAt2);
        gain.gain.linearRampToValueAtTime(0.0001, releaseAt2 + releaseMs / 1000);
        try {
          source.stop(releaseAt2 + releaseMs / 1000 + 0.04);
        } catch (err) { /* 已结束 */ }
      } else {
        try {
          source.stop(safeStart + buffer.duration / rate + 0.05);
        } catch (err) { /* 已结束 */ }
      }
      return true;
    }

    function scheduleSynth(event, voiceDef, midi, startTime, pedal) {
      if (!ctx || !bus) return;
      if (active.size >= state.maxPolyphony) return;
      var freq = midiFreq(midi);
      if (freq < 18 || freq > 12000) return;
      var vel = clamp(Number(event.velocity) || 0.85, 0.05, 1);
      var env = voiceDef.envelope || {};
      var attack = clamp(Number(env.attack) || 0.005, 0.002, 1);
      var sustain = clamp(Number(env.sustain) || 0, 0, 1);
      var release = clamp(Number(env.release) || 0.3, 0.03, 4);
      var peak = Math.max(0.0002, vel * (Number(voiceDef.gain) || 0.7) * 0.18);
      var decay = clamp(Number(env.decay) || 1, 0.05, 8);
      var power = clamp(Number(voiceDef.pitch_decay) || 0, 0, 1.2);
      if (power) decay *= clamp(Math.pow(261.63 / freq, power), 0.32, 4);
      var noteGain = ctx.createGain();
      var attackEnd = startTime + attack;
      var decayEnd = attackEnd + decay;
      var releaseAt = startTime + Math.max(event.dur / 1000, attack + 0.02);
      var sustainLevel = Math.max(0.0001, peak * (sustain > 0 ? sustain : 0.0001));
      var endAt = releaseAt + release + 0.05;
      try {
        noteGain.gain.setValueAtTime(0.0001, startTime);
        noteGain.gain.exponentialRampToValueAtTime(peak, attackEnd);
        noteGain.gain.exponentialRampToValueAtTime(sustainLevel, decayEnd);
        if (typeof noteGain.gain.cancelAndHoldAtTime === "function") {
          noteGain.gain.cancelAndHoldAtTime(releaseAt);
        } else {
          noteGain.gain.setValueAtTime(
            sustain > 0 ? sustainLevel : Math.max(0.0001, peak * 0.2), releaseAt);
        }
        noteGain.gain.exponentialRampToValueAtTime(0.0001, releaseAt + release);
      } catch (err) {
        noteGain.gain.setValueAtTime(peak, startTime);
        noteGain.gain.exponentialRampToValueAtTime(0.0001, endAt);
      }
      var panner = null;
      if (typeof ctx.createStereoPanner === "function") {
        panner = ctx.createStereoPanner();
        panner.pan.value = clamp((Number(voiceDef.pan) || 0) + (midi - 60) / 96, -1, 1);
        noteGain.connect(panner);
        panner.connect(bus);
      } else {
        noteGain.connect(bus);
      }
      var group = { sources: [], gain: noteGain };
      var partials = voiceDef.partials || [];
      var oscillators = [];
      for (var i = 0; i < partials.length; i++) {
        var part = partials[i] || {};
        var ratio = Number(part.ratio) || 1;
        if (freq * ratio > ctx.sampleRate * 0.45) continue;
        var osc = ctx.createOscillator();
        var type = String(part.type || "sine");
        if (type !== "sine" && type !== "triangle" && type !== "sawtooth" && type !== "square") {
          type = "sine";
        }
        osc.type = type;
        osc.frequency.setValueAtTime(freq * ratio, startTime);
        osc.detune.setValueAtTime((Number(part.detune) || 0) + (Number(voiceDef.detune) || 0), startTime);
        var partialGain = ctx.createGain();
        partialGain.gain.value = Math.max(0, Number(part.gain) || 1);
        osc.connect(partialGain);
        partialGain.connect(noteGain);
        osc.start(startTime);
        osc.stop(endAt);
        group.sources.push(osc);
        oscillators.push(osc);
      }
      var vib = voiceDef.vibrato || {};
      if ((Number(vib.depth) || 0) > 0 && (Number(vib.rate) || 0) > 0 && oscillators.length) {
        var lfo = ctx.createOscillator();
        var lfoGain = ctx.createGain();
        lfo.frequency.value = Number(vib.rate) || 5;
        lfoGain.gain.value = Number(vib.depth) || 4;
        lfo.connect(lfoGain);
        oscillators.forEach(function (osc) { lfoGain.connect(osc.detune); });
        var lfoStart = startTime + Math.max(0, Number(vib.delay) || 0);
        if (lfoStart < endAt) {
          lfo.start(lfoStart);
          lfo.stop(endAt);
          group.sources.push(lfo);
        }
      }
      if (group.sources.length) {
        active.add(group);
        group.sources[0].onended = function () { active.delete(group); };
      }
    }

    function scheduleEvent(event, startTime, pedal) {
      if (!isReady() || !bus) return;
      var voiceDef = state.definitions[event.voice] || state.definitions[state.defaultVoice];
      if (!voiceDef) return;
      var midis = event.midis || [];
      for (var i = 0; i < midis.length; i++) {
        var midi = Number(midis[i]);
        if (!isFinite(midi)) continue;
        if (String(voiceDef.engine || "") === "sample" &&
            scheduleSample(event, event.voice, voiceDef, midi, startTime, pedal)) {
          continue;
        }
        scheduleSynth(event, voiceDef, midi, startTime, pedal);
      }
    }

    function sampleState() {
      return { ready: sampleReady, error: "", bank: sampleBank };
    }

    readSettings();

    return {
      prepareSamples: prepareSamples,
      unlock: unlock,
      resumeIfNeeded: resumeIfNeeded,
      isReady: isReady,
      currentTime: currentTime,
      beginPlayback: beginPlayback,
      stopAll: stopAll,
      scheduleEvent: scheduleEvent,
      setVolume: setVolume,
      setMuted: setMuted,
      isMuted: isMuted,
      getVolume: getVolume,
      getLevel: getLevel,
      sampleState: sampleState
    };
  })();

  // -------------------------------------------------------------------------
  // 声音开关与采样准备状态
  // -------------------------------------------------------------------------
  function setGateLoading(progress) {
    state.samplesLoading = true;
    els.gateBtn.disabled = true;
    els.gateBtn.textContent = "正在准备钢琴音色…";
    els.gateProgressWrap.classList.remove("hidden");
    var pct = Math.round(clamp(progress, 0, 1) * 100);
    els.gateProgressBar.style.width = pct + "%";
    els.gateStatus.textContent = "正在渲染钢琴采样 " + pct + "%（6 音区 × 3 力度层）";
  }

  function setGateReady() {
    state.samplesLoading = false;
    state.samplesReady = true;
    els.gateBtn.disabled = false;
    els.gateBtn.textContent = "开始聆听";
    els.gateProgressWrap.classList.add("hidden");
    els.gateStatus.textContent = "钢琴采样已就绪 · 6 音区 × 3 力度层 · 带混响与踏板";
  }

  function setGateFallback(message) {
    state.samplesLoading = false;
    state.samplesReady = false;
    els.gateBtn.disabled = false;
    els.gateBtn.textContent = "开始聆听（合成音色）";
    els.gateProgressWrap.classList.add("hidden");
    els.gateStatus.textContent = "采样加载失败，已回退合成音色：" + String(message || "未知错误");
  }

  function kickSamples() {
    if (state.samplesLoading || state.samplesReady || state.sampleFailed) return;
    var config = state.sampleConfig || {};
    var sampleVoices = (config.voices || []).filter(function (voiceId) {
      var def = state.definitions[voiceId];
      return def && String(def.engine || "") === "sample";
    });
    if (!sampleVoices.length) {
      state.samplesReady = true;
      setGateReady();
      return;
    }
    setGateLoading(0);
    Sound.prepareSamples(config, state.definitions, setGateLoading)
      .then(function () {
        state.samplesLoading = false;
        state.samplesReady = true;
        setGateReady();
      })
      .catch(function (err) {
        state.samplesLoading = false;
        state.sampleFailed = true;
        setGateFallback(err && err.message ? err.message : String(err));
      });
  }

  function unlockAudio() {
    if (state.audioReady || state.unlocking || state.samplesLoading) return;
    state.unlocking = true;
    els.gateBtn.disabled = true;
    els.gateError.textContent = "";
    Sound.unlock().then(function () {
      state.audioReady = true;
      state.unlocking = false;
      els.gate.classList.add("hidden");
      els.gateBtn.disabled = false;
      if (state.playing) {
        var now = songNowMs();
        state.audioTimelineStart = Sound.currentTime() - Math.max(0, now) / 1000;
        state.scoreCursor = findCursor(state.score, Math.max(0, now));
        state.visualCursor = state.scoreCursor;
        Sound.beginPlayback();
      }
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
      if (payload.sample_engine) state.sampleConfig = payload.sample_engine;
      var snapshot = payload.state || {};
      if (snapshot.voices && !state.voices.length) {
        applyVoices({
          voices: snapshot.voices,
          registers: snapshot.registers,
          max_polyphony: snapshot.max_polyphony,
          default: snapshot.defaults && snapshot.defaults.voice,
          definitions: payload.definitions,
          sample_engine: payload.sample_engine || snapshot.sample_engine
        });
      }
      applyServer(snapshot.server);
      updateCounts(snapshot.listeners, snapshot.audio_ready);
      if (snapshot.playback && snapshot.playback.playing) {
        // 同一次演奏的重连不重置时间轴；只有新演奏/首次加载才应用快照
        if (!state.playing || snapshot.playback.playback_id !== state.playbackId) {
          applySnapshot(snapshot.playback, payload.score || []);
        }
      } else if (snapshot.playback && !snapshot.playback.playing && state.playing) {
        Sound.stopAll();
        resetPlaybackUI(false);
      } else if (!state.playing) {
        resetPlaybackUI(false);
      }
      kickSamples();
    });

    on("start", function (data) {
      startPlayback(data);
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

  function sortScore(score) {
    return (score || []).slice().sort(function (a, b) {
      return (Number(a.at) || 0) - (Number(b.at) || 0);
    });
  }

  function findCursor(score, elapsed) {
    var index = 0;
    while (index < score.length && Number(score[index].at) < elapsed) index++;
    return index;
  }

  function startPlayback(data) {
    state.playing = true;
    state.playbackId = data.playback_id || "";
    state.title = data.title || "AI 演奏";
    state.voiceId = data.voice || state.voiceId;
    state.totalMs = Number(data.total_ms) || 0;
    state.leadMs = Number(data.lead_ms) || state.defaults.lookahead_ms || 1200;
    state.range = Array.isArray(data.range) ? data.range : state.range;
    state.pedal = !!data.pedal;
    state.tempo = data.tempo || null;
    state.timeSignature = data.time_signature || null;
    state.tracks = Number(data.tracks) || 1;
    state.scheduleAheadMs = Number(state.sampleConfig.schedule_ahead_ms) || 8000;
    state.score = sortScore(data.score || []);
    state.scoreCursor = 0;
    state.visualCursor = 0;
    state.songStartPerf = performance.now() + state.leadMs;
    state.audioTimelineStart = null;
    if (state.audioReady) {
      state.audioTimelineStart = Sound.currentTime() + state.leadMs / 1000;
      Sound.beginPlayback();
    }
    var extra = state.tempo ? " · " + state.tempo + " BPM · " + state.tracks + " 轨" : "";
    setPlaybackHeader({
      title: state.title,
      subtitle: (data.voice_label || data.voice) + " · " +
                (data.register_label || "音区") + " · " +
                (data.note_count || state.score.length) + " 个音" + extra,
      voice: data.voice_label || data.voice || "音色",
      register: data.register_label || "音区",
      count: data.note_count || state.score.length
    });
    els.elapsed.textContent = "0:00";
    els.duration.textContent = formatTime(state.totalMs);
    els.progressBar.style.width = "0%";
    highlightVoice(state.voiceId);
    highlightRegister(data.register || "");
  }

  function applySnapshot(playback, score) {
    state.playing = true;
    state.playbackId = playback.playback_id || "";
    state.title = playback.title || "AI 演奏";
    state.voiceId = playback.voice || state.voiceId;
    state.totalMs = Number(playback.total_ms) || 0;
    state.leadMs = Number(playback.lead_ms) || state.defaults.lookahead_ms || 1200;
    state.range = Array.isArray(playback.range) ? playback.range : state.range;
    state.pedal = !!playback.pedal;
    state.tempo = playback.tempo || null;
    state.timeSignature = playback.time_signature || null;
    state.tracks = Number(playback.tracks) || 1;
    state.scheduleAheadMs = Number(state.sampleConfig.schedule_ahead_ms) || 8000;
    state.score = sortScore(score || []);
    var elapsed = Number(playback.elapsed_ms) || 0;
    state.songStartPerf = performance.now() - elapsed;
    state.audioTimelineStart = null;
    if (state.audioReady) {
      state.audioTimelineStart = Sound.currentTime() - elapsed / 1000;
      Sound.beginPlayback();
    }
    var cursor = findCursor(state.score, elapsed);
    state.scoreCursor = cursor;
    state.visualCursor = cursor;
    var extra = state.tempo ? " · " + state.tempo + " BPM · " + state.tracks + " 轨" : "";
    setPlaybackHeader({
      title: state.title,
      subtitle: (playback.voice_label || playback.voice) + " · " +
                (playback.register_label || "音区") + " · " +
                (playback.note_count || state.score.length) + " 个音" + extra,
      voice: playback.voice_label || playback.voice || "音色",
      register: playback.register_label || "音区",
      count: playback.note_count || state.score.length
    });
    els.duration.textContent = formatTime(state.totalMs);
    highlightVoice(state.voiceId);
    highlightRegister(playback.register || "");
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
    state.score = [];
    state.scoreCursor = 0;
    state.visualCursor = 0;
    state.audioTimelineStart = null;
    state.songStartPerf = 0;
    state.range = [48, 72];
    state.pedal = false;
    state.tempo = null;
    state.timeSignature = null;
    state.tracks = 1;
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
    if (data.sample_engine) state.sampleConfig = data.sample_engine;
    if (data.definitions) applyDefinitions(data.definitions);
    if (Number(data.max_polyphony)) state.maxPolyphony = Number(data.max_polyphony);
    if (data.default) state.defaultVoice = String(data.default);
    if (!state.defaultVoice && state.voices.length) state.defaultVoice = state.voices[0].id;
    if (!state.voiceId) state.voiceId = state.defaultVoice;
    renderVoiceList();
    renderRegisters();
    kickSamples();
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
      desc.textContent = (String(voice.engine || "") === "sample" ? "采样 · " : "") +
                         (voice.description || "");
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
    highlightRegister("");
  }

  function highlightRegister(key) {
    var children = els.registerList.children;
    for (var i = 0; i < children.length; i++) {
      children[i].classList.toggle("active", !!key && children[i].dataset.key === key);
    }
  }

  // -------------------------------------------------------------------------
  // 画布：落键瀑布 + 键盘高亮（直接读整份曲谱，避免逐音事件丢失）
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

  function draw(songNow) {
    if (!canvas || !g) return;
    var width = canvas.clientWidth || 1;
    var height = canvas.clientHeight || 1;
    if (!width || !height) return;
    var kb = rebuildKeyboard(width, height);
    var lead = Math.max(200, state.leadMs);

    g.clearRect(0, 0, width, height);

    g.strokeStyle = "rgba(255, 255, 255, 0.022)";
    g.lineWidth = 1;
    var step = Math.max(40, kb.top / 7);
    for (var y = step; y < kb.top; y += step) {
      g.beginPath();
      g.moveTo(0, Math.round(y) + 0.5);
      g.lineTo(width, Math.round(y) + 0.5);
      g.stroke();
    }

    var score = state.score;
    var cursor = state.visualCursor;
    while (cursor < score.length &&
           Number(score[cursor].at) + Number(score[cursor].dur) + 600 < songNow) {
      cursor++;
    }
    state.visualCursor = cursor;

    var activeKeys = new Set();
    var drawn = 0;
    for (var i = cursor; i < score.length; i++) {
      var ev = score[i];
      var at = Number(ev.at) || 0;
      var dur = Number(ev.dur) || 0;
      if (at - lead > songNow + lead * 2) break;
      var midis = ev.midis || [];
      if (songNow >= at && songNow <= at + Math.max(140, Math.min(dur, 3200))) {
        midis.forEach(function (midi) { activeKeys.add(midi); });
      }
      if (songNow < at - lead) continue;
      if (songNow > at + dur + 300) continue;
      var progress = clamp((songNow - (at - lead)) / lead, 0, 1);
      var bottom = progress * kb.top;
      var barHeight = clamp((dur / lead) * kb.top * 0.85, 8, kb.top * 0.62);
      if (bottom < 0) continue;
      for (var m = 0; m < midis.length; m++) {
        var rect = kb.rects.get(midis[m]);
        if (!rect) continue;
        var alpha = 0.16 + 0.5 * clamp(Number(ev.velocity) || 0.85, 0.05, 1);
        g.fillStyle = "rgba(74, 116, 240, " + alpha.toFixed(3) + ")";
        roundRect(rect.x + 1, bottom - barHeight,
                  Math.max(2, rect.w - 2), barHeight, 3);
        g.fill();
        g.fillStyle = "rgba(188, 207, 255, 0.92)";
        g.fillRect(rect.x + 1, bottom - 2, Math.max(2, rect.w - 2), 2);
      }
      drawn++;
    }

    g.lineWidth = 1;
    kb.whites.forEach(function (key) {
      var isActive = activeKeys.has(key.midi);
      g.fillStyle = isActive ? "#dde5f4" : "#0b0d12";
      g.fillRect(key.x, kb.top, Math.max(1, key.w - 1), kb.height);
      g.strokeStyle = "#1a1f29";
      g.strokeRect(key.x + 0.5, kb.top + 0.5, Math.max(1, key.w - 1), kb.height - 1);
      if (key.midi % 12 === 0 && key.w > 20) {
        g.fillStyle = isActive ? "#0a0c11" : "rgba(155, 165, 182, 0.62)";
        g.font = "10px " + '"SFMono-Regular", "Consolas", monospace';
        g.textAlign = "center";
        g.textBaseline = "bottom";
        g.fillText("C" + (Math.floor(key.midi / 12) - 1),
                   key.x + key.w / 2, kb.top + kb.height - 6);
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

    if (!state.playing && drawn === 0) {
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

  function songNowMs() {
    if (Sound.isReady() && state.audioTimelineStart != null) {
      return (Sound.currentTime() - state.audioTimelineStart) * 1000;
    }
    if (state.songStartPerf) {
      return performance.now() - state.songStartPerf;
    }
    return 0;
  }

  function scheduleAhead(songNow) {
    if (!state.playing || !state.score.length || !Sound.isReady() ||
        state.audioTimelineStart == null) return;
    var until = songNow + (state.scheduleAheadMs || 8000);
    while (state.scoreCursor < state.score.length) {
      var ev = state.score[state.scoreCursor];
      if (Number(ev.at) > until) break;
      state.scoreCursor++;
      if (Number(ev.at) + Number(ev.dur) < songNow - 120) continue;
      var startTime = state.audioTimelineStart + Number(ev.at) / 1000;
      if (startTime < Sound.currentTime() - 0.05) continue;
      Sound.scheduleEvent(ev, startTime, state.pedal);
    }
  }

  function updateTimelineUI(songNow) {
    if (!state.playing || state.totalMs <= 0) return;
    var now = performance.now();
    if (now - state.lastUiAt < 100) return;
    state.lastUiAt = now;
    var ratio = clamp(songNow / state.totalMs, 0, 1);
    els.progressBar.style.width = (ratio * 100).toFixed(2) + "%";
    els.elapsed.textContent = formatTime(clamp(songNow, 0, state.totalMs));
    if (songNow > state.totalMs + 1200) {
      state.playing = false;
      els.progressBar.style.width = "100%";
      els.elapsed.textContent = formatTime(state.totalMs);
      els.subtitle.textContent = "演奏结束 · 等待下一段旋律";
    }
  }

  function frame() {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    var songNow = songNowMs();
    if (state.playing) {
      scheduleAhead(songNow);
      updateTimelineUI(songNow);
    }
    var now = performance.now();
    if (now - state.lastLevelAt > 100) {
      state.lastLevelAt = now;
      els.levelBar.style.width = (Sound.getLevel() * 100).toFixed(1) + "%";
    }
    draw(songNow);
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
      gateError: $("gateError"), gateProgressWrap: $("gateProgressWrap"),
      gateProgressBar: $("gateProgressBar"), gateStatus: $("gateStatus"),
      elapsed: $("elapsed"), duration: $("duration"),
      progressBar: $("progressBar"), muteBtn: $("muteBtn"), volume: $("volume"),
      levelBar: $("levelBar"),
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
