"""
VeinVision server (simple)
Live IR camera -> lighting flattened + CLAHE -> full-screen web stream.
Under IR, veins show up as dark lines; flattening + CLAHE makes them easy to see.

Run:
  python server.py --source rtsp://USER:PASS@CAMERA_IP:554/stream1
  or put CAMERA_SOURCE=rtsp://... in a .env file next to this script (kept out of git) and run: python server.py
  (also works with a webcam index "0", a recorded clip "clip.mp4" or a photo "arm.jpg")
Open:  http://localhost:8000   (or http://<laptop-ip>:8000 from another device on the same network)
"""
import argparse
import ipaddress
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
os.environ.setdefault("FLASK_SKIP_DOTENV", "1")                    # we read .env ourselves
import cv2
import numpy as np
from flask import Flask, Response, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def load_env(path):
    """Read KEY=VALUE lines from a local .env file (kept out of git) into the environment."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------- finding the camera
# On a new network (e.g. a phone hotspot) the camera gets a new IP. If the saved address doesn't
# answer, look for the camera (a device with the RTSP port open) on this computer's network.
def safe(src):
    """The source for logs, without the camera password."""
    return "rtsp://" + src.split("@", 1)[1] if src.startswith("rtsp://") and "@" in src else src


def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # picks the network interface; nothing is sent
            return s.getsockname()[0]
    except OSError:
        return None


def find_camera(port):
    """IP of a device on this computer's network with the RTSP port open, or None."""
    me = local_ip()
    if not me:
        return None
    hosts = [str(h) for h in ipaddress.ip_network(f"{me}/24", strict=False).hosts() if str(h) != me]
    with ThreadPoolExecutor(max_workers=64) as pool:
        for host, ok in zip(hosts, pool.map(lambda h: port_open(h, port, 0.5), hosts)):
            if ok:
                return host
    return None


def with_host(src, host):
    """The same rtsp:// address (same login, port and path) pointing at another host."""
    parts = urlsplit(src)
    login = parts.netloc.rpartition("@")[0]
    netloc = (login + "@" if login else "") + host + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


# ---------------------------------------------------------------- camera input
class Source:
    """Always holds the newest frame (no lag build-up). RTSP, webcam index, video file or photo."""

    def __init__(self, src):
        self.src = src
        self.is_file = os.path.isfile(src)
        self.frame = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _say(self, text):  # print a status line only when it changes (the search repeats every few seconds)
        if text != getattr(self, "_last_note", None):
            print(text, flush=True)
            self._last_note = text

    def _locate(self):
        """Make sure the camera answers; if it isn't at its saved address, find it on this network."""
        parts = urlsplit(self.src)
        host, port = parts.hostname, parts.port or 554
        if not host or port_open(host, port):
            return True
        if not getattr(self, "_last_note", "").startswith("[camera] no camera streaming"):  # say it once per search
            self._say(f"[camera] {host} isn't answering; looking for the camera on this network...")
        found = find_camera(port)
        if found:
            self.src = with_host(self.src, found)
            self._say(f"[camera] found a camera at {found}, using it")
            return True
        self._say("[camera] no camera streaming on this network yet: check it's on the same Wi-Fi/hotspot, "
                  "Privacy Mode is off and its Camera Account is set (Tapo app). Still looking...")
        return False

    def _open(self):
        if self.src.startswith("rtsp") and not self._locate():
            return None  # nothing to connect to yet: look again shortly instead of waiting for a timeout
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
                if not self.src.startswith("rtsp") or getattr(self, "_last_note", "").startswith("[camera] found"):
                    self._say(f"[camera] can't open {safe(self.src)}, retrying in 2 s")
                time.sleep(2)
                continue
            self._say(f"[camera] connected to {safe(self.src)}")
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
                        self._say("[camera] stream lost, reconnecting")
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
    load_env(os.path.join(HERE, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.environ.get("CAMERA_SOURCE"), required=not os.environ.get("CAMERA_SOURCE"),
                    help="rtsp://..., webcam index, video file or photo (default: CAMERA_SOURCE from .env)")
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
