"""
VeinVision server (simple)
Live IR camera -> lighting flattened + CLAHE -> full-screen web stream.
Under IR, veins show up as dark lines; flattening + CLAHE makes them easy to see.

Run:
  python server.py --source rtsp://USER:PASS@CAMERA_IP:554/stream1
  (also works with a webcam index "0", a recorded clip "clip.mp4" or a photo "arm.jpg")
Open:  http://localhost:8000   (or http://<laptop-ip>:8000 from another device on the same network)
"""
import argparse
import logging
import os
import threading
import time

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2
import numpy as np
from flask import Flask, Response, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


# ---------------------------------------------------------------- camera input
class Source:
    """Always holds the newest frame (no lag build-up). RTSP, webcam index, video file or photo."""

    def __init__(self, src):
        self.src = src
        self.is_file = os.path.isfile(src)
        self.frame = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self):
        s = int(self.src) if self.src.isdigit() else self.src
        cap = cv2.VideoCapture(s, cv2.CAP_FFMPEG) if str(s).startswith("rtsp") else cv2.VideoCapture(s)
        return cap if cap.isOpened() else None

    def _loop(self):
        if self.is_file and self.src.lower().endswith(IMAGE_EXT):  # photo: replay it at ~15 fps
            img = cv2.imread(self.src)
            while img is not None:
                self.frame = img.copy()
                time.sleep(1 / 15)
        while True:
            cap = self._open()
            if cap is None:
                print(f"[camera] can't open {self.src}, retrying in 2 s", flush=True)
                time.sleep(2)
                continue
            print("[camera] connected", flush=True)
            fps = cap.get(cv2.CAP_PROP_FPS) or 15
            fails = 0
            while True:
                ok, f = cap.read()
                if not ok or f is None:
                    if self.is_file:                                 # loop recorded clips
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    fails += 1
                    if fails > 30:                                   # stream dropped -> reconnect
                        print("[camera] stream lost, reconnecting", flush=True)
                        break
                    time.sleep(0.05)
                    continue
                fails = 0
                self.frame = f
                if self.is_file:
                    time.sleep(1 / fps)
            cap.release()


# ---------------------------------------------------------------- enhancement
class Enhancer:
    def __init__(self, out_w, contrast, darken, gamma):
        self.out_w, self.darken = out_w, darken
        self.clahe = cv2.createCLAHE(clipLimit=contrast, tileGridSize=(8, 8))
        self.lut = (255 * (np.arange(256) / 255) ** gamma).astype(np.uint8)   # gamma > 1 = darker darks

    def __call__(self, frame):
        h, w = frame.shape[:2]
        if w != self.out_w:
            frame = cv2.resize(frame, (self.out_w, round(h * self.out_w / w)), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        H, W = g.shape

        # 1. flatten the uneven IR lighting: divide by a heavy blur (computed at 1/4 size for speed)
        small = cv2.resize(g, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        light = cv2.resize(cv2.GaussianBlur(small, (0, 0), W / 48), (W, H), interpolation=cv2.INTER_LINEAR)
        flat = np.clip((g + 10) / (light + 10) * 128, 0, 255).astype(np.uint8)
        flat = cv2.GaussianBlur(flat, (0, 0), 1.0)                 # light denoise so grain isn't boosted

        # 2. CLAHE local contrast boost
        c = self.clahe.apply(flat).astype(np.float32)

        # 3. make veins darker: only pixels darker than their surroundings (thin dark lines) are
        #    pushed further down; nothing is brightened, so skin stays as it is
        around = cv2.GaussianBlur(c, (0, 0), W / 90)               # ~vein-sized neighborhood
        c += self.darken * np.minimum(c - around, 0)

        # 4. gamma: pull dark/mid tones down more than highlights -> bigger vein/skin gap
        return cv2.LUT(np.clip(c, 0, 255).astype(np.uint8), self.lut)


# ---------------------------------------------------------------- processing loop
class Processor:
    def __init__(self, source, enhance):
        self.source, self.enhance = source, enhance
        self.jpg, self.seq = None, 0
        self.cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        last = None
        while True:
            f = self.source.frame
            if f is None or f is last:
                time.sleep(0.003)
                continue
            last = f
            try:                                                     # one bad frame never kills the stream
                ok, jpg = cv2.imencode(".jpg", self.enhance(f), [cv2.IMWRITE_JPEG_QUALITY, 85])
            except Exception as e:
                print("[process]", repr(e), flush=True)
                continue
            if ok:
                with self.cond:
                    self.jpg, self.seq = jpg.tobytes(), self.seq + 1
                    self.cond.notify_all()

    def frames(self):
        seen = -1
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.seq != seen, timeout=2)
                if self.seq == seen or self.jpg is None:
                    continue
                seen, jpg = self.seq, self.jpg
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                   + b"\r\n\r\n" + jpg + b"\r\n")


# ---------------------------------------------------------------- web server
def create_app(proc):
    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():
        return send_from_directory(HERE, "index.html")

    @app.route("/stream")
    def stream():
        return Response(proc.frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-cache"})

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="rtsp://..., webcam index, video file or photo")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--width", type=int, default=1280, help="output width (lower = faster)")
    ap.add_argument("--contrast", type=float, default=3.0, help="CLAHE clip limit (higher = more contrast)")
    ap.add_argument("--darken", type=float, default=1.5, help="extra darkening of veins (0 = off, 1-3 typical)")
    ap.add_argument("--gamma", type=float, default=1.4, help=">1 makes dark areas darker (1.0 = off)")
    a = ap.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    proc = Processor(Source(a.source), Enhancer(a.width, a.contrast, a.darken, a.gamma))
    print(f"Open http://localhost:{a.port}", flush=True)
    create_app(proc).run(host="0.0.0.0", port=a.port, threaded=True)
