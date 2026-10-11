// Passthrough AR for the arm page (Meta Quest browser). The arm floats in the room as a glass
// hologram; point at it and pinch to move it. It lights up whenever Vein Assist recommends a region.
// The 3D parts live in ar-kit.js, which loads only on devices that can do AR.

const state = window.veinArm;
const button = document.getElementById("ar-btn");
const hint = document.getElementById("ar-hint");
const preview = new URLSearchParams(location.search).has("ar-preview"); // desktop check, no headset

let ready = null;
function setUp() { // load the 3D parts and build the scene once
  ready ??= (async () => {
    const kit = await import("./ar-kit.js");
    const stage = kit.createStage({ preview });
    const arm = await kit.createArm(document.querySelector("svg.arm"));
    stage.scene.add(arm.group);
    stage.addGrabbable(arm.group, arm.hit);
    stage.onLayout(() => {
      stage.placeInFront(arm.group, { distance: 0.65, down: 0.12 });
      if (preview) stage.camera.lookAt(arm.group.position.x, arm.group.position.y - 0.05, arm.group.position.z);
    });
    stage.onFrame((now) => arm.update(now));
    state.onChange(arm.setSite);
    await arm.setSite(state.current());
    // Read-only status, handy for checking AR from the browser console.
    state.ar = {
      presenting: stage.presenting,
      armPosition: () => arm.group.position.toArray().map((v) => +v.toFixed(3)),
      region: arm.site,
    };
    return stage;
  })();
  return ready;
}

async function enterAR() {
  button.disabled = true;
  try {
    const stage = await setUp();
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
  navigator.xr.isSessionSupported("immersive-ar").then((ok) => {
    if (!ok) return;
    button.hidden = false;
    button.addEventListener("click", enterAR);
    setUp(); // get ready in the background so the button starts AR straight away
  }).catch(() => {});
} else if (!window.isSecureContext && /OculusBrowser|Quest/i.test(navigator.userAgent)) {
  // On the Quest over plain http, the browser blocks AR: point to the https link.
  hint.textContent = `For AR, open https://${location.hostname}:8443/arm/`;
  hint.hidden = false;
}
