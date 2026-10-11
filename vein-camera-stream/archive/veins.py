"""
Vein detection (classical CV, no training) + vein geometry for the best-site marker and JSON output.
Runs only inside the arm mask from arm.py. All coordinates are in the processing image (640 px wide).
"""
import cv2
import numpy as np


def _ellipse(d):
    d = max(int(d), 1) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


class VeinDetector:
    EDGE = 9            # px ignored along the arm outline

    def __init__(self):
        self.acc = None
        self._clahe = {}

    def reset(self):
        self.acc = None

    def __call__(self, gray, arm, p, moving=False):
        """Returns the vein mask (uint8 0/255) for this frame; empty if there is no arm."""
        if not arm.any():
            self.acc = None
            return np.zeros_like(gray)
        H, W = gray.shape
        g = gray.astype(np.float32)
        m = (arm > 0).astype(np.float32)

        # 1. flatten the uneven IR lighting using skin pixels only (normalized blur at 1/4 res),
        #    and set everything outside the arm to neutral so the arm outline isn't a "dark line"
        q = (W // 4, H // 4)
        sig = W / 48
        num = cv2.GaussianBlur(cv2.resize(g * m, q, interpolation=cv2.INTER_AREA), (0, 0), sig)
        den = cv2.GaussianBlur(cv2.resize(m, q, interpolation=cv2.INTER_AREA), (0, 0), sig)
        illum = cv2.resize(num / (den + 1e-3), (W, H), interpolation=cv2.INTER_LINEAR)
        flat = np.clip(g / (illum + 1) * 128, 0, 255).astype(np.uint8)
        flat[arm == 0] = 128

        # 2. local contrast boost, then black-hat = thin dark structures narrower than the kernel
        c = round(float(p["contrast"]), 1)
        if c not in self._clahe:
            self._clahe[c] = cv2.createCLAHE(clipLimit=c, tileGridSize=(8, 8))
        enh = cv2.GaussianBlur(self._clahe[c].apply(flat), (5, 5), 0)
        k = max(int(p["width"]), 5) | 1
        pad = cv2.copyMakeBorder(enh, k, k, k, k, cv2.BORDER_REPLICATE)             # no fake valley at the frame edge
        resp = cv2.morphologyEx(pad, cv2.MORPH_BLACKHAT, _ellipse(k))[k:-k, k:-k].astype(np.float32)
        # (the image border counts as an edge too: a forearm usually enters from outside the frame)
        inner = cv2.erode(arm, _ellipse(self.EDGE), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        resp[inner == 0] = 0

        # 3. temporal smoothing: strong when still (no flicker), weak while moving (no ghost trails)
        s = float(p["smooth"]) if not moving else min(float(p["smooth"]), 0.25)
        ok = self.acc is not None and self.acc.shape == resp.shape
        self.acc = s * self.acc + (1 - s) * resp if ok else resp
        acc = self.acc

        # 4. hysteresis threshold: strongest X% inside the arm seed veins, weaker connected pixels
        #    extend them -> continuous veins instead of dashed fragments
        vals = acc[inner > 0]
        if vals.size < 50:
            return np.zeros_like(gray)
        # floor: on skin with no visible veins the top X% is just noise (measured: noise-only skin has
        # p85 ~1.3x median, real veins >= 3.6x), so require a clear margin over the typical response
        hi = max(float(np.percentile(vals, 100 - p["sens"])), 2.2 * float(np.median(vals)), 6.0)
        weak = (acc > 0.65 * hi).astype(np.uint8)
        n, lab = cv2.connectedComponents(weak)
        keep = np.zeros(n, bool)
        keep[np.unique(lab[acc > hi])] = True
        keep[0] = False
        mask = keep[lab].astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _ellipse(5))

        # 5. keep elongated shapes only (veins are lines; pores, hair shadows, moles are dots), and
        #    drop strips that just hug the arm outline (skin curving away = shading, not a vein)
        mask &= inner
        n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
        near_edge = cv2.distanceTransform(arm, cv2.DIST_L2, 3) < self.EDGE + 5
        near = np.bincount(lab[near_edge], minlength=n)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if a < p["min_size"] or near[i] > 0.75 * a:
                continue
            mo = cv2.moments((lab[y:y + h, x:x + w] == i).astype(np.uint8), True)
            l1, l2 = np.linalg.eigvalsh([[mo["mu20"], mo["mu11"]], [mo["mu11"], mo["mu02"]]])[::-1]
            if np.sqrt(l1 / max(l2, 1e-3)) >= 2.2 or a > p["min_size"] * 8:
                keep[i] = True
        return keep[lab].astype(np.uint8) * 255


# ---------------------------------------------------------------------- geometry
def vein_segments(mask, min_len=20):
    """One entry per vein blob: centerline polyline (centroids of slices along the main axis),
    mean width, length, straightness, unit direction."""
    out = []
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 15:
            continue
        x, y, w, h = st[i, :4]
        ys, xs = np.nonzero(lab[y:y + h, x:x + w] == i)
        pts = np.stack([xs + x, ys + y], 1).astype(np.float32)
        c = pts.mean(0)
        evals, evecs = np.linalg.eigh(np.cov((pts - c).T))
        v = evecs[:, 1]                                              # main axis
        t = (pts - c) @ v
        length = float(t.max() - t.min())
        if length < min_len:
            continue
        k = int(np.clip(length / 10, 3, 16))
        bins = np.minimum(((t - t.min()) / (length + 1e-6) * k).astype(int), k - 1)
        line, widths = [], []
        for j in range(k):
            sel = bins == j
            if sel.any():
                line.append(pts[sel].mean(0))
                widths.append(sel.sum() / (length / k))
        line = np.array(line)
        arc = float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum()) if len(line) > 1 else 0.0
        chord = float(np.linalg.norm(line[-1] - line[0])) if len(line) > 1 else 0.0
        out.append(dict(points=line, width=float(np.median(widths)), length=length,
                        straightness=chord / (arc + 1e-6), direction=v.astype(np.float32)))
    return out


