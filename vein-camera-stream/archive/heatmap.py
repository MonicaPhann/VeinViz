"""
Vein detection-confidence heatmap (test view).

Multi-scale Hessian "vesselness" (Frangi-style) on the lighting-flattened IR image: it responds to
dark, tube-shaped structures a few pixels wide (veins under IR), and much less to noise, blobs or edges.
The response is turned into a 0..1 DETECTION confidence (how strongly the image looks like a vessel),
which is NOT a clinical confidence or a judgement that a vein is suitable for IV placement.

Color: weak/ambiguous = transparent, moderate = yellow, strong = dark red.
"""
import cv2
import numpy as np

PROC_W = 640


def flatten(g):
    """Divide out the uneven IR lighting (heavy blur computed at 1/4 size)."""
    H, W = g.shape
    small = cv2.resize(g, (max(W // 4, 1), max(H // 4, 1)), interpolation=cv2.INTER_AREA)
    light = cv2.resize(cv2.GaussianBlur(small, (0, 0), W / 48), (W, H), interpolation=cv2.INTER_LINEAR)
    return (g + 10) / (light + 10) * 128


def vesselness(f, scales=(1.5, 2.5, 4.0), beta=0.5):
    """Dark-vessel response in grey levels (scale-normalized Hessian), max over scales."""
    best = np.zeros_like(f)
    for s in scales:
        L = cv2.GaussianBlur(f, (0, 0), s)
        xx = cv2.Sobel(L, cv2.CV_32F, 2, 0, ksize=3)
        yy = cv2.Sobel(L, cv2.CV_32F, 0, 2, ksize=3)
        xy = cv2.Sobel(L, cv2.CV_32F, 1, 1, ksize=3)
        tmp = np.sqrt((xx - yy) ** 2 + 4 * xy ** 2)
        e1, e2 = (xx + yy + tmp) / 2, (xx + yy - tmp) / 2
        big = np.where(np.abs(e1) >= np.abs(e2), e1, e2)            # curvature across the vessel
        small = np.where(np.abs(e1) >= np.abs(e2), e2, e1)          # curvature along it (~0 for a line)
        ratio = small / (np.abs(big) + 1e-6)
        r = s * s * big * np.exp(-ratio ** 2 / (2 * beta ** 2))    # line-like, not blob-like
        r[big <= 0] = 0                                             # dark valleys only
        np.maximum(best, r, out=best)
    return best


class Heatmap:
    def __init__(self, low=2.5, high=20.0, smooth=0.5):
        self.low, self.high, self.smooth = low, high, smooth       # response (grey levels) -> confidence 0 / 1
        self.acc = None
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        # colormap: transparent -> yellow -> orange -> dark red, opacity rising with confidence (BGR)
        stops = [(0.00, (0, 220, 255), 0.0), (0.15, (0, 220, 255), 0.0), (0.40, (0, 220, 255), 0.50),
                 (0.65, (0, 110, 255), 0.65), (1.00, (30, 30, 200), 0.80)]
        x = np.linspace(0, 1, 256)
        pos = [p for p, _, _ in stops]
        self.lut = np.stack([np.interp(x, pos, [c[i] for _, c, _ in stops]) for i in range(3)], 1)
        self.lut = self.lut.astype(np.uint8).reshape(256, 1, 3)
        self.alpha = np.interp(x, pos, [a for _, _, a in stops]).astype(np.float32)

    def skin_gate(self, g):
        """Only where the arm is: brighter than the dark towel, away from the arm outline."""
        b = cv2.GaussianBlur(g, (0, 0), 3).astype(np.uint8)
        _, m = cv2.threshold(b, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)),
                      borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return cv2.GaussianBlur(m, (0, 0), 3).astype(np.float32) / 255

    def confidence(self, gray):
        """gray: uint8 IR frame (any size). Returns confidence 0..1 at 640 px wide."""
        h, w = gray.shape
        g = cv2.resize(gray, (PROC_W, round(h * PROC_W / w)), interpolation=cv2.INTER_AREA).astype(np.float32)
        f = cv2.GaussianBlur(flatten(g), (0, 0), 1.0)
        r = vesselness(f) * self.skin_gate(g)
        ok = self.acc is not None and self.acc.shape == r.shape
        self.acc = self.smooth * self.acc + (1 - self.smooth) * r if ok else r   # steady, no flicker
        c = np.clip((self.acc - self.low) / (self.high - self.low), 0, 1)
        c = c * c * (3 - 2 * c)                                     # smoothstep: soft edges like the reference
        # thin lines: full strength on the vessel's center line, fading toward its sides (soft glow)
        peak = cv2.dilate(self.acc, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        return c * np.clip(self.acc / (peak + 1e-3), 0, 1) ** 3

    def render(self, gray_out, conf):
        """Overlay the heatmap on the (flattened + CLAHE) IR image, plus a legend."""
        H, W = gray_out.shape
        base = self.clahe.apply(np.clip(flatten(gray_out.astype(np.float32)), 0, 255).astype(np.uint8))
        base = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
        c8 = (cv2.resize(conf, (W, H), interpolation=cv2.INTER_LINEAR) * 255).astype(np.uint8)
        color = cv2.LUT(cv2.cvtColor(c8, cv2.COLOR_GRAY2BGR), self.lut)
        a = self.alpha[c8]
        out = cv2.blendLinear(base, color, 1 - a, a)
        self.legend(out)
        return out

    def legend(self, out):
        H, W = out.shape[:2]
        sc = W / 1280
        bw, bh = int(220 * sc), int(14 * sc)
        x0, y0 = W - bw - int(24 * sc), H - int(56 * sc)
        cv2.rectangle(out, (x0 - int(12 * sc), y0 - int(34 * sc)), (W - int(12 * sc), H - int(10 * sc)), (0, 0, 0), -1)
        ramp = np.linspace(0, 255, bw).astype(np.uint8)
        a = self.alpha[ramp][:, None]
        bar = (self.lut[ramp, 0].astype(np.float32) * a + 60 * (1 - a)).astype(np.uint8)
        out[y0:y0 + bh, x0:x0 + bw] = np.repeat(bar[None], bh, 0)
        font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.5 * sc, max(1, round(sc))
        cv2.putText(out, "Detection confidence", (x0, y0 - int(10 * sc)), font, fs, (255, 255, 255), th, cv2.LINE_AA)
        cv2.putText(out, "low", (x0, y0 + bh + int(16 * sc)), font, 0.42 * sc, (200, 200, 200), th, cv2.LINE_AA)
        cv2.putText(out, "high", (x0 + bw - int(34 * sc), y0 + bh + int(16 * sc)), font, 0.42 * sc, (200, 200, 200), th, cv2.LINE_AA)
        cv2.putText(out, "Image signal only - not a clinical assessment", (int(14 * sc), H - int(16 * sc)),
                    font, 0.45 * sc, (220, 220, 220), th, cv2.LINE_AA)
