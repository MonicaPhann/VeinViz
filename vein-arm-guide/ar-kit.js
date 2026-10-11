// Shared passthrough-AR pieces for the Meta Quest browser (WebXR "immersive-ar"):
//   createStage() - renderer, scene, hand/controller pinching, placing things in front of you
//   createArm()   - the arm hologram that lights up the recommended region
// Used by this folder's arm page (ar.js) and by Vein Assist's AR view (it loads /arm/ar-kit.js).
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js";

export { THREE };

// Same sites and wording as the flat arm page.
export const SITES = {
  median_cubital: ["Median cubital vein", "Elbow crease"],
  cephalic_antecubital: ["Cephalic vein", "Elbow, thumb side"],
  basilic_antecubital: ["Basilic vein", "Elbow, little-finger side"],
  cephalic_forearm: ["Cephalic vein", "Forearm, thumb side"],
  basilic_forearm: ["Basilic vein", "Forearm, little-finger side"],
  dorsal_hand: ["Dorsal hand veins", "Back of the hand"],
};

export const FONT = `Figtree, "Segoe UI", system-ui, sans-serif`;
// Grabbing is forgiving, because pointing with a hand wobbles:
export const GRAB_MARGIN = 0.12;  // metres of extra grab area around each thing
const AIM_TOLERANCE = 15;         // degrees: a pinch roughly pointed at something still grabs it
const REACH = 0.35;               // metres: a pinch with the hand this close grabs it wherever you point
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- small helpers ----------

export function canvasTexture(draw, w, h) {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.redraw = () => { // repaint the same canvas and upload it again
    const g = canvas.getContext("2d");
    g.clearRect(0, 0, w, h);
    draw(g, w, h);
    tex.needsUpdate = true;
  };
  tex.redraw();
  return tex;
}

export function plane(width, height) {
  return new THREE.Mesh(
    new THREE.PlaneGeometry(width, height),
    new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false }),
  );
}

// A grab area: invisible until you point at it, then a faint glowing frame (brighter while held).
export function hitArea(width, height) {
  const mesh = plane(width, height);
  const px = 512, py = Math.max(64, Math.round(512 * height / width));
  mesh.material.map = canvasTexture((g, w, h) => {
    g.fillStyle = "rgba(205, 240, 255, 0.10)";
    g.strokeStyle = "rgba(205, 240, 255, 0.85)";
    g.lineWidth = 6;
    g.beginPath();
    g.roundRect(4, 4, w - 8, h - 8, Math.min(w, h) * 0.12);
    g.fill();
    g.stroke();
  }, px, py);
  mesh.material.opacity = 0;
  mesh.position.z = -0.004; // just behind what it belongs to
  mesh.userData.glow = 0;
  return mesh;
}

// ---------- the stage ----------