class SiteTracker:
    """'Best insertion site' = the thickest, straightest, long-enough vein; kept stable over time
    (only switches to another vein if that one wins for several frames in a row)."""

    def __init__(self):
        self.site, self.challenger = None, 0

    def reset(self):
        self.site, self.challenger = None, 0

    def __call__(self, segs, toward_elbow=None):
        cands = [s for s in segs if s["length"] >= 35]
        if not cands:
            self.challenger += 1
            if self.challenger > 10:
                self.site = None
            return self.site
        best = max(cands, key=lambda s: s["width"] * s["straightness"] ** 2 * min(1.0, s["length"] / 90))
        pts = best["points"]
        mid = pts[len(pts) // 2]
        d = best["direction"].copy()
        if toward_elbow is not None and float(d @ toward_elbow) < 0:
            d = -d                                                   # needle points toward the heart
        new = dict(x=float(mid[0]), y=float(mid[1]), dx=float(d[0]), dy=float(d[1]),
                   width=best["width"], arrow=toward_elbow is not None)
        if self.site is None:
            self.site, self.challenger = new, 0
        elif np.hypot(new["x"] - self.site["x"], new["y"] - self.site["y"]) < 25:
            if new["dx"] * self.site["dx"] + new["dy"] * self.site["dy"] < 0:
                new["dx"], new["dy"] = -new["dx"], -new["dy"]        # axis sign is arbitrary
            for k in ("x", "y", "dx", "dy", "width"):                # same vein: glide, don't jump
                self.site[k] = 0.8 * self.site[k] + 0.2 * new[k]
            norm = np.hypot(self.site["dx"], self.site["dy"]) + 1e-6
            self.site["dx"] /= norm
            self.site["dy"] /= norm
            self.site["arrow"] = new["arrow"]
            self.challenger = 0
        else:
            self.challenger += 1
            if self.challenger >= 8:
                self.site, self.challenger = new, 0
        return self.site
