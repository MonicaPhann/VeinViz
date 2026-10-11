# VeinVision: plan, architecture, UI, test plan

## 1. Arm-detection model choice

**Chosen: MediaPipe Hand Landmarker (Tasks API, `hand_landmarker.task`, float16, 8 MB), plus IR brightness refinement.**

| Candidate | Verdict |
|---|---|
| **MediaPipe Hands** | ✅ Pretrained, CPU, ~16 ms/frame in video mode on this laptop. Shape-based, so it works on greyscale: 2/2 hands found on a grey photo, also rotated 90° (top-down). Gives the wrist and hand direction, so the forearm can be extended from it. |
| MediaPipe Pose | ❌ Needs the face and torso in view; a top-down shot of an arm on a table has neither. |
| MediaPipe selfie / image segmentation | ❌ Trained on upright people facing a selfie camera; unreliable on a lone arm seen from above, and no hand/arm distinction. |
| YOLO-seg "person" | ❌ Needs torch/ultralytics (heavy install, ~50–100 ms CPU at useful sizes), and "person" on a lone forearm is unreliable. It would also segment the clinician's hand. |

**Measured IR caveat:** on noisy, low-contrast grey frames the model found **0 hands**. Fix (in `arm.model_input`): Gaussian
blur σ=2 plus a 1–99.5 % contrast stretch before the model. This recovered 2/2 hands under low contrast, noise and vignetting.

