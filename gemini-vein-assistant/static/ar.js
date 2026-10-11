// Vein Assist in passthrough AR (Meta Quest browser).
// A glass chat panel with the mic orb under it, and the arm hologram beside it, floating in the room.
// Pinch the orb to turn the mic on or off; point at the panel or the arm and pinch to move it.
// The Quest runs one AR page at a time, so the arm lives here too. The 3D parts come from the
// arm page's ar-kit.js (served at /arm/), and load only on devices that can do AR.

const app = window.veinAssist;
const button = document.getElementById("ar-btn");
const hint = document.getElementById("ar-hint");
const preview = new URLSearchParams(location.search).has("ar-preview"); // desktop check, no headset
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const PANEL = { w: 0.5, px: [1024, 900] };   // metres wide, canvas pixels
const ORB = { size: 0.175, px: [560, 460] }; // the orb itself stays about 7.5 cm across
const COLORS = { off: "#8a97a8", armed: "#2dd4bf", addressed: "#2dd4bf", generating: "#a5b4fc", speaking: "#7cb3ff" };
const ICONS = { // Lucide paths, 24x24
  mic: ["M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3"],
  micOff: ["M2 2l20 20", "M18.89 13.23A7 7 0 0 0 19 12v-2", "M5 10v2a7 7 0 0 0 12 5", "M15 9.34V5a3 3 0 0 0-5.68-1.33", "M9 9v3a3 3 0 0 0 5.12 2.12", "M12 19v3"],
  spark: ["M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z", "M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"],
};

// ---------- drawing ----------

