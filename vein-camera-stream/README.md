# Vein Camera Stream (VeinVision)

The live near-infrared vein camera feed that the other VeinViz tools use.
It reads a Tapo IR camera over RTSP, makes the veins stand out, and serves the result as a full-screen web page
and an MJPEG stream (`/stream`). Vein Assist (`../gemini-vein-assistant`) grabs its camera frames from this stream.

Under infrared light, blood absorbs more than skin, so veins look like darker lines. The server evens out the uneven
IR lighting, boosts local contrast (CLAHE), darkens thin dark lines and applies a gamma curve, so veins are easy to see.

```
Tapo (IR mode) --RTSP--> server.py (flatten lighting + CLAHE + darken veins + gamma) --MJPEG--> index.html (full screen, image only)
                                                                                      └──> /stream (used by Vein Assist)
```

Assistive / training tool only, not a medical device.

## 1. Physical setup
- Tapo pointing **straight down**, 30–50 cm above the table, over a **dark towel**.
- Tapo app: Night Vision = **Infrared**, motion tracking / patrol **OFF**, timestamp and logo overlays **OFF**.
- Laptop and camera on the **same network** (a phone hotspot works; some campus Wi-Fi blocks it). Dim the room lights near the rig.

## 2. Install
```
pip install -r requirements.txt
```

## 3. Run
```
python server.py --source rtsp://USER:PASS@CAMERA_IP:554/stream1
```
Use the camera account you created in the Tapo app. **Don't commit your real camera URL**: it contains the password.

Open **http://localhost:8000**. The page shows only the live image. Use the browser's full-screen mode (F11 / Ctrl+Cmd+F).
Other devices on the same network (e.g. Vein Assist, a Meta Quest browser) use `http://<laptop-ip>:8000/stream`.

Options:
- `--contrast 3.0`: CLAHE strength (higher = more contrast, more grain)
- `--darken 1.5`: extra darkening of thin dark lines (veins); 0 = off, 1–3 typical
- `--gamma 1.4`: >1 pushes dark tones darker so veins separate from skin; 1.0 = off
- `--width 1280`: lower = faster
- `--port 8000`

Test without the camera: `--source clip.mp4` or `--source photo.jpg` (a webcam index like `0` also works).

## 4. Read the stream from your own code
```python
import cv2
cap = cv2.VideoCapture("http://localhost:8000/stream")
ok, frame = cap.read()
```

## 5. Troubleshooting
- **"can't open" even though the camera is on**: the Tapo allows only **one viewer per stream**. Close other viewers
  (another server, VLC, the Tapo app's live view) or use `stream2` (720p).
- **Black page**: check the terminal. `[camera] can't open` means a wrong IP/password, or the laptop isn't on the same network.
- **Laggy**: use `stream2` in the RTSP URL or `--width 960`.
- **Too noisy / grainy**: lower `--contrast` (e.g. 2.0).

## 6. Archive (experiments)
`archive/` keeps earlier versions we tried, for reference. See [`archive/README.md`](archive/README.md).