**How the mask is built (arm.py):**
1. Run the hand model on a contrast-normalised copy of the frame, in its own thread so it overlaps vein detection.
2. **Track one hand**: the one nearest the previous hand, else the largest. A second hand (the clinician's) is ignored.
3. **Region** = hull of the 21 landmarks (dilated), plus a forearm quad extended about 3 palm-lengths from the wrist, away from the fingers.
4. **Refine with IR brightness**: the reference is the median brightness of the back of the hand. Keep pixels between max(Otsu, 0.35·ref)
   and 1.6·ref, connected to the hand. This removes the towel, **finger gaps**, dark watch straps, white/metal objects and glare.
5. **Sleeve cut**: walk from the wrist toward the elbow in slices. Stop where the median brightness jumps more than 15 % (a fabric
   edge) or the arm runs out, and drop half a slice more to skip the cuff shadow. Gradual IR falloff passes.
6. **Temporal smoothing**: EMA of the mask (weaker while moving), landmark EMA, presence hysteresis (2 hits on, 8 misses off,
   about 0.5 s). The mask doesn't flicker or jump when one frame misses.
7. **No arm leads to no veins**: the vein search runs only inside the mask (eroded 9 px; the image border counts as an edge).

**Fallback for IR failure (`--allow-no-hand`)**: classical "bright blob on dark towel", accepted only if it is arm-shaped
(elongated ≥ 1.6, solid ≥ 0.8, touches the frame edge, ≥ 1.8× brighter than the background). **Off by default.** In testing it
accepted an arm-shaped bright bar, so it can't guarantee "veins only on the arm". Use it only if the model fails on your IR feed
or you must frame the elbow without the hand. If mediapipe can't load at all, the server falls back automatically and prints a warning.

## 2. Architecture (colorization removed)

```
Source thread (RTSP / webcam / clip / photo; newest frame only; auto-reconnect)
   │
Processor thread ── Pipeline.run(frame) ─────────────────────────────────────────────┐
   │  1. grey → output size (1280) and processing size (640)                           │
   │  2. Motion: frame difference inside last arm mask → "moving" (hysteresis)         │
   │  3. ArmDetector  ← AsyncHands thread (MediaPipe)                                  │
   │  4. VeinDetector (only inside the arm mask):                                      │
   │       flatten lighting using skin pixels only, outside arm = neutral              │
   │       → CLAHE → black-hat → EMA (weak while moving)                               │
   │       → hysteresis threshold (top X % seeds + noise floor 2.2× median)            │
   │       → drop dots and strips that hug the arm outline                             │
   │  5. vein_segments (centerlines, widths) → SiteTracker (best site, stable)         │
   │  6. render: linear contrast stretch of the REAL IR image (no tint), dim off-arm   │
   │       to 40 %, veins = bright color + dark outline, optional site marker          │
   └─ JPEG ──► /stream (MJPEG), /snapshot.jpg      info ──► /status, /veins.json, /veins/stream (SSE)
```
Removed: `SKIN_TONES`, `colorize()`, skin-tone keys 1–5, `view=color/ir`, `--skin`.
Files: `arm.py`, `veins.py`, `pipeline.py` (shared by server and tests), `server.py`, `index.html`, `tools/offline_test.py`.

## 3. Clinician UI

**Layout:** the full-height IR image (letterboxed, never covered by controls); a thin top strip with **VeinVision · status pill
(LIVE / FROZEN / NO CAMERA / OFFLINE)** and **Full screen**; a disclaimer line at the bottom of the image; one control bar below the image.

**Controls (72 px tall, plain words, glove- and clicker-friendly):**
`− Fewer veins  ●●●○○  More veins +` | `Hide veins` `Suggest site` | `❚❚ Freeze / ▶ Resume live` `Save photo`

**Status messages (priority order):**
| State | What the clinician sees |
|---|---|
| Server not reachable | Card: "Connecting to VeinVision…", image hidden |
| Camera lost | Card: "Camera disconnected – reconnecting…", image greyed out (a stale image is never shown as live) |
| Frozen | Amber frame around the image, pill "FROZEN", "Frozen – press Resume live to continue" |
| No arm | Card: "Place arm under camera / Lay the arm flat on the dark towel, with the hand in view." No highlights at all |
| Arm moving > 0.4 s | Banner: "Hold still" |
| Arm still, no veins for 2.5 s | Banner: "No veins found – try More veins" |

**Vein color:** bright cyan core plus a dark outline on a colorless IR image. Veins differ from skin in **brightness**, not only
hue, so they stay readable with red-green color blindness. Yellow and green are available via the **C** key. The suggested site is
white with a black outline, distinct from the veins.

## 4. Implementation steps (priority order) and how each was / should be tested

| # | Step | Status | Test on recorded data first |
|---|---|---|---|
| 1 | Remove colorization; faithful IR render | ✅ | `offline_test.py --source clip.mp4 --out d.mp4`: the output looks like the raw feed, only contrast-stretched |
| 2 | Hand model + IR preprocessing | ✅ | `offline_test.py` → "arm found by: hand N %". **Target ≥ 90 % on clips where the hand is visible.** Tested on a degraded-IR photo: 97 % |
| 3 | Mask refinement: finger gaps, sleeve, watch | ✅ | Press **A** live, or watch the green outline in `debug.mp4`. Record clips with a sleeve, a watch and spread fingers |
| 4 | Veins only inside the mask + no-arm state | ✅ | "vein pixels outside the arm: 0". Clip of an empty towel → arm none, 0 vein px (tested ✅) |
| 5 | Temporal smoothing (mask, veins, motion) | ✅ | "overlap while still" ≥ 0.80 (tested: 0.98) |
| 6 | Speed ≥ 15 fps | ✅ | Tested 41 fps offline (arm 9 ms, veins 8 ms, render 5 ms at 1280 px out). Live: press **D** |
| 7 | Clinician UI + status messages | ✅ | Tested in browser: live, no arm, camera down, server down, frozen, save |
| 8 | Robustness | ✅ | Pull the camera's power mid-demo → "Camera disconnected", then it recovers by itself. Bad settings POST is ignored |
| 9 | Best site + `/veins.json` + SSE | ✅ (nice-to-have) | `curl localhost:8000/veins.json`; `curl -N localhost:8000/veins/stream` |
| 10 | **Tune on real IR clips (tonight)** | ⏳ | See below |

**Tonight's tuning loop (about 30 min):**
1. Record 4–6 clips of 15 s each on the rig (VLC → Media → Convert/Save the RTSP stream, or a screen recorder): different people,
   back of hand + forearm, one with a sleeve, one with a watch, one empty towel, one with the hand moving in and out.
2. `python tools/offline_test.py --source clipN.mp4 --out dN.mp4` for each. Check PASS lines and watch `dN.mp4`.
3. If the hand is often not found: make sure the hand is in frame and the towel is dark. As a last resort, use `--allow-no-hand`.
4. If veins are speckled or missing: try `--sens-level` 2–4. To make it the default, change `DEFAULTS` in `pipeline.py`.

## 5. Acceptance criteria

| Criterion | How it's measured | Result so far |
|---|---|---|
| Veins only on the arm | `offline_test`: vein pixels outside the arm = 0, on every clip | ✅ 0 (enforced by construction, verified) |
| No highlights without an arm | Empty-towel, light-grey-towel and bright-object scenes → arm none, 0 vein px | ✅ all 0 (default mode) |
| No false veins on smooth skin | Synthetic skin without veins, sensitivity level 3 → 0 px | ✅ 0 px; real veins found 99 % |
| No flicker | Frame-to-frame vein overlap while still ≥ 0.80; mask hysteresis | ✅ 0.98 |
| ≥ 15 fps | `offline_test` speed line; live **D** key | ✅ 41 fps offline (live is capped by the camera's ~15–25 fps) |
| Nurse can use it without instructions | Hand the laptop to someone untrained: they place the arm, read "Hold still" / veins, freeze and save in under 30 s without help | ⏳ do this tonight |
| Not presented as diagnostic | Disclaimer always on screen | ✅ |

**Known limits (say these to judges):** a sleeve of exactly skin-like IR brightness isn't cut; the arm must include the hand
unless `--allow-no-hand` is used; vein visibility depends on skin and fat thickness (as with all NIR finders); and the image has
the Tapo's 0.5–1 s RTSP latency.