export function createStage({ preview = false, logUrl = "/api/ar-log" } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(devicePixelRatio);
  renderer.setSize(innerWidth, innerHeight);
  renderer.setClearColor(0x000000, 0); // see-through everywhere nothing is drawn
  renderer.xr.enabled = true;
  renderer.xr.setReferenceSpaceType("local");
  renderer.domElement.className = "ar-canvas";
  renderer.domElement.style.removeProperty("display"); // three.js sets display:block; the page's CSS decides
  document.body.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.01, 20);
  addEventListener("resize", () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  const buttons = [];    // { hit, press }
  const grabbables = []; // { group, hit }
  const frameFns = [];
  const endFns = [];
  let layout = () => {};
  let placed = false, frames = 0;

  const viewer = () => (renderer.xr.isPresenting ? renderer.xr.getCamera() : camera);

  function faceViewer(group) { // stay upright and turn toward you
    const eye = new THREE.Vector3().setFromMatrixPosition(viewer().matrixWorld);
    eye.y = group.position.y;
    group.lookAt(eye);
  }

  // Put a group in front of you: distance ahead, `right` to the side, `down` below eye level (metres).
  function placeInFront(group, { distance = 0.65, right = 0, down = 0 } = {}) {
    const cam = viewer();
    const eye = new THREE.Vector3().setFromMatrixPosition(cam.matrixWorld);
    const forward = new THREE.Vector3();
    cam.getWorldDirection(forward);
    forward.y = 0;
    if (forward.lengthSq() < 1e-4) forward.set(0, 0, -1);
    forward.normalize();
    const side = new THREE.Vector3(-forward.z, 0, forward.x); // to your right
    group.position.copy(eye).addScaledVector(forward, distance).addScaledVector(side, right);
    group.position.y = eye.y - down;
    faceViewer(group);
  }

  // Pinch (or trigger): press a button you point at, otherwise pick up the thing you're
  // pointing at, roughly pointing at, or reaching for.
  const raycaster = new THREE.Raycaster();
  const rotation = new THREE.Matrix4();
  const local = new THREE.Vector3(), point = new THREE.Vector3(), normal = new THREE.Vector3();
  const quat = new THREE.Quaternion(), surface = new THREE.Plane();
  function aimFrom(matrix) { // point the raycaster along a hand's (or controller's) target ray
    rotation.identity().extractRotation(matrix);
    raycaster.ray.origin.setFromMatrixPosition(matrix);
    raycaster.ray.direction.set(0, 0, -1).applyMatrix4(rotation);
  }
  // How far the hand is from a rectangle on a grab area (half-width hw, half-height hh, centred on
  // it), and how many degrees the pointer misses it by.
  const meet = new THREE.Vector3(), closest = new THREE.Vector3();
  function nearMiss(hit, hw, hh) {
    local.copy(raycaster.ray.origin);
    hit.worldToLocal(local);
    const reach = Math.hypot(local.x - THREE.MathUtils.clamp(local.x, -hw, hw),
                             local.y - THREE.MathUtils.clamp(local.y, -hh, hh), local.z);
    hit.getWorldPosition(point);
    normal.set(0, 0, 1).applyQuaternion(hit.getWorldQuaternion(quat));
    surface.setFromNormalAndCoplanarPoint(normal, point);
    if (!raycaster.ray.intersectPlane(surface, meet)) return { reach, angle: Infinity, distance: Infinity };
    const distance = meet.distanceTo(raycaster.ray.origin);
    closest.copy(meet);
    hit.worldToLocal(closest);
    closest.set(THREE.MathUtils.clamp(closest.x, -hw, hw), THREE.MathUtils.clamp(closest.y, -hh, hh), 0);
    hit.localToWorld(closest);
    const angle = THREE.MathUtils.radToDeg(raycaster.ray.direction.angleTo(closest.sub(raycaster.ray.origin).normalize()));
    return { reach, angle, distance };
  }
  function pick(matrix) {
    aimFrom(matrix);
    const button = buttons.find((b) => b.hit.visible && raycaster.intersectObject(b.hit).length);
    if (button) return { button };
    // The grab area (with its margin) decides whether a pinch can grab something; when areas
    // overlap, the thing whose visible part you're pointing at (or reaching for) wins.
    let best = null;
    for (const g of grabbables) {
      const { width, height } = g.hit.geometry.parameters;
      const outer = nearMiss(g.hit, width / 2, height / 2);
      if (!(outer.reach < REACH || outer.angle <= AIM_TOLERANCE)) continue;
      const inner = nearMiss(g.hit, Math.max(0.01, width / 2 - GRAB_MARGIN), Math.max(0.01, height / 2 - GRAB_MARGIN));
      // Reaching wins over pointing; between equally good aims, the nearer thing (the one in front) wins.
      const score = outer.reach < REACH ? inner.reach - 10 : inner.angle + 0.001 * inner.distance;
      if (score < (best?.score ?? Infinity)) best = { g, score };
    }
    return best ? { grab: best.g } : {};
  }

  // ---- hands and controllers, read straight from the headset every frame ----
  // A held object follows its hand's pose directly (it isn't parented to anything), and a hold ends
  // when the pinch opens, when the headset drops that hand, or when AR is paused (e.g. another window
  // was opened). Nothing is left that can go stale.
  let session = null;
  const rays = new Map();  // XRInputSource -> target ray pose (Matrix4, room coordinates), this frame
  const holds = new Map(); // XRInputSource -> { item, offset } where offset = ray⁻¹ · object
  const poseMatrix = new THREE.Matrix4(), moved = new THREE.Matrix4(), scale = new THREE.Vector3();
  const hand = (source) => `${source.handedness || "?"} ${source.hand ? "hand" : source.targetRayMode}`;

  function rayPose(source, xrFrame) {
    const pose = xrFrame?.getPose(source.targetRaySpace, renderer.xr.getReferenceSpace());
    return pose ? new THREE.Matrix4().fromArray(pose.transform.matrix) : null;
  }
  function letGo(source, why) {
    const held = holds.get(source);
    if (!held) return;
    holds.delete(source);
    faceViewer(held.item.group);
    note(`${hand(source)} let go of the ${held.item.name} (${why})`);
  }
  function letGoOfEverything(why) { [...holds.keys()].forEach((source) => letGo(source, why)); }

  function onSelectStart(event) {
    const source = event.inputSource;
    letGo(source, "new pinch"); // in case a previous pinch never reported its end
    const ray = rayPose(source, event.frame) || rays.get(source);
    if (!ray) return note(`${hand(source)} pinched, but its position wasn't known`);
    const { button, grab } = pick(ray);
    if (button) {
      note(`${hand(source)} pressed the ${button.name}`);
      button.press();
    } else if (grab) {
      grab.group.updateMatrixWorld();
      holds.set(source, { item: grab, offset: ray.clone().invert().multiply(grab.group.matrixWorld) });
      note(`${hand(source)} picked up the ${grab.name}`);
    } else {
      note(`${hand(source)} pinched, not pointing at anything`);
    }
  }
  const onSelectEnd = (event) => letGo(event.inputSource, "pinch opened");
  function onSourcesChange(event) {
    for (const source of event.removed) {
      note(`${hand(source)} lost`);
      letGo(source, "hand lost");
    }
    for (const source of event.added) note(`${hand(source)} found`);
  }
  // When another window is opened, the Quest pauses AR ("visible-blurred") and disconnects the hands
  // and controllers, and often never hands them back. So if AR stays paused for a few seconds, close
  // it: you come back to the page and tap View in AR again, with everything working.
  const PAUSE_LIMIT_MS = 5000;
  let pauseTimer = null, endReason = "closed";
  function onVisibility() {
    note(`AR view ${session.visibilityState}`);
    clearTimeout(pauseTimer);
    if (session.visibilityState === "visible") return;
    letGoOfEverything("AR paused");
    const paused = session;
    pauseTimer = setTimeout(() => {
      if (session !== paused || paused.visibilityState === "visible") return;
      note("AR stayed paused, so it was closed; tap View in AR to go back in");
      endReason = "paused";
      paused.end().catch(() => {});
    }, PAUSE_LIMIT_MS);
  }

  // Show a frame around whatever a pinch would grab or press (brighter while held).
  function updateHighlights() {
    for (const item of [...buttons, ...grabbables]) item.hit.userData.glow = 0;
    for (const [source, ray] of rays) {
      const held = holds.get(source);
      if (held) {
        held.item.hit.userData.glow = 1;
        continue;
      }
      const { button, grab } = pick(ray);
      const target = (button || grab)?.hit;
      if (target) target.userData.glow = Math.max(target.userData.glow, 0.55);
    }
    for (const item of [...buttons, ...grabbables]) {
      const m = item.hit.material;
      m.opacity += (item.hit.userData.glow - m.opacity) * 0.25; // ease in and out
    }
  }

  function trackInput(xrFrame) {
    rays.clear();
    for (const source of session.inputSources) {
      if (source.targetRayMode === "gaze") continue;
      const ray = rayPose(source, xrFrame);
      if (ray) rays.set(source, ray);
    }
    for (const [source, held] of holds) {
      if (![...session.inputSources].includes(source)) { letGo(source, "hand gone"); continue; }
      const ray = rays.get(source);
      if (!ray) continue; // briefly untracked: leave it where it is until the hand is seen again
      moved.multiplyMatrices(ray, held.offset);
      moved.decompose(held.item.group.position, held.item.group.quaternion, scale);
    }
    updateHighlights();
  }

  function frame(time, xrFrame) {
    // Wait a couple of frames for the headset pose, then lay things out in front of you.
    if (!placed && (renderer.xr.isPresenting || preview) && ++frames > 2) {
      layout();
      placed = true;
    }
    const now = performance.now();
    if (session && xrFrame) trackInput(xrFrame);
    frameFns.forEach((fn) => fn(now));
    renderer.render(scene, camera);
  }

  async function enterAR() {
    const xr = await navigator.xr.requestSession("immersive-ar", { optionalFeatures: ["local-floor", "hand-tracking"] });
    placed = false;
    frames = 0;
    await renderer.xr.setSession(xr);
    session = xr;
    endReason = "closed";
    holds.clear();
    xr.addEventListener("selectstart", onSelectStart);
    xr.addEventListener("selectend", onSelectEnd);
    xr.addEventListener("inputsourceschange", onSourcesChange);
    xr.addEventListener("visibilitychange", onVisibility);
    xr.addEventListener("end", () => {
      clearTimeout(pauseTimer);
      letGoOfEverything("left AR");
      session = null;
      rays.clear();
      renderer.setAnimationLoop(null);
      note("left AR");
      endFns.forEach((fn) => fn(endReason)); // "paused" (closed after being away) or "closed"
    });
    renderer.setAnimationLoop(frame);
    note(`entered AR with ${[...xr.inputSources].map(hand).join(", ") || "no hands yet"}`);
  }

  // A short log of AR events (hands found/lost, pauses, pinches) sent to the server's log, so a
  // problem seen on the headset can be traced afterwards.
  const pendingNotes = [];
  function note(text) { pendingNotes.push(text); }
  setInterval(() => {
    if (!pendingNotes.length) return;
    fetch(logUrl, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: document.title, lines: pendingNotes.splice(0) }) }).catch(() => {});
  }, 2000);

  if (preview) { // desktop check: the same scene on a dark backdrop, standing in front of it
    document.documentElement.classList.add("ar-preview");
    camera.position.set(0, 1.6, 0);
    renderer.setAnimationLoop(frame);
  }

  return {
    THREE, renderer, scene, camera, preview, enterAR, placeInFront, faceViewer,
    presenting: () => renderer.xr.isPresenting,
    onLayout: (fn) => { layout = fn; placed = false; frames = 0; }, // lays out on the next frames
    onFrame: (fn) => frameFns.push(fn),
    onEnd: (fn) => endFns.push(fn), // called with "paused" or "closed" when AR ends
    addButton: (hit, press, name = "button") => buttons.push({ hit, press, name }),
    addGrabbable: (group, hit, name = "object") => grabbables.push({ group, hit, name }),
    holding: () => [...holds.values()].map((h) => h.item.name).join(", "), // what's held right now
  };
}

