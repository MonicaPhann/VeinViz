"""
Offline test: run the full VeinVision pipeline on a recorded IR clip, a photo, or a folder of photos,
and check the acceptance criteria (arm detection rate, no veins off the arm, flicker, speed).

  python tools/offline_test.py --source clip.mp4 --out debug.mp4
  python tools/offline_test.py --source photos/ --out debug.mp4      # each photo held for 15 frames
  python tools/offline_test.py --source arm.jpg --out debug.png      # side-by-side image of the result
  python tools/offline_test.py --source clip.mp4 --no-hand-model     # compare with the shape fallback

Debug output = [what the clinician sees | detected arm (green) + hand landmarks + timings].
"""
import argparse
import glob
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import DEFAULTS, PROC_W, Pipeline  # noqa: E402

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def frames(src, hold, limit):
    if os.path.isdir(src):
        files = sorted(f for f in glob.glob(os.path.join(src, "*")) if f.lower().endswith(IMAGE_EXT))
        for f in files:
            img = cv2.imread(f)
            if img is None:
                continue
            for _ in range(hold):
                yield os.path.basename(f), img
    elif src.lower().endswith(IMAGE_EXT):
        img = cv2.imread(src)
        if img is None:
            sys.exit(f"can't read {src}")
        for _ in range(limit or hold):
            yield os.path.basename(src), img
    else:
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            sys.exit(f"can't open {src}")
        n = 0
        while not limit or n < limit:
            ok, f = cap.read()
            if not ok:
                break
            n += 1
            yield f"frame {n}", f


def side_by_side(out, frame, arm, info, label):
    h, w = out.shape[:2]
    g = cv2.cvtColor(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (w, h)), cv2.COLOR_GRAY2BGR)
    a = cv2.resize(arm, (w, h), interpolation=cv2.INTER_NEAREST)
    g[a > 0] = (0.6 * g[a > 0] + 0.4 * np.array([60, 200, 60])).astype(np.uint8)
    cv2.drawContours(g, cv2.findContours(a, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], -1, (60, 255, 60), 2)
    txt = f"{label} | arm: {info['arm_source'] or 'none'} | moving: {info['moving']} | {info['ms']}"
    cv2.putText(g, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * w / 640, (255, 255, 255), 2, cv2.LINE_AA)
    return np.hstack([out, g])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", help="debug .mp4 (or .png/.jpg for the last frame)")
    ap.add_argument("--frames", type=int, default=0, help="max frames (0 = all)")
    ap.add_argument("--hold", type=int, default=15, help="frames per photo")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--sens-level", type=int, default=DEFAULTS["sens_level"])
    ap.add_argument("--no-hand-model", action="store_true")
    ap.add_argument("--allow-no-hand", action="store_true", help="enable the shape fallback (see server.py)")
    ap.add_argument("--show-site", action="store_true")
    a = ap.parse_args()

    pipe = Pipeline(out_w=a.width, use_model=not a.no_hand_model, fallback=a.allow_no_hand)
    p = dict(DEFAULTS, sens_level=a.sens_level, show_site=a.show_site)
    writer, last = None, None
    n, src_count, off_arm, ious, times, ms = 0, {}, 0, [], [], []
    prev_veins = None
    for label, f in frames(a.source, a.hold, a.frames):
        t = time.perf_counter()
        out, info = pipe.run(f, p)
        cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])         # include encode cost, like the server
        times.append(time.perf_counter() - t)
        ms.append([info["ms"]["arm"], info["ms"]["veins"], info["ms"]["render"]])
        n += 1
        src_count[info["arm_source"] or "none"] = src_count.get(info["arm_source"] or "none", 0) + 1
        arm, veins = info["_arm_mask"], info["_vein_mask"]
        off_arm += int(((veins > 0) & (arm == 0)).sum())
        if prev_veins is not None and info["arm"] and not info["moving"]:
            u = ((veins > 0) | (prev_veins > 0)).sum()
            if u > 50:
                ious.append(((veins > 0) & (prev_veins > 0)).sum() / u)
        prev_veins = veins
        if a.out:
            last = side_by_side(out, f, arm, info, label)
            if a.out.lower().endswith(".mp4"):
                if writer is None:
                    writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), 15, (last.shape[1], last.shape[0]))
                writer.write(last)
    if writer:
        writer.release()
    elif a.out and last is not None:
        cv2.imwrite(a.out, last)
    if not n:
        sys.exit("no frames")

    warm = times[min(5, n - 1):]                                      # skip model warm-up
    fps = 1 / np.mean(warm)
    stable = float(np.mean(ious)) if ious else float("nan")
    print(f"\nframes: {n}   processing {PROC_W}px, output {a.width}px")
    print("arm found by: " + ", ".join(f"{k} {v / n:.0%}" for k, v in sorted(src_count.items())))
    print(f"speed: {fps:.1f} fps  (arm {np.mean([m[0] for m in ms]):.0f} ms, veins {np.mean([m[1] for m in ms]):.0f} ms,"
          f" render {np.mean([m[2] for m in ms]):.0f} ms per frame)")
    print(f"vein pixels outside the arm: {off_arm}")
    print(f"frame-to-frame vein overlap while still (1.0 = no flicker): {stable:.2f}")
    print("\nchecks:")
    print(f"  [{'PASS' if fps >= 15 else 'FAIL'}] >= 15 fps")
    print(f"  [{'PASS' if off_arm == 0 else 'FAIL'}] no veins off the arm")
    print(f"  [{'PASS' if ious and stable >= 0.8 else 'CHECK'}] steady veins (overlap >= 0.80)")
    if a.out:
        print(f"\ndebug output: {a.out}")


if __name__ == "__main__":
    main()
