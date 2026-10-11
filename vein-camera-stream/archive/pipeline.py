"""
Frame pipeline shared by the live server and the offline test tool:
IR frame -> motion check -> arm mask (hand model) -> veins (inside the arm only) -> rendered frame + vein data.
No colorization: the output is the real IR image (linear contrast stretch only) with veins highlighted.
"""
import time

import cv2
import numpy as np

from arm import ArmDetector, MIDDLE_MCP, WRIST
from veins import SiteTracker, VeinDetector, vein_segments

PROC_W = 640
SENS_LEVELS = [7, 10, 15, 20, 26]          # % of the arm marked as vein, "Fewer veins" .. "More veins"
# BGR. Bright, saturated hues on a colorless IR image + a dark outline, so veins differ from the
# background in brightness (not only hue) and stay readable with red-green color blindness.
VEIN_COLORS = {"cyan": (255, 229, 0), "yellow": (0, 214, 255), "green": (80, 255, 60)}
DEFAULTS = dict(
    sens_level=3,       # 1..5, index into SENS_LEVELS
    show_veins=True,
    show_site=False,    # "Suggest site" marker
    show_arm=False,     # operator/debug: outline of the detected arm + hand landmarks
    vein_color="cyan",
    width=21,           # max vein width in px at 640 wide (black-hat kernel)
    contrast=3.0,       # local contrast boost used for detection (not for display)
    min_size=60,        # smallest vein blob kept
    smooth=0.6,         # temporal smoothing 0..0.95 (higher = steadier, more lag)
)


def _ellipse(d):
    d = max(int(d), 1) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