// ---------- the arm hologram ----------

const ARM_HEIGHT = 0.36;          // metres
const ARM_ASPECT = 240 / 630;     // the SVG's viewBox
const TEX_SCALE = 4;              // SVG units -> texture pixels
const LABEL_PX = [1024, 300];
const LABEL_WIDTH = 0.26;         // metres

// A soft glass hologram: light outline, faint fill, blue veins, cyan glow. Reads well over a real room.
const ARM_STYLE = `
  .outline > * { fill: none; stroke: rgba(205, 240, 255, 0.9); stroke-width: 3.5; stroke-linejoin: round; }
  .fill > * { fill: rgba(140, 200, 235, 0.22); }
  .vein { fill: none; stroke: #6cbcff; stroke-width: 3.2; stroke-linecap: round; }
  .vein.thin { stroke-width: 2.2; opacity: .65; }
  .crease { fill: none; stroke: rgba(205, 240, 255, .4); stroke-width: 2; stroke-linecap: round; stroke-dasharray: 3 5; }
  .zone, .zone .ring { display: none; }
  .zone .area { fill: #22d3ee; fill-opacity: .38; stroke: #67e8f9; stroke-width: 2.5; }
  .zone .seg { fill: none; stroke: #cffafe; stroke-width: 5; stroke-linecap: round; }
`;

