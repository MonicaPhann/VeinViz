# Vein Assist

A hands-free Gemini assistant for vein procedures (blood draws, IV cannulation and similar).
Say **"Gemini"** and then your question. The answer is spoken aloud and streams into a live chat page.
Each question also sends Gemini the current frame from the infrared vein camera.

## Requirements

- Python 3.11 or newer (check with `python3 --version`, or `py --version` on Windows). Tested on Python 3.14 on an Apple Silicon Mac.
- A Gemini API key from https://aistudio.google.com/apikey
- Optional: the VeinViz infrared camera stream on the same network (default `http://192.168.1.143:8000/stream`).
  Without it, the assistant still works and tells you the camera frame is unavailable.

## Start it

1. Go into this folder and create your settings file:
   ```
   cd gemini-vein-assistant
   cp .env.example .env
   ```
   On Windows: `copy .env.example .env`
2. Open `.env` and paste your key after `GEMINI_API_KEY=`. Never commit `.env`; it's in `.gitignore`.
3. Start the server:
   ```
   ./run.sh
   ```
   On Windows, double-click `run.bat` or run `.\run.bat`.
   The first run takes a few minutes. It creates a `.venv` folder, installs the Python packages, and
   downloads the speech model (about 500 MB, one time only).
4. When the terminal shows `Uvicorn running on http://0.0.0.0:8080`, open **http://localhost:8080**,
   tap the mic and allow microphone access.

To stop it, press Ctrl+C in the terminal. After changing any code, stop it and run `./run.sh` (or `run.bat`) again.

## Using it

| You say | What happens |
|---|---|
| "Gemini, what gauge should I use for this vein?" | Grabs a camera frame, answers out loud, and adds the answer to the chat |
| "Gemini" … (pause) … "is the tourniquet on too long?" | Also works: after "Gemini" it keeps listening for up to 8 seconds |
| "Gemini" while it's talking | Stops the current answer and listens to the new question |
| "Gemini, stop" | Stops talking |
| "Gemini, recommended region" | Picks a site and lights it up on the arm page (`../vein-arm-guide`) |

Mic states: **grey** = off · **teal** = waiting for "Gemini" · **teal with ripples** = listening to you ·
**indigo spinning arc** = thinking · **blue bars** = speaking.

The button in the top-right corner cycles the theme: light, dark and Frutiger Aero.

## Second screen

Any device on the same network can open `http://<this-computer's-IP>:8080` to follow the chat live.
Browsers only allow the mic on `localhost` or HTTPS, so those other screens are view-only.

## Config (`.env`)

| Variable | Default |
|---|---|
| `GEMINI_API_KEY` | (required) |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` (about 1.5s per answer; `gemini-flash-latest` took 80s+ on this key) |
| `WHISPER_MODEL` | `small.en` (`base.en` is faster but less accurate) |
| `CAMERA_STREAM_URL` | `http://192.168.1.143:8000/stream` |
| `PORT` | `8080` |

If your camera runs at a different address, change `CAMERA_STREAM_URL`.
The chat lives in memory only. Restarting the server starts a fresh session.

## Notes

- Speech is transcribed locally with Whisper, so room audio never leaves this computer. Only the
  questions you ask after "Gemini", and the camera frames, are sent to the Gemini API. Check that this meets
  your organisation's rules before you use it with real patient information.
- This is a support tool. It doesn't replace clinical judgement or local protocols.
