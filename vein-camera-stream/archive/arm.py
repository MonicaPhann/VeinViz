"""
Arm / hand mask for VeinVision.

Primary: MediaPipe Hand Landmarker (pretrained, CPU, ~15 ms/frame) finds the hand on the IR frame.
From its 21 landmarks we build a generous hand + forearm region, then refine that region with IR
brightness so the final mask follows the real skin edge and drops sleeves, watches, jewelry and
the gaps between fingers.

Fallback (no hand in view, e.g. only forearm/elbow under the camera, or the model can't load):
the classical "bright blob on the dark towel", accepted ONLY if it is shaped like an arm
(elongated, enters from the frame edge, solid, clearly brighter than the background).

All coordinates are in the processing image (640 px wide).
"""
import os
import threading
import time
import urllib.request

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "models", "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")

WRIST, INDEX_MCP, MIDDLE_MCP, PINKY_MCP = 0, 5, 9, 17


def _ellipse(d):
    d = max(int(d), 1) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


def model_input(gray):
    """IR frames are noisy, hazy and unevenly lit; the hand model needs a clean, full-range image.
    Tested: raw low-contrast IR -> 0 hands found; blur + percentile stretch -> found."""
    x = cv2.GaussianBlur(gray, (0, 0), 2)
    lo, hi = np.percentile(x[::4, ::4], (1, 99.5))
    a = 255.0 / max(hi - lo, 1.0)
    x = cv2.convertScaleAbs(x, alpha=a, beta=-lo * a)
    return np.ascontiguousarray(cv2.cvtColor(x, cv2.COLOR_GRAY2RGB))


class HandModel:
    """MediaPipe Hand Landmarker in VIDEO mode (tracks between frames, faster than per-image)."""

    def __init__(self, path=MODEL_PATH):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        if not os.path.exists(path):
            print("[arm] downloading hand model (one time, ~8 MB)")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, path)
        self.mp = mp
        self.lm = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=path),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.35, min_hand_presence_confidence=0.35,
            min_tracking_confidence=0.35))
        self.ts = 0

    def __call__(self, gray):
        h, w = gray.shape
        self.ts = max(self.ts + 1, int(time.monotonic() * 1000))     # must strictly increase
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=model_input(gray))
        res = self.lm.detect_for_video(img, self.ts)
        return [np.array([(p.x * w, p.y * h) for p in hand], np.float32) for hand in res.hand_landmarks]


class AsyncHands:
    """Runs the hand model on its own thread so it overlaps with vein detection (keeps us >= 15 fps).
    Returns the newest landmarks (at most ~1 frame old); the mask itself is always cut from the
    current frame's pixels, so its edges stay current."""

    def __init__(self, model):
        self.model, self.pending, self.result = model, None, []
        self.cv = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def __call__(self, gray):
        with self.cv:
            self.pending = gray
            self.cv.notify()
        return self.result

    def _loop(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.pending is not None)
                g, self.pending = self.pending, None
            try:
                self.result = self.model(g)
            except Exception as e:
                if "shutdown" in str(e):                             # interpreter exiting
                    return
                print("[arm] hand model error:", repr(e))
                self.result = []


def palm_size(lm):
    return max(np.linalg.norm(lm[INDEX_MCP] - lm[PINKY_MCP]), np.linalg.norm(lm[MIDDLE_MCP] - lm[WRIST]), 8.0)