// The arm page's drawing, restyled. layer "arm" = the arm alone, "glow" = only the chosen region.
async function armImage(source, layer, site) {
  const svg = source.cloneNode(true);
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  svg.setAttribute("width", 240 * TEX_SCALE);
  svg.setAttribute("height", 630 * TEX_SCALE);
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = ARM_STYLE + (layer === "glow"
    ? `.outline, .fill, .vein, .crease { display: none; } .zone[data-site="${site}"] { display: inline; }`
    : "");
  svg.insertBefore(style, svg.firstChild);
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  await img.decode();
  return img;
}

function drawLabel(g, w, h, site) {
  const font = (weight, px) => `${weight} ${px}px ${FONT}`;
  g.fillStyle = "rgba(6, 14, 24, 0.62)";
  g.strokeStyle = "rgba(205, 240, 255, 0.35)";
  g.lineWidth = 3;
  g.beginPath();
  g.roundRect(4, 4, w - 8, h - 8, 48);
  g.fill();
  g.stroke();
  g.textAlign = "center";
  g.textBaseline = "middle";
  const info = site && SITES[site];
  if (info) {
    g.fillStyle = "#67e8f9";
    g.font = font(600, 38);
    g.letterSpacing = "6px";
    g.fillText("RECOMMENDED REGION", w / 2, 72);
    g.letterSpacing = "0px";
    g.fillStyle = "#ffffff";
    g.font = font(600, 72);
    g.fillText(info[0], w / 2, 150);
    g.fillStyle = "#b6c6d6";
    g.font = font(500, 46);
    g.fillText(info[1], w / 2, 228);
  } else {
    g.fillStyle = "#e8edf2";
    g.font = font(500, 52);
    g.fillText("Say “Gemini, recommended region”", w / 2, h / 2);
  }
}

