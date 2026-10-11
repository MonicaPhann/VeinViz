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

// An invisible, slightly larger target that makes things easy to point at.
export function hitArea(width, height) {
  const mesh = plane(width, height);
  mesh.material.colorWrite = false;
  return mesh;
}

// ---------- the stage ----------

export function createStage({ preview = false } = {}) {
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

  // Pinch (or trigger): press a button you point at, otherwise pick up what you point at.
  const raycaster = new THREE.Raycaster();
  const rotation = new THREE.Matrix4();
  function aim(controller) {
    rotation.identity().extractRotation(controller.matrixWorld);
    raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
    raycaster.ray.direction.set(0, 0, -1).applyMatrix4(rotation);
  }
  function selectStart(event) {
    const controller = event.target;
    aim(controller);
    const button = buttons.find((b) => b.hit.visible && raycaster.intersectObject(b.hit).length);
    if (button) {
      button.press();
      return;
    }
    const hits = grabbables
      .map((g) => ({ g, hit: raycaster.intersectObject(g.hit)[0] }))
      .filter((x) => x.hit)
      .sort((a, b) => a.hit.distance - b.hit.distance);
    if (hits.length) {
      controller.attach(hits[0].g.group); // it follows the hand until the pinch ends
      controller.userData.holding = hits[0].g.group;
    }
  }
  function selectEnd(event) {
    const controller = event.target;
    const group = controller.userData.holding;
    if (!group) return;
    scene.attach(group);
    controller.userData.holding = null;
    faceViewer(group);
  }
  for (const i of [0, 1]) {
    const controller = renderer.xr.getController(i);
    controller.addEventListener("selectstart", selectStart);
    controller.addEventListener("selectend", selectEnd);
    scene.add(controller);
  }

  function frame() {
    // Wait a couple of frames for the headset pose, then lay things out in front of you.
    if (!placed && (renderer.xr.isPresenting || preview) && ++frames > 2) {
      layout();
      placed = true;
    }
    const now = performance.now();
    frameFns.forEach((fn) => fn(now));
    renderer.render(scene, camera);
  }

  async function enterAR() {
    const session = await navigator.xr.requestSession("immersive-ar", { optionalFeatures: ["local-floor", "hand-tracking"] });
    placed = false;
    frames = 0;
    await renderer.xr.setSession(session);
    renderer.setAnimationLoop(frame);
    session.addEventListener("end", () => renderer.setAnimationLoop(null));
  }

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
    addButton: (hit, press) => buttons.push({ hit, press }),
    addGrabbable: (group, hit) => grabbables.push({ group, hit }),
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
  const hit = hitArea(armW * 1.8, ARM_HEIGHT + labelH + 0.08);
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