class ArmDetector:
    ON_FRAMES = 2       # consecutive detections before the arm counts as present
    OFF_FRAMES = 8      # consecutive misses before it counts as gone (~0.5 s): no flicker on a dropped frame

    def __init__(self, use_model=True, fallback=False, threaded=True):
        self.model = None
        if use_model:
            try:
                self.model = AsyncHands(HandModel()) if threaded else HandModel()
            except Exception as e:                                   # never block the demo on the model
                print("[arm] hand model unavailable, using shape fallback only:", e)
        self.fallback = fallback or self.model is None
        self.lms = None          # smoothed landmarks of the tracked (patient's) hand
        self.soft = None         # temporally smoothed mask, float 0..1
        self.hits = self.miss = 0
        self.present = False
        self.source = None       # "hand" (model) or "shape" (fallback)

    # ------------------------------------------------------------------ public
    def __call__(self, gray, moving=False):
        """gray: processing-res IR frame. Returns (mask uint8 0/255, info dict)."""
        raw, src = None, None
        hands = self.model(gray) if self.model else []
        if hands:
            raw = self._from_hand(gray, self._track(hands))
            src = "hand" if raw is not None else None
        else:
            self.lms = None
        if raw is None and self.fallback:
            raw = self._from_shape(gray)
            src = "shape" if raw is not None else None

        if raw is not None:
            self.hits, self.miss = self.hits + 1, 0
            new = raw.astype(np.float32) / 255
            a = 0.3 if moving else 0.6                               # follow faster while the arm moves
            ok = self.soft is not None and self.soft.shape == new.shape
            self.soft = a * self.soft + (1 - a) * new if ok else new
            self.source = src
        else:
            self.hits, self.miss = 0, self.miss + 1
        if not self.present and self.hits >= self.ON_FRAMES:
            self.present = True
        if self.present and self.miss >= self.OFF_FRAMES:
            self.present, self.soft = False, None

        if not self.present or self.soft is None:
            return np.zeros_like(gray), dict(arm=False, source=None, landmarks=None)
        mask = (self.soft > 0.5).astype(np.uint8) * 255
        return mask, dict(arm=True, source=self.source, landmarks=self.lms if self.source == "hand" else None)

    # ------------------------------------------------------------------ model path
    def _track(self, hands):
        """Follow one hand (the patient's): the one nearest the previously tracked hand, else the
        largest. A second hand (e.g. the clinician's) is ignored."""
        if self.lms is not None:
            d = [np.linalg.norm(h[WRIST] - self.lms[WRIST]) for h in hands]
            i = int(np.argmin(d))
            if d[i] < 0.6 * palm_size(self.lms):
                self.lms = 0.5 * self.lms + 0.5 * hands[i]          # light landmark smoothing
                return self.lms
        self.lms = max(hands, key=palm_size)
        return self.lms

    @staticmethod
    def _region(lm, shape):
        """Generous hand + forearm area from landmarks (refined by brightness afterwards)."""
        s = palm_size(lm)
        reg = np.zeros(shape, np.uint8)
        cv2.fillConvexPoly(reg, cv2.convexHull(lm.astype(np.int32)), 255)
        reg = cv2.dilate(reg, _ellipse(0.45 * s))                    # landmarks sit on joint centers
        d = lm[WRIST] - lm[MIDDLE_MCP]                               # points from hand toward elbow
        d /= np.linalg.norm(d) + 1e-6
        n = np.array([-d[1], d[0]])
        a, b = lm[WRIST] - 0.25 * s * d, lm[WRIST] + 3.2 * s * d     # forearm ~3 palm lengths
        quad = np.array([a + 0.7 * s * n, b + 1.05 * s * n, b - 1.05 * s * n, a - 0.7 * s * n])
        cv2.fillConvexPoly(reg, quad.astype(np.int32), 255)
        core = np.zeros(shape, np.uint8)                             # palm / back of hand: surely skin
        tri = lm[[WRIST, INDEX_MCP, PINKY_MCP]]
        cv2.fillConvexPoly(core, (tri.mean(0) + 0.7 * (tri - tri.mean(0))).astype(np.int32), 255)
        return reg, core

    def _from_hand(self, gray, lm):
        reg, core = self._region(lm, gray.shape)
        b = cv2.GaussianBlur(gray, (0, 0), 2)
        inside = reg > 0
        if not core.any() or inside.sum() < 200:
            return None
        ref = float(np.median(b[core > 0]))                          # this patient's skin brightness
        if ref < 20:
            return None
        otsu = cv2.threshold(b[inside].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
        lo = float(np.clip(otsu, 0.35 * ref, 0.68 * ref))            # veins are only 10-25% darker: kept
        # too dark = background / finger gaps / dark straps; too bright = white sleeve, metal, glare
        skin = ((b > lo) & (b < 1.6 * ref) & inside).astype(np.uint8) * 255
        skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, _ellipse(5))
        n, lab = cv2.connectedComponents(skin)
        ids = np.unique(lab[(core > 0) & (skin > 0)])
        ids = ids[ids > 0]
        if not len(ids):
            return None
        m = np.isin(lab, ids).astype(np.uint8) * 255                 # only skin connected to the hand
        m = self._cut_sleeve(b, m, lm)
        return cv2.morphologyEx(m, cv2.MORPH_CLOSE, _ellipse(5))

    @staticmethod
    def _cut_sleeve(b, m, lm):
        """Walk from the wrist toward the elbow in slices; stop where the brightness jumps (sleeve or
        cuff edge) or the arm runs out. Gradual IR falloff passes; a fabric edge doesn't."""
        s = palm_size(lm)
        d = lm[WRIST] - lm[MIDDLE_MCP]
        d = d / (np.linalg.norm(d) + 1e-6)
        ys, xs = np.nonzero(m)
        t = (xs - lm[WRIST][0]) * d[0] + (ys - lm[WRIST][1]) * d[1]  # distance past the wrist
        step, start = 0.2 * s, 0.2 * s
        fore = t > start
        if fore.sum() < 50:
            return m
        idx = ((t[fore] - start) // step).astype(int)
        vals = b[ys[fore], xs[fore]]
        full = 0.25 * step * s                                       # pixel count of a slice ~1/4 arm wide
        hist, cut = [], None
        for i in range(idx.max() + 1):
            v = vals[idx == i]
            if v.size < full:
                cut = i
                break
            med = float(np.median(v))
            if hist:
                local = float(np.median(hist[-3:]))
                if abs(med - local) > 0.15 * local:
                    cut = i
                    break
            hist.append(med)
        if cut is not None:                                          # also skip the cuff's shadow band
            drop = t[fore] >= start + (cut - 0.5) * step
            m[ys[fore][drop], xs[fore][drop]] = 0
        return m

    # ------------------------------------------------------------------ fallback path
    @staticmethod
    def _from_shape(gray):
        H, W = gray.shape
        b = cv2.GaussianBlur(gray, (0, 0), 5)
        thr, m = cv2.threshold(b, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        n, lab, st, _ = cv2.connectedComponentsWithStats(m)
        if n < 2:
            return None
        big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        x, y, w, h, area = st[big]
        if not 0.05 < area / gray.size < 0.75:                       # nothing there / background not dark
            return None
        if not (x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2):
            return None                                              # an arm enters from the frame edge
        blob = (lab == big).astype(np.uint8) * 255
        cnt = max(cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        (_, _), (rw, rh), _ = cv2.minAreaRect(cnt)
        hull = cv2.contourArea(cv2.convexHull(cnt))
        if max(rw, rh) / (min(rw, rh) + 1e-3) < 1.6 or cv2.contourArea(cnt) / (hull + 1e-3) < 0.8:
            return None                                              # not arm-shaped
        ref = float(np.median(b[blob > 0]))
        if ref < 1.8 * (float(np.median(b[blob == 0])) + 1):
            return None                                              # background not clearly darker
        out = np.zeros_like(gray)
        cv2.drawContours(out, [cnt], -1, 255, cv2.FILLED)
        out[(b < max(thr, 0.45 * ref)) | (b > 1.6 * ref)] = 0
        return cv2.morphologyEx(out, cv2.MORPH_OPEN, _ellipse(5))
