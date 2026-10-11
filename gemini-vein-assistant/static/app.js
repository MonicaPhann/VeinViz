// Vein Assist client: wake word -> question -> live chat + spoken answer.
(() => {
  "use strict";

  const ORIGIN = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random()).slice(2);
  const WAKE = /\b(gemini|gemini's|jemini|gemeni|gemmini|geminy|jeminy|jeminai|gemina)\b[\s,.:;!?-]*/i;
  const STOP_WORDS = /^(stop|cancel|quiet|be quiet|shh+|never ?mind|that'?s all|thank you|thanks)\W*$/i;
  const SUBMIT_AFTER_MS = 500;     // pause after the last transcribed phrase before sending
  const ADDRESSED_TIMEOUT_MS = 8000; // give up if "Gemini" is followed by nothing

  const $ = (id) => document.getElementById(id);
  const chat = $("chat"), thread = $("thread"), empty = $("empty"), jump = $("jump");
  const mic = $("mic"), statusEl = $("status"), caption = $("caption"), conn = $("conn");

  const ICON_SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>';
  const ICON_CAM = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>';

  // ---------------- state ----------------
  let state = "off";            // off | armed | addressed | generating | speaking
  let micOn = false;
  let buffer = "";              // question being collected after the wake word
  let submitTimer = null, addressedTimer = null;
  let myReplyId = null;         // assistant message this device should speak
  let awaitingReply = false;
  const items = new Map();      // id -> { data, el, body }
  let region = null;            // latest recommended region ({ site, label, reason })
  let pageVersion = null;       // the server's page-file version this page was loaded with
  const watchers = new Set();   // the AR view redraws when anything here changes
  const changed = () => watchers.forEach((fn) => fn());

  const STATUS = {
    off: "Mic off · tap to start",
    armed: "Listening for “Gemini”",
    addressed: "Listening…",
    generating: "Thinking…",
    speaking: "Speaking…",
  };

  function setState(next, note) {
    if (!note && next === "armed" && listener.paused) note = "Mic paused · tap or pinch to start it";
    state = next;
    mic.dataset.state = next;
    mic.setAttribute("aria-pressed", String(micOn));
    mic.setAttribute("aria-label", micOn ? "Turn microphone off" : "Turn microphone on");
    statusEl.textContent = note || STATUS[next];
    if (next !== "addressed") caption.textContent = "";
    changed();
  }

  function idleState() { return micOn ? "armed" : "off"; }

  // ---------------- markdown (tiny, safe) ----------------
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

  function md(src) {
    const out = [];
    let list = null, para = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${para.join("<br>")}</p>`); para = []; } };
    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of src.split("\n")) {
      const line = raw.replace(/^#{1,6}\s+(.*)$/, "**$1**");
      const ul = line.match(/^\s*[-*•]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        const tag = ul ? "ul" : "ol";
        if (list !== tag) { closeList(); out.push(`<${tag}>`); list = tag; }
        out.push(`<li>${inline((ul || ol)[1])}</li>`);
      } else if (!line.trim()) {
        flushPara(); closeList();
      } else {
        closeList(); para.push(inline(line));
      }
    }
    flushPara(); closeList();
    return out.join("");
  }

  // ---------------- chat rendering ----------------
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;

  function withScroll(fn) {
    const stick = nearBottom();
    fn();
    if (stick) chat.scrollTop = chat.scrollHeight;
    else jump.hidden = false;
  }

  function renderBody(item) {
    const { data, body } = item;
    if (!data.done && !data.text) {
      body.innerHTML = '<span class="dots" aria-label="Generating"><i></i><i></i><i></i></span>';
      return;
    }
    body.innerHTML = md(data.text || "");
    if (!data.done) {
      const target = body.lastElementChild && body.lastElementChild.matches("ul,ol")
        ? body.lastElementChild.lastElementChild : body.lastElementChild;
      (target || body).insertAdjacentHTML("beforeend", '<span class="caret" aria-hidden="true"></span>');
    }
    if (data.done && data.error) {
      body.insertAdjacentHTML("beforeend", `<p class="err">${esc(data.error)}</p>`);
    }
  }

  function renderMeta(item) {
    const { data, meta } = item;
    const who = data.role === "user" ? "You" : "Assistant";
    const cam = data.role === "assistant" && data.camera ? `<span class="chip">${ICON_CAM}IR frame</span>` : "";
    meta.innerHTML = `<span>${who}</span><span>${fmtTime(data.ts)}</span>${cam}`;
  }

  function addMessage(data) {
    empty.hidden = true;
    const el = document.createElement("article");
    el.className = `msg ${data.role}`;
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    const body = document.createElement("div");
    body.className = "msg-body";
    if (data.role === "assistant") {
      const stack = document.createElement("div");
      stack.className = "stack";
      stack.append(meta, body);
      el.insertAdjacentHTML("beforeend", `<span class="avatar">${ICON_SPARK}</span>`);
      el.append(stack);
    } else {
      el.append(meta, body);
    }
    const item = { data, el, body, meta };
    items.set(data.id, item);
    renderMeta(item);
    renderBody(item);
    withScroll(() => thread.append(el));
  }

  // ---------------- websocket ----------------
  let ws = null;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { conn.textContent = "Live"; conn.dataset.ok = "true"; };
    ws.onclose = () => {
      conn.textContent = "Reconnecting…"; conn.dataset.ok = "false";
      setTimeout(connect, 1500);
    };
    ws.onmessage = (e) => { onEvent(JSON.parse(e.data)); changed(); };
  }

  function onEvent(ev) {
    if (ev.type === "snapshot") {
      // After an update and a server restart, pick up the new page instead of running the old one.
      if (ev.version && pageVersion && ev.version !== pageVersion) { location.reload(); return; }
      pageVersion = ev.version || pageVersion;
      region = ev.region || null;
      items.clear();
      thread.querySelectorAll(".msg").forEach((n) => n.remove());
      empty.hidden = ev.messages.length > 0;
      ev.messages.forEach(addMessage);
      chat.scrollTop = chat.scrollHeight;
    } else if (ev.type === "message") {
      addMessage(ev.message);
      if (ev.message.role === "assistant" && ev.message.origin === ORIGIN && awaitingReply) {
        myReplyId = ev.message.id;
        awaitingReply = false;
        tts.reset();
      }
    } else if (ev.type === "meta") {
      const it = items.get(ev.id);
      if (it) { it.data.camera = ev.camera; renderMeta(it); }
    } else if (ev.type === "delta") {
      const it = items.get(ev.id);
      if (!it) return;
      it.data.text += ev.text;
      withScroll(() => renderBody(it));
      if (ev.id === myReplyId) tts.feed(ev.text);
    } else if (ev.type === "region") {
      region = ev.region;
    } else if (ev.type === "done") {
      const it = items.get(ev.id);
      if (!it) return;
      it.data.done = true;
      it.data.error = ev.error;
      withScroll(() => renderBody(it));
      if (ev.id === myReplyId) {
        if (ev.error && !it.data.text) tts.feed("Sorry, I couldn't get an answer.");
        tts.finish();
      }
    }
  }

  function ask(text) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setState(idleState(), "Not connected to the server");
      return;
    }
    tts.stop();
    awaitingReply = true;
    myReplyId = null;
    ws.send(JSON.stringify({ type: "ask", text, origin: ORIGIN }));
    setState("generating");
  }

  // ---------------- text to speech ----------------
  // Answers are spoken sentence by sentence. The server turns each sentence into audio (Piper: the
  // same voice on every device, and it works where the browser has no voices, like the Quest).
  // If the server can't, the browser's own voice is used for that sentence instead.
  const tts = (() => {
    const synth = window.speechSynthesis;
    const player = new Audio();
    let queue = [], pumping = -1, gen = 0, buf = "", finished = false, voice = null;
    let serverDownUntil = 0, stopCurrent = null;

    function pickVoice() {
      if (!synth) return null;
      const vs = synth.getVoices().filter((v) => /^en[-_]/i.test(v.lang));
      const prefs = ["Google US English", "Samantha", "Ava", "Allison", "Microsoft Aria", "Microsoft Jenny", "Karen", "Daniel"];
      for (const p of prefs) {
        const v = vs.find((x) => x.name.includes(p));
        if (v) return v;
      }
      return vs.find((v) => v.localService) || vs[0] || null;
    }
    if (synth) synth.onvoiceschanged = () => { voice = pickVoice(); };

    const clean = (s) => s.replace(/[*_`#>]/g, "").replace(/^\s*[-•]\s+/gm, "").replace(/\s+/g, " ").trim();

    async function serverAudio(text) { // a WAV for this sentence, or null if the server can't speak
      if (Date.now() < serverDownUntil) return null;
      try {
        const r = await fetch("/api/tts", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return URL.createObjectURL(await r.blob());
      } catch (err) {
        console.warn("Server voice unavailable, using the browser's", err);
        serverDownUntil = Date.now() + 60000; // try the server again in a minute
        return null;
      }
    }

    function playAudio(url) {
      return new Promise((resolve) => {
        const done = () => { player.onended = player.onerror = null; stopCurrent = null; resolve(); };
        stopCurrent = () => { player.pause(); done(); };
        player.onended = player.onerror = done;
        player.src = url;
        player.play().then(() => setState("speaking")).catch(done); // e.g. blocked: skip, don't hang
      });
    }

    function playBrowserVoice(text) {
      return new Promise((resolve) => {
        if (!synth) return resolve();
        const u = new SpeechSynthesisUtterance(text);
        voice = voice || pickVoice();
        if (voice) u.voice = voice;
        u.rate = 1.05;
        let watchdog = null;
        const done = () => { clearTimeout(watchdog); stopCurrent = null; resolve(); };
        watchdog = setTimeout(() => { synth.cancel(); done(); }, 8000); // never started: no voices here
        u.onstart = () => { clearTimeout(watchdog); setState("speaking"); };
        u.onend = u.onerror = done;
        stopCurrent = () => { synth.cancel(); done(); };
        synth.speak(u);
      });
    }

    function speak(text) {
      const t = clean(text);
      if (!t || !micOn) return;
      queue.push({ text: t, audio: serverAudio(t) }); // start making the audio straight away
      if (pumping !== gen) pump(gen);
    }

    async function pump(myGen) { // play sentences in order; stops if a newer answer or "stop" comes in
      pumping = myGen;
      while (queue.length && myGen === gen) {
        const item = queue.shift();
        const url = await item.audio;
        if (myGen !== gen) { if (url) URL.revokeObjectURL(url); break; }
        if (url) {
          await playAudio(url);
          URL.revokeObjectURL(url);
        } else {
          await playBrowserVoice(item.text);
        }
      }
      if (pumping === myGen) pumping = -1;
      if (myGen === gen) settle();
    }

    function settle() {
      if (pumping === gen || queue.length) return;
      if (finished) { myReplyId = null; setState(idleState()); }
      else if (state === "speaking") setState("generating");
    }

    function flush(all) {
      // Speak whole sentences as soon as they're complete.
      // Mid-stream, wait for the space after a period so "0." + "5 mL" isn't split.
      const re = all ? /[^.!?\n]+(?:[.!?]+(?=\s|$)|\n)/g : /[^.!?\n]+(?:[.!?]+(?=\s)|\n)/g;
      let m, last = 0;
      while ((m = re.exec(buf))) { speak(m[0]); last = re.lastIndex; }
      buf = buf.slice(last);
      if (all && buf.trim()) { speak(buf); buf = ""; }
    }

    return {
      reset() { gen++; queue = []; buf = ""; finished = false; stopCurrent?.(); },
      feed(text) { buf += text; flush(false); },
      finish() { flush(true); finished = true; settle(); },
      stop() { gen++; queue = []; buf = ""; finished = true; stopCurrent?.(); },
      warm() { voice = pickVoice(); },
    };
  })();

  // ---------------- wake word ----------------
  // Speech arrives from the server's local Whisper: live partials while someone
  // is talking, then a final transcript when they stop.
  let talking = false; // someone is speaking right now (voice activity)

  function afterWake(text) {
    const m = text.match(WAKE);
    return m ? text.slice(m.index + m[0].length) : null;
  }

  function becomeAddressed() {
    if (state === "speaking" || state === "generating") {
      tts.stop();
      myReplyId = null;
      awaitingReply = false;
    }
    if (state !== "addressed") { buffer = ""; setState("addressed"); }
    clearTimeout(addressedTimer);
    addressedTimer = setTimeout(() => {
      if (state === "addressed" && !buffer.trim() && !talking) setState(idleState());
    }, ADDRESSED_TIMEOUT_MS);
  }

  function submitBuffer() {
    clearTimeout(submitTimer);
    clearTimeout(addressedTimer);
    const q = buffer.replace(/\s+/g, " ").trim();
    buffer = "";
    if (!q) return;
    if (STOP_WORDS.test(q)) { setState(idleState()); return; }
    ask(q);
  }

  function maybeSubmit() {
    clearTimeout(submitTimer);
    if (state === "addressed" && buffer.trim() && !talking) submitTimer = setTimeout(submitBuffer, SUBMIT_AFTER_MS);
  }

  function onPartial(text) {
    if (state === "off") return;
    if (state !== "addressed" && !WAKE.test(text)) return; // ignore everything until the wake word
    becomeAddressed();
    const live = afterWake(text) ?? text;
    caption.textContent = `${buffer} ${live}`.replace(/\s+/g, " ").trim();
    changed();
  }

  function onFinal(text) {
    if (state === "off") return;
    if (state !== "addressed") {
      if (!WAKE.test(text)) return;
      becomeAddressed();
    }
    if (text.trim()) {
      const rest = afterWake(text);
      buffer += " " + (rest !== null ? rest : text);
    }
    caption.textContent = buffer.replace(/\s+/g, " ").trim();
    changed();
    maybeSubmit();
  }

  function onVoiceStart() {
    talking = true;
    clearTimeout(submitTimer); // they're still going: wait for the rest of the question
    if (state === "addressed") becomeAddressed();
  }

  function onVoiceEnd() { talking = false; }

  // ---------------- microphone -> server ----------------
  // Runs in the audio thread: downsample to 16 kHz mono int16, post 20 ms frames with their loudness.
  const WORKLET = `
    class Pcm16k extends AudioWorkletProcessor {
      constructor() { super(); this.ratio = sampleRate / 16000; this.pos = 0; this.sum = 0; this.cnt = 0;
        this.out = new Int16Array(320); this.n = 0; this.sq = 0; }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (!ch) return true;
        for (let i = 0; i < ch.length; i++) {
          this.sum += ch[i]; this.cnt++; this.pos++;
          if (this.pos >= this.ratio) {
            this.pos -= this.ratio;
            const v = Math.max(-1, Math.min(1, this.sum / this.cnt));
            this.sum = 0; this.cnt = 0; this.sq += v * v;
            this.out[this.n++] = v * 32767;
            if (this.n === 320) {
              this.port.postMessage({ pcm: this.out.buffer, rms: Math.sqrt(this.sq / 320) }, [this.out.buffer]);
              this.out = new Int16Array(320); this.n = 0; this.sq = 0;
            }
          }
        }
        return true;
      }
    }
    registerProcessor("pcm16k", Pcm16k);`;

  const listener = (() => {
    const START_FRAMES = 3, END_FRAMES = 35, MAX_FRAMES = 1000, PREROLL = 15; // 20 ms frames
    let ctx = null, stream = null, sock = null, ready = false;
    // Speech starts when the level rises well above the room's background noise. The floor is low
    // because some mics (like the Quest's in a browser) come through quietly.
    const MIN_THRESHOLD = 0.005;
    // Laptop mics sound best with the browser's voice processing; on some headsets (like the Quest)
    // that processing hands over pure silence, so after 3 s of exact zeros we switch to the raw mic.
    const MIC_MODES = {
      processed: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      raw: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    };
    const SILENT_FRAMES = 150; // 3 s of 20 ms frames
    let mode = "processed", silentFrames = 0, source = null, workletNode = null, switching = false, noSound = false;
    let analyser = null, analyserBuf = null, checked = false;

    // Build (or rebuild) the audio engine around the current mic stream.
    async function buildAudio(sampleRate) {
      if (ctx) { const old = ctx; ctx = null; old.close().catch(() => {}); }
      const c = sampleRate ? new AudioContext({ sampleRate }) : new AudioContext();
      // Browsers can start audio paused unless a tap started it (an AR pinch may not count).
      if (c.state === "suspended") c.resume().catch(() => {});
      c.onstatechange = () => { if (state === "armed") setState("armed"); else changed(); };
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await c.audioWorklet.addModule(url);
      const node = new AudioWorkletNode(c, "pcm16k");
      node.port.onmessage = (e) => onFrame(e.data);
      const mute = c.createGain();
      mute.gain.value = 0; // keeps the graph pulling audio without playing it back
      source = c.createMediaStreamSource(stream);
      analyser = c.createAnalyser(); // a second listening point, straight off the mic, for the report
      analyser.fftSize = 2048;
      analyserBuf = new Float32Array(analyser.fftSize);
      source.connect(node);
      source.connect(analyser);
      node.connect(mute).connect(c.destination);
      workletNode = node;
      ctx = c;
    }

    // Last resort before giving up: rebuild the engine at the mic's own sample rate.
    async function useMicRate() {
      switching = true;
      try {
        const rate = stream?.getAudioTracks()[0]?.getSettings().sampleRate;
        const target = rate && rate !== ctx?.sampleRate ? rate : 16000;
        await buildAudio(target);
        mode = `raw@${target}`;
        silentFrames = 0;
      } catch (err) {
        console.warn("Couldn't rebuild audio", err);
        mode = "raw@failed";
      } finally {
        switching = false;
      }
    }

    // If every way of reading the mic is silent, record 2.5 s with the browser's recorder (which
    // skips the audio engine) and let the server measure it. Tells us where the sound is lost.
    async function recorderCheck() {
      if (checked || !stream || !window.MediaRecorder) return;
      checked = true;
      try {
        const rec = new MediaRecorder(stream);
        const chunks = [];
        rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        const stopped = new Promise((resolve) => { rec.onstop = resolve; });
        rec.start();
        await new Promise((r) => setTimeout(r, 2500));
        rec.stop();
        await stopped;
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        await fetch("/api/mic-check", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
      } catch (err) {
        console.warn("Recorder check failed", err);
      }
    }

    // The headset can stop the mic (e.g. while the page is in the background): get a fresh one.
    function watchTrack(s) {
      const track = s.getAudioTracks()[0];
      if (track) track.onended = () => { if (stream === s && micOn) reacquire(); };
    }
    async function reacquire() {
      if (switching || !ctx) return;
      switching = true;
      try {
        const fresh = await navigator.mediaDevices.getUserMedia({ audio: mode === "processed" ? MIC_MODES.processed : MIC_MODES.raw });
        if (!ctx) { fresh.getTracks().forEach((t) => t.stop()); return; } // mic was turned off meanwhile
        source?.disconnect();
        stream?.getTracks().forEach((t) => t.stop());
        stream = fresh;
        watchTrack(fresh);
        source = ctx.createMediaStreamSource(fresh);
        source.connect(workletNode);
        source.connect(analyser);
        silentFrames = 0;
        if (noSound) { noSound = false; if (state === "armed") setState("armed"); }
      } catch (err) {
        console.warn("Couldn't restart the mic", err); // retried when the page is next in front
      } finally {
        switching = false;
      }
    }
    document.addEventListener("visibilitychange", () => {
      const track = stream?.getAudioTracks()[0];
      if (!document.hidden && micOn && track && track.readyState === "ended") reacquire();
    });

    async function useRawMic() {
      switching = true;
      try {
        const raw = await navigator.mediaDevices.getUserMedia({ audio: MIC_MODES.raw });
        if (!ctx) { raw.getTracks().forEach((t) => t.stop()); return; } // mic was turned off meanwhile
        source?.disconnect();
        stream?.getTracks().forEach((t) => t.stop());
        stream = raw;
        watchTrack(raw);
        source = ctx.createMediaStreamSource(raw);
        source.connect(workletNode);
        source.connect(analyser);
        mode = "raw";
        silentFrames = 0;
      } catch (err) {
        console.warn("Raw mic not available", err);
        mode = "raw"; // don't keep retrying
      } finally {
        switching = false;
      }
    }
    let seq = 0, lastFinal = 0, inSpeech = false, voiced = 0, silent = 0, frames = 0, noise = 0.004, preroll = [];
    // A short report to the server every few seconds while the mic is on, so problems show in its log.
    let stats = { frames: 0, sum: 0, peak: 0, starts: 0, engine: 0 }, statsTimer = null;
    function sendStats() {
      sendJson({
        type: "stats", sampleRate: ctx?.sampleRate, audio: ctx?.state, frames: stats.frames,
        level: +(stats.sum / Math.max(1, stats.frames)).toFixed(4), peak: +stats.peak.toFixed(4),
        threshold: +Math.max(MIN_THRESHOLD, noise * 3).toFixed(4), speechStarts: stats.starts,
        enginePeak: +stats.engine.toFixed(4),
        mode, mic: (() => {
          const t = stream?.getAudioTracks()[0];
          const set = t?.getSettings?.() || {};
          return t ? { label: t.label, muted: t.muted, enabled: t.enabled, state: t.readyState, rate: set.sampleRate } : null;
        })(),
      });
      stats = { frames: 0, sum: 0, peak: 0, starts: 0, engine: 0 };
    }

    const sendJson = (o) => { if (sock && sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(o)); };
    const sendPcm = (b) => { if (sock && sock.readyState === WebSocket.OPEN) sock.send(b); };

    function endUtterance() {
      if (!inSpeech) return;
      inSpeech = false; voiced = 0;
      sendJson({ type: "end", id: seq });
      onVoiceEnd();
    }

    function onFrame({ pcm, rms }) {
      // A real mic always picks up some noise; exact zeros for 3 s means it isn't delivering sound.
      silentFrames = rms === 0 ? silentFrames + 1 : 0;
      if (silentFrames === SILENT_FRAMES && !switching) {
        if (mode === "processed") useRawMic();
        else if (mode === "raw") useMicRate();
        else if (micOn) {
          noSound = true;
          setState("armed", "No sound from the mic · check it isn't muted on this device");
          recorderCheck();
        }
      } else if (noSound && rms > 0) {
        noSound = false; // sound is back (e.g. unmuted)
        if (state === "armed") setState("armed");
      }
      if (analyser && stats.frames % 5 === 0) { // sample the second listening point 10x a second
        analyser.getFloatTimeDomainData(analyserBuf);
        for (const v of analyserBuf) stats.engine = Math.max(stats.engine, Math.abs(v));
      }
      stats.frames++;
      stats.sum += rms;
      stats.peak = Math.max(stats.peak, rms);
      const thr = Math.max(MIN_THRESHOLD, noise * 3);
      if (!inSpeech) {
        preroll.push(pcm);
        if (preroll.length > PREROLL) preroll.shift();
        if (rms > thr) voiced++;
        else { voiced = 0; noise = Math.min(0.05, noise * 0.98 + rms * 0.02); }
        if (voiced >= START_FRAMES) {
          inSpeech = true; silent = 0; frames = 0; seq++;
          sendJson({ type: "start", id: seq });
          preroll.forEach(sendPcm); preroll = [];
          stats.starts++;
          onVoiceStart();
        }
      } else {
        sendPcm(pcm); frames++;
        silent = rms > thr * 0.7 ? 0 : silent + 1;
        if (silent >= END_FRAMES || frames >= MAX_FRAMES) endUtterance();
      }
    }

    function openSocket() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      sock = new WebSocket(`${proto}://${location.host}/audio`);
      sock.binaryType = "arraybuffer";
      sock.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === "ready") { ready = true; if (state === "armed" || state === "off") setState(idleState()); }
        else if (ev.type === "partial" && ev.id > lastFinal) onPartial(ev.text);
        else if (ev.type === "final") { lastFinal = Math.max(lastFinal, ev.id); onFinal(ev.text); }
      };
      sock.onclose = () => {
        inSpeech = false; talking = false;
        if (!stream) return;
        ready = false;
        if (micOn) setState(idleState(), "Can’t reach the speech server · restart it with ./run.sh");
        setTimeout(() => { if (stream) openSocket(); }, 1500);
      };
    }

    return {
      get ready() { return ready; },
      get paused() { return !!ctx && ctx.state !== "running"; },
      resume() { if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {}); },
      async start() {
        mode = "processed";
        silentFrames = 0;
        checked = false;
        noSound = false;
        stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_MODES.processed });
        watchTrack(stream);
        await buildAudio();
        openSocket();
        statsTimer = setInterval(sendStats, 3000);
      },
      stop() {
        clearInterval(statsTimer);
        endUtterance();
        const s = stream; stream = null;
        if (s) s.getTracks().forEach((t) => t.stop());
        if (ctx) { ctx.close(); ctx = null; }
        if (sock) { sock.onclose = null; sock.close(); sock = null; }
        preroll = []; ready = false;
      },
    };
  })();

  let starting = false;
  async function toggleMic() {
    if (starting) return;
    if (!window.isSecureContext || !navigator.mediaDevices) {
      setState("off", "Mic needs localhost or HTTPS · this screen is view-only");
      return;
    }
    if (!micOn) {
      starting = true;
      try {
        await listener.start();
        micOn = true;
        tts.warm();
        setState("armed", listener.ready ? undefined : "Loading speech model…");
      } catch (err) {
        listener.stop();
        setState("off", err && err.name === "NotAllowedError"
          ? "Microphone blocked · allow it in the browser’s site settings"
          : "Couldn’t start the microphone");
      } finally {
        starting = false;
      }
    } else {
      micOn = false;
      clearTimeout(submitTimer); clearTimeout(addressedTimer);
      buffer = "";
      tts.stop();
      myReplyId = null;
      awaitingReply = false;
      listener.stop();
      setState("off");
    }
  }

  // ---------------- theme: light -> dark -> aero ----------------
  const root = document.documentElement, themeBtn = $("theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const NEXT = { light: "dark", dark: "aero", aero: "light" };
  const NAMES = { light: "light mode", dark: "dark mode", aero: "Aero theme" };
  const currentMode = () => root.dataset.theme || (systemDark.matches ? "dark" : "light");

  function syncThemeButton() {
    const mode = currentMode();
    root.dataset.mode = mode; // drives which icon shows
    themeBtn.setAttribute("aria-label", `Switch to ${NAMES[NEXT[mode]]}`);
    themeBtn.title = themeBtn.getAttribute("aria-label");
  }

  themeBtn.addEventListener("click", () => {
    const next = NEXT[currentMode()];
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (_) {}
    syncThemeButton();
  });
  systemDark.addEventListener("change", syncThemeButton);
  syncThemeButton();

  // ---------------- for the AR view (ar.js) ----------------
  window.veinAssist = {
    state: () => state,
    status: () => statusEl.textContent,
    caption: () => caption.textContent,
    messages: () => [...items.values()].map((it) => it.data),
    region: () => region,
    toggleMic: () => toggleMic(),
    ask: (text) => ask(text), // send a question as if it had been spoken after "Gemini"
    resumeAudio: () => { listener.resume(); if (state === "armed") setState("armed"); },
    onChange: (fn) => watchers.add(fn),
  };

  // ---------------- wiring ----------------
  mic.addEventListener("click", toggleMic);
  addEventListener("pointerdown", () => { if (micOn && listener.paused) { listener.resume(); setTimeout(() => setState(state), 300); } });
  chat.addEventListener("scroll", () => { if (nearBottom()) jump.hidden = true; }, { passive: true });
  jump.addEventListener("click", () => { chat.scrollTop = chat.scrollHeight; jump.hidden = true; });

  setState("off");
  connect();
})();