const plain = (text) => (text || "")
  .replace(/\*\*(.+?)\*\*/g, "$1").replace(/`/g, "")
  .replace(/^\s*[-*•]\s+/gm, "• ").replace(/^#+\s*/gm, "");

function wrap(g, text, maxWidth) {
  const lines = [];
  for (const para of plain(text).split("\n")) {
    let line = "";
    for (const word of para.split(/\s+/).filter(Boolean)) {
      const test = line ? `${line} ${word}` : word;
      if (g.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    lines.push(line);
  }
  return lines;
}

function drawPanel(g, w, h, FONT) {
  const pad = 44;
  g.fillStyle = "rgba(6, 14, 24, 0.66)";
  g.strokeStyle = "rgba(205, 240, 255, 0.35)";
  g.lineWidth = 3;
  g.beginPath();
  g.roundRect(4, 4, w - 8, h - 8, 44);
  g.fill();
  g.stroke();

  // header: name and what the assistant is doing
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillStyle = "#e8edf2";
  g.font = `600 36px ${FONT}`;
  g.fillText("Vein Assist", pad, 62);
  g.textAlign = "right";
  g.fillStyle = COLORS[app.state()] || "#9aa6b2";
  g.font = `500 28px ${FONT}`;
  g.fillText(app.status(), w - pad, 62);
  g.fillStyle = "rgba(205, 240, 255, 0.18)";
  g.fillRect(pad, 104, w - pad * 2, 2);

  // messages, newest at the bottom; older ones scroll off the top
  const top = 128, lineH = 44;
  let y = h - 40;
  g.textBaseline = "alphabetic";
  const caption = app.state() === "addressed" ? app.caption() : "";
  if (caption) {
    g.font = `italic 500 30px ${FONT}`;
    g.fillStyle = "#9aa6b2";
    g.textAlign = "center";
    g.fillText(`“${caption.length > 60 ? "…" + caption.slice(-58) : caption}”`, w / 2, y);
    y -= 60;
  }
  const messages = app.messages();
  if (!messages.length) {
    g.textAlign = "center";
    g.fillStyle = "#c9d4df";
    g.font = `500 34px ${FONT}`;
    g.fillText("Say “Gemini”, then your question.", w / 2, (top + y) / 2);
    return;
  }
  g.save();
  g.beginPath();
  g.rect(0, top - 8, w, y - top + 48);
  g.clip(); // older messages fade off under the header
  for (let i = messages.length - 1; i >= 0 && y > top; i--) {
    const m = messages[i];
    if (m.role === "user") {
      g.font = `500 32px ${FONT}`;
      const lines = wrap(g, m.text, w * 0.62);
      const boxW = Math.min(w * 0.62, Math.max(...lines.map((l) => g.measureText(l).width))) + 44;
      const boxH = lines.length * lineH + 26;
      y -= boxH;
      g.fillStyle = "rgba(205, 240, 255, 0.14)";
      g.beginPath();
      g.roundRect(w - pad - boxW, y, boxW, boxH, 26);
      g.fill();
      g.fillStyle = "#e8edf2";
      g.textAlign = "right";
      lines.forEach((l, k) => g.fillText(l, w - pad - 22, y + 46 + k * lineH));
    } else {
      g.font = `500 34px ${FONT}`;
      const text = m.text || (m.done ? (m.error ? "Couldn't get an answer." : "") : "…");
      const lines = wrap(g, text, w - pad * 2);
      y -= lines.length * lineH + 6;
      g.fillStyle = m.error && !m.text ? "#f97066" : "#ffffff";
      g.textAlign = "left";
      lines.forEach((l, k) => g.fillText(l, pad, y + 38 + k * lineH));
    }
    y -= 28;
  }
  g.restore();
}

function drawIcon(g, paths, cx, cy, size, color) {
  g.save();
  g.translate(cx - size / 2, cy - size / 2);
  g.scale(size / 24, size / 24);
  g.strokeStyle = color;
  g.lineWidth = 2;
  g.lineCap = "round";
  g.lineJoin = "round";
  paths.forEach((d) => g.stroke(new Path2D(d)));
  g.restore();
}

function drawOrb(g, w, h, now, FONT) {
  const state = app.state();
  const color = COLORS[state] || COLORS.off;
  const cx = w / 2, cy = 190, r = 120;
  const t = reduceMotion ? 0 : now / 1000;

  if (state === "addressed") { // ripples: being talked to
    for (const offset of [0, 0.8]) {
      const k = ((t + offset) % 1.6) / 1.6;
      g.strokeStyle = color;
      g.globalAlpha = 0.55 * (1 - k);
      g.lineWidth = 6;
      g.beginPath();
      g.arc(cx, cy, r + k * 60, 0, Math.PI * 2);
      g.stroke();
    }
    g.globalAlpha = 1;
  }
  g.fillStyle = state === "addressed" ? color : state === "armed" ? "rgba(45, 212, 191, 0.18)" : "rgba(14, 20, 28, 0.85)";
  g.strokeStyle = state === "off" ? "rgba(205, 240, 255, 0.35)" : color;
  g.lineWidth = state === "generating" ? 3 : 6;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  g.stroke();

  if (state === "generating") { // spinning arc: thinking
    const a = t * Math.PI * 2;
    g.strokeStyle = color;
    g.lineWidth = 10;
    g.lineCap = "round";
    g.beginPath();
    g.arc(cx, cy, r + 14, a, a + Math.PI * 1.2);
    g.stroke();
    drawIcon(g, ICONS.spark, cx, cy, 112, color);
  } else if (state === "speaking") { // bouncing bars: speaking
    g.fillStyle = color;
    [0, -0.6, -0.3, -0.75, -0.45].forEach((delay, i) => {
      const k = reduceMotion ? 0.6 : 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(((t + delay) / 0.9) * Math.PI * 2));
      const bh = 120 * k;
      g.beginPath();
      g.roundRect(cx - 64 + i * 30, cy - bh / 2, 14, bh, 7);
      g.fill();
    });
  } else {
    drawIcon(g, state === "off" ? ICONS.micOff : ICONS.mic, cx, cy, 104, state === "addressed" ? "#0b0f14" : color);
  }

  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = state === "off" ? "#c9d4df" : color;
  g.font = `600 40px ${FONT}`;
  g.fillText(state === "off" ? "Pinch to turn the mic on" : app.status(), cx, h - 44);
}

// ---------- scene ----------

let ready = null;
function setUp() { // load the 3D parts and build the scene once
  ready ??= (async () => {
    const kit = await import("/arm/ar-kit.js");
    const { THREE, FONT } = kit;
    await document.fonts.ready;
    const stage = kit.createStage({ preview });

    // chat panel with the mic orb under it (they move together)
    const chat = new THREE.Group();
    const panelH = PANEL.w * PANEL.px[1] / PANEL.px[0];
    const panel = kit.plane(PANEL.w, panelH);
    panel.material.map = kit.canvasTexture((g, w, h) => drawPanel(g, w, h, FONT), ...PANEL.px);
    const orbH = ORB.size * ORB.px[1] / ORB.px[0];
    const orb = kit.plane(ORB.size, orbH);
    orb.material.map = kit.canvasTexture((g, w, h) => drawOrb(g, w, h, performance.now(), FONT), ...ORB.px);
    orb.position.y = -panelH / 2 - orbH / 2 - 0.015;
    const panelHit = kit.hitArea(PANEL.w + 2 * kit.GRAB_MARGIN, panelH + 2 * kit.GRAB_MARGIN);
    const orbHit = kit.hitArea(ORB.size * 0.8, orbH * 1.1); // pinch here toggles the mic
    orbHit.position.set(orb.position.x, orb.position.y, -0.002);
    chat.add(panel, orb, panelHit, orbHit);
    stage.scene.add(chat);
    stage.onEnd((reason) => {
      if (reason !== "paused") return;
      hint.textContent = "AR was closed while you were in another window. Tap View in AR to go back in.";
      hint.hidden = false;
    });
    stage.addButton(orbHit, () => { app.resumeAudio(); app.toggleMic(); }, "mic orb");
    stage.addGrabbable(chat, panelHit, "chat panel");

    // the arm, beside the chat (if the arm page is installed next to Vein Assist)
    let arm = null;
    try {
      const html = await (await fetch("/arm/")).text();
      const svg = new DOMParser().parseFromString(html, "text/html").querySelector("svg.arm");
      if (svg) {
        arm = await kit.createArm(svg);
        stage.scene.add(arm.group);
        stage.addGrabbable(arm.group, arm.hit, "arm");
        await arm.setSite(app.region()?.site);
      }
    } catch (err) {
      console.warn("Arm page not available, showing the chat only", err);
    }

    stage.onLayout(() => {
      stage.placeInFront(chat, { distance: 0.8, right: arm ? -0.17 : 0, down: 0.02 });
      if (arm) stage.placeInFront(arm.group, { distance: 0.75, right: 0.27, down: 0.08 });
      if (preview) stage.camera.lookAt(chat.position.x + (arm ? 0.08 : 0), chat.position.y - 0.05, chat.position.z);
    });

    // redraw the panel when the chat changes (at most ~12 times a second while an answer streams in)
    let dirty = true, lastPanel = 0, lastSite = app.region()?.site;
    app.onChange(() => {
      dirty = true;
      const site = app.region()?.site;
      if (arm && site !== lastSite) {
        lastSite = site;
        arm.setSite(site);
      }
    });
    let lastOrbKey = null;
    stage.onFrame((now) => {
      if (dirty && now - lastPanel > 80) {
        panel.material.map.redraw();
        dirty = false;
        lastPanel = now;
      }
      const state = app.state();
      const key = state + "|" + app.status(); // e.g. "Loading speech model" -> "Listening for Gemini"
      const animated = !reduceMotion && ["addressed", "generating", "speaking"].includes(state);
      if (animated || key !== lastOrbKey) orb.material.map.redraw();
      lastOrbKey = key;
      arm?.update(now);
    });

    // Read-only status, handy for checking AR from the browser console.
    app.ar = {
      presenting: stage.presenting,
      chatPosition: () => chat.getWorldPosition(new THREE.Vector3()).toArray().map((v) => +v.toFixed(3)),
      armPosition: () => arm?.group.getWorldPosition(new THREE.Vector3()).toArray().map((v) => +v.toFixed(3)),
      region: () => arm?.site() ?? null,
      holding: stage.holding,
    };
    return stage;
  })();
  return ready;
}

async function enterAR() {
  button.disabled = true;
  try {
    const stage = await setUp();
    hint.hidden = true;
    await stage.enterAR();
  } catch (err) {
    console.error(err);
    hint.textContent = "Couldn't start AR: " + (err.message || err);
    hint.hidden = false;
  } finally {
    button.disabled = false;
  }
}

if (preview) {
  setUp();
} else if (navigator.xr) {
  navigator.xr.isSessionSupported("immersive-ar").then(async (ok) => {
    if (!ok) return;
    const kitThere = await fetch("/arm/ar-kit.js", { method: "HEAD" }).then((r) => r.ok, () => false);
    if (!kitThere) return; // needs the vein-arm-guide folder next to Vein Assist
    button.hidden = false;
    button.addEventListener("click", enterAR);
    setUp(); // get ready in the background so the button starts AR straight away
  }).catch(() => {});
} else if (!window.isSecureContext && /OculusBrowser|Quest/i.test(navigator.userAgent)) {
  // On the Quest over plain http, the browser blocks AR and the mic: point to the https link.
  hint.textContent = `For AR and the mic on this headset, open https://${location.hostname}:8443`;
  hint.hidden = false;
}
