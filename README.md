# VeinViz

Hands-free tools for vein procedures, built around a near-infrared vein camera.

| Folder | What it is |
|---|---|
| [`gemini-vein-assistant/`](gemini-vein-assistant/) | **Vein Assist.** A voice assistant: say "Gemini" and ask a question. It answers out loud using Gemini and the live infrared camera frame, and shows a live chat page. |
| [`vein-arm-guide/`](vein-arm-guide/) | **Vein Arm Guide.** A small arm diagram that lights up the recommended region when you say "Gemini, recommended region". Made to view on a Meta Quest in passthrough. |
| [`vein-camera-stream/`](vein-camera-stream/) | **Vein Camera Stream (VeinVision).** The live infrared vein camera feed. Reads the Tapo IR camera, evens out the lighting and boosts contrast so veins stand out, and serves it as a full-screen page and an MJPEG stream that Vein Assist uses. |

## Quick start

Each folder's README has the full steps.

```
# terminal 1: the camera stream (first run creates vein-camera-stream/.env: put your camera address in it)
cd vein-camera-stream
./run.sh

# terminal 2: the assistant (needs a Gemini API key in gemini-vein-assistant/.env)
cd gemini-vein-assistant
cp .env.example .env
./run.sh

# terminal 3: the arm page
cd vein-arm-guide
./serve.sh
```

On Windows, use `run.bat` / `serve.bat` instead of `./run.sh` / `./serve.sh`
(double-click them, or run `.\run.bat` in PowerShell). Install Python 3.11+ from python.org first and tick
"Add python.exe to PATH".

Then open http://localhost:8000 (camera), http://localhost:8080 (assistant) and http://localhost:8081 (arm page).
Vein Assist reads the camera from `CAMERA_STREAM_URL` (`http://<laptop-ip>:8000/stream`).
