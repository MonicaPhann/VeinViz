# VeinViz

Hands-free tools for vein procedures, built around a near-infrared vein camera.

| Folder | What it is |
|---|---|
| [`vein-camera-stream/`](vein-camera-stream/) | **Vein Camera Stream (VeinVision).** The live infrared vein camera feed. Reads the Tapo IR camera, evens out the lighting and boosts contrast so veins stand out, and serves it as a full-screen page and an MJPEG stream that Vein Assist uses. |
| [`gemini-vein-assistant/`](gemini-vein-assistant/) | **Vein Assist.** A voice assistant: say "Gemini" and ask a question. It answers out loud using Gemini and the live infrared camera frame, and shows a live chat page. |
| [`vein-arm-guide/`](vein-arm-guide/) | **Vein Arm Guide.** A small arm diagram that lights up the recommended region when you say "Gemini, recommended region". Made to view on a Meta Quest in passthrough. |

## Quick start

Each folder's README has the full steps. You need Python 3.11+ (from python.org; on Windows, tick
"Add python.exe to PATH"), a Gemini API key, and the camera's address.

Start them in this order:

| | Windows (double-click) | macOS / Linux (one terminal each) |
|---|---|---|
| 1. Camera stream | `vein-camera-stream\run.bat` | `cd vein-camera-stream && ./run.sh` |
| 2. Assistant | `gemini-vein-assistant\run.bat` | `cd gemini-vein-assistant && ./run.sh` |
| 3. Arm page | `vein-arm-guide\serve.bat` | `cd vein-arm-guide && ./serve.sh` |

On first run, each script creates its `.env` file. Put the camera address in `vein-camera-stream/.env` and
your Gemini API key in `gemini-vein-assistant/.env`. On Windows, the assistant opens Notepad for the key.

Then open http://localhost:8000 (camera), http://localhost:8080 (assistant) and http://localhost:8081 (arm page).
Vein Assist reads the camera from `CAMERA_STREAM_URL` in its `.env` (`http://<laptop-ip>:8000/stream`).