// `source` is the arm page's <svg class="arm"> element (from this page, or fetched from /arm/).
export async function createArm(source) {
  await document.fonts.ready;
  const group = new THREE.Group();
  const armW = ARM_HEIGHT * ARM_ASPECT;
  const toTexture = (img) => canvasTexture((g, w, h) => g.drawImage(img, 0, 0, w, h), 240 * TEX_SCALE, 630 * TEX_SCALE);

  const armMesh = plane(armW, ARM_HEIGHT);
  armMesh.material.map = toTexture(await armImage(source, "arm"));
  const glowMesh = plane(armW, ARM_HEIGHT);
  glowMesh.position.z = 0.002;
  glowMesh.visible = false;
  let site = null;
  const labelH = LABEL_WIDTH * LABEL_PX[1] / LABEL_PX[0];
  const labelMesh = plane(LABEL_WIDTH, labelH);
  labelMesh.material.map = canvasTexture((g, w, h) => drawLabel(g, w, h, site), ...LABEL_PX);
  labelMesh.position.y = -ARM_HEIGHT / 2 - labelH / 2 - 0.012;
  const hit = hitArea(armW + 2 * GRAB_MARGIN, ARM_HEIGHT + labelH + 2 * GRAB_MARGIN);
  hit.position.y = -labelH / 2;
  group.add(armMesh, glowMesh, labelMesh, hit);

  let token = 0, pulseStart = 0;
  async function setSite(next) {
    site = next && SITES[next] ? next : null;
    const mine = ++token;
    labelMesh.material.map.redraw();
    if (!site) {
      glowMesh.visible = false;
      return;
    }
    const img = await armImage(source, "glow", site);
    if (mine !== token) return; // a newer recommendation arrived meanwhile
    glowMesh.material.map?.dispose();
    glowMesh.material.map = toTexture(img);
    glowMesh.material.needsUpdate = true;
    glowMesh.visible = true;
    pulseStart = performance.now();
  }

  function update(now) { // three soft pulses when a region arrives, then a steady glow
    if (!glowMesh.visible) return;
    const t = (now - pulseStart) / 1000;
    glowMesh.material.opacity = reduceMotion || t > 3.9 ? 1 : 0.55 + 0.45 * Math.cos((t * 2 * Math.PI) / 1.3);
  }

  return { group, hit, setSite, update, site: () => (glowMesh.visible ? site : null) };
}