class Motion:
    """Is the arm moving? Mean frame difference inside the arm on a tiny image, with hysteresis."""

    def __init__(self):
        self.prev, self.moving, self.on, self.off, self.level = None, False, 0, 0, 0.0

    def __call__(self, gray, arm):
        h, w = gray.shape
        t = cv2.resize(gray, (160, max(1, 160 * h // w)), interpolation=cv2.INTER_AREA).astype(np.float32)
        prev, self.prev = self.prev, t
        if prev is None or prev.shape != t.shape:
            return self.moving
        sel = cv2.resize(arm, (t.shape[1], t.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        diff = np.abs(t - prev)
        self.level = float(diff[sel].mean()) if sel.sum() > 50 else float(diff.mean())
        if self.level > 3.0:
            self.on, self.off = self.on + 1, 0
        elif self.level < 1.8:
            self.on, self.off = 0, self.off + 1
        if self.on >= 2:
            self.moving = True
        elif self.off >= 3:
            self.moving = False
        return self.moving


class Pipeline:
    def __init__(self, out_w=1280, use_model=True, fallback=False):
        self.out_w = out_w
        self.arm = ArmDetector(use_model=use_model, fallback=fallback)
        self.veins = VeinDetector()
        self.site = SiteTracker()
        self.motion = Motion()
        self.last_arm = None
        self.lo = self.hi = None

    def run(self, frame, p):
        """frame: BGR (or gray) camera frame. p: settings dict (see DEFAULTS).
        Returns (rendered BGR frame, info dict)."""
        t0 = time.perf_counter()
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h0, w0 = gray.shape
        ow = min(self.out_w, w0)
        g_out = cv2.resize(gray, (ow, round(h0 * ow / w0)), interpolation=cv2.INTER_AREA) if ow != w0 else gray
        g = cv2.resize(g_out, (PROC_W, round(h0 * PROC_W / w0)), interpolation=cv2.INTER_AREA)

        moving = self.motion(g, self.last_arm if self.last_arm is not None else np.zeros_like(g))
        arm, ainfo = self.arm(g, moving)
        self.last_arm = arm
        t1 = time.perf_counter()

        pp = dict(p, sens=SENS_LEVELS[int(np.clip(p["sens_level"], 1, len(SENS_LEVELS))) - 1])
        veins = self.veins(g, arm, pp, moving)
        segs = vein_segments(veins) if ainfo["arm"] else []
        toward_elbow = None
        if ainfo["landmarks"] is not None:
            lm = ainfo["landmarks"]
            d = lm[WRIST] - lm[MIDDLE_MCP]
            toward_elbow = d / (np.linalg.norm(d) + 1e-6)
        site = self.site(segs, toward_elbow) if ainfo["arm"] else None
        if not ainfo["arm"]:
            self.site.reset()
        t2 = time.perf_counter()

        out = self.render(g_out, g, arm, veins, site, ainfo, p)
        t3 = time.perf_counter()

        sx = ow / PROC_W                                             # processing -> output pixels
        info = dict(
            arm=ainfo["arm"], arm_source=ainfo["source"], moving=moving, motion=round(self.motion.level, 2),
            vein_count=len(segs), size=(out.shape[1], out.shape[0]),
            veins=[dict(points=np.round(s["points"].astype(float) * sx, 1).tolist(), width=round(s["width"] * sx, 1),
                        length=round(s["length"] * sx, 1),
                        angle_deg=round(float(np.degrees(np.arctan2(s["direction"][1], s["direction"][0]))), 1))
                   for s in segs],
            best_site=None if site is None else dict(
                x=round(site["x"] * sx, 1), y=round(site["y"] * sx, 1), width=round(site["width"] * sx, 1),
                angle_deg=round(float(np.degrees(np.arctan2(site["dy"], site["dx"]))), 1),
                direction_known=site["arrow"]),
            ms=dict(arm=round((t1 - t0) * 1000, 1), veins=round((t2 - t1) * 1000, 1),
                    render=round((t3 - t2) * 1000, 1)),
            _arm_mask=arm, _vein_mask=veins,
        )
        return out, info

    # ------------------------------------------------------------------ rendering
    def render(self, g_out, g, arm, veins, site, ainfo, p):
        H, W = g_out.shape
        sc = W / PROC_W
        found = ainfo["arm"]

        # faithful IR image: one linear contrast stretch (set from the arm when present), smoothed
        # over time so brightness doesn't pump. No local/artificial enhancement on what is displayed.
        sample = g[arm > 0] if found and (arm > 0).sum() > 500 else g[::2, ::2].ravel()
        lo, hi = np.percentile(sample, (1, 99.5))
        hi = max(hi, lo + 20)
        if self.lo is None:
            self.lo, self.hi = lo, hi
        self.lo, self.hi = 0.85 * self.lo + 0.15 * lo, 0.85 * self.hi + 0.15 * hi
        a = 225.0 / (self.hi - self.lo)
        base = cv2.convertScaleAbs(g_out, alpha=a, beta=15 - self.lo * a)

        if found:                                                    # gently dim everything that isn't arm
            gain = cv2.GaussianBlur(arm, (0, 0), 2)
            gain = cv2.resize(cv2.convertScaleAbs(gain, alpha=0.6, beta=102), (W, H))   # 40% .. 100%
            base = cv2.multiply(base, gain, scale=1 / 255)
        out = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)

        if found and p["show_veins"] and veins.any():
            vm = cv2.resize(cv2.GaussianBlur(veins, (0, 0), 1), (W, H), interpolation=cv2.INTER_LINEAR)
            vm = cv2.threshold(vm, 110, 255, cv2.THRESH_BINARY)[1]
            rim = cv2.subtract(cv2.dilate(vm, _ellipse(2 * max(1, round(1.5 * sc)) + 1)), vm)
            col = np.full_like(out, VEIN_COLORS.get(p["vein_color"], VEIN_COLORS["cyan"]))
            out = cv2.copyTo(cv2.convertScaleAbs(out, alpha=0.2), rim, out)            # dark outline
            out = cv2.copyTo(cv2.addWeighted(col, 0.85, out, 0.15, 0), vm, out)      # bright vein

        if found and p["show_arm"]:
            cnts = cv2.findContours(cv2.resize(arm, (W, H), interpolation=cv2.INTER_NEAREST),
                                    cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
            cv2.drawContours(out, cnts, -1, (200, 200, 200), max(1, round(sc)), cv2.LINE_AA)
            if ainfo["landmarks"] is not None:
                for x, y in ainfo["landmarks"] * sc:
                    cv2.circle(out, (int(x), int(y)), max(2, round(3 * sc)), (255, 255, 255), -1, cv2.LINE_AA)

        if found and p["show_site"] and site is not None:
            self.draw_site(out, site, sc)
        return out

    @staticmethod
    def draw_site(out, s, sc):
        """White ring + direction arrow with a black outline: distinct from the colored veins."""
        c = np.array([s["x"], s["y"]]) * sc
        d = np.array([s["dx"], s["dy"]])
        r, L = round(16 * sc), 55 * sc
        p0, p1 = tuple((c - d * L).astype(int)), tuple((c + d * L).astype(int))
        ci = tuple(c.astype(int))
        for color, th in (((0, 0, 0), round(6 * sc)), ((255, 255, 255), max(2, round(2.5 * sc)))):
            cv2.circle(out, ci, r, color, th, cv2.LINE_AA)
            if s["arrow"]:
                cv2.arrowedLine(out, p0, p1, color, th, cv2.LINE_AA, tipLength=0.25)
            else:
                cv2.line(out, p0, p1, color, th, cv2.LINE_AA)
        txt, fs = "Suggested site", 0.6 * sc
        org = (int(c[0] + r + 8 * sc), int(c[1] - r))
        cv2.putText(out, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), max(3, round(4 * sc)), cv2.LINE_AA)
        cv2.putText(out, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), max(1, round(1.5 * sc)), cv2.LINE_AA)
