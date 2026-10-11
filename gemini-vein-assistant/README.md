# Vein Assist

A hands-free Gemini assistant for vein procedures (blood draws, IV cannulation and similar).
Say **"Gemini"** and then your question. The answer is spoken aloud and streams into a live chat page.
Each question also sends Gemini the current frame from the infrared vein camera.

## Requirements

- **Python 3.11 or newer.** Check with `python3 --version` (Mac/Linux) or `py --version` (Windows).
  Get it from https://www.python.org/downloads/. On Windows, tick **"Add python.exe to PATH"** in the installer.
- **A Gemini API key** from https://aistudio.google.com/apikey
- **Chrome, Edge or Firefox** for the page. The browser asks for microphone access once.
- Optional: the VeinViz infrared camera stream on the same network (default `http://192.168.1.143:8000/stream`).
  Without it, the assistant still works and says the camera frame is unavailable.

Works on Windows 10/11 (64-bit), macOS 13+ (Apple Silicon or Intel) and Linux.

## Start it

### Windows

1. Open the `gemini-vein-assistant` folder and double-click **`run.bat`**.
2. The first run takes a few minutes. It sets up the Python packages and downloads the speech model
   (about 500 MB, one time only).
3. If there's no API key yet, Notepad opens `.env`. Paste your key after `GEMINI_API_KEY=`, save, and
   close Notepad. The server then starts.
4. If Windows Firewall asks about Python, click **Allow** (private networks). That's only needed for
   other devices on your Wi-Fi, like a second screen.
5. When the window shows `Uvicorn running on http://0.0.0.0:8080`, open **http://localhost:8080**,
   click the mic, and allow microphone access.

To stop it, press Ctrl+C in that window, or close it.

### macOS / Linux

1. In a terminal:
   ```
   cd gemini-vein-assistant
   ./run.sh
   ```
2. The first time, it creates `.env` and stops to ask for your key. Open `.env`, paste the key after
   `GEMINI_API_KEY=`, save, and run `./run.sh` again.
3. The first full start takes a few minutes. It sets up the Python packages and downloads the speech
   model (about 500 MB, one time only).
4. When the terminal shows `Uvicorn running on http://0.0.0.0:8080`, open **http://localhost:8080**,
   click the mic, and allow microphone access.

To stop it, press Ctrl+C.

After changing any code, stop the server and start it again. Never commit `.env`: it holds your key
and is listed in `.gitignore`.

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

## Spoken answers

The server reads answers aloud with a local Piper voice and sends the audio to the device that asked, so every
device sounds the same and devices whose browser has no voices (like the Meta Quest) still speak. The voice is
downloaded once on the first start (about 60 MB, into `voices/`). If it isn't available, the page uses the
browser's own voice instead.

## Second screen

Any device on the same network can open `http://<this-computer's-IP>:8080` to follow the chat live.
The server prints this link when it starts (`Other devices on this network: http://…:8080`).
Browsers only allow the mic on `localhost` or HTTPS, so those other screens are view-only.

## HTTPS: the mic on other devices, and AR on the Quest

Browsers only allow the microphone, and the Quest only allows AR, on `localhost` or https. So the server also
runs an https address, using a certificate it makes for itself (saved in `certs/`, which is never committed).
It prints the links when it starts:

```
One link for everything (mic on any device; on the Quest, View in AR shows the chat, mic and arm):
  https://192.168.1.143:8443   (first visit on each device: tap Advanced -> Proceed)
  Arm only: https://192.168.1.143:8443/arm/
```

The first time each device opens the https link, the browser warns that the connection isn't private, because
the certificate isn't from a public authority. Tap **Advanced → Proceed** (Chrome/Edge/Quest) or
**Show Details → visit this website** (Safari). After that, the mic works on that device. The plain http links
keep working as before.

## AR on the Meta Quest (passthrough)

Vein Assist can float in your real room: the chat as a glass panel, the mic orb under it, and the arm
hologram beside it, lighting up the recommended region. (The Quest runs one AR page at a time, so the arm is
included here; the arm page's own AR view is still there if you only want the arm.)

1. On the Quest, open the **HTTPS** link Vein Assist prints, e.g. `https://192.168.1.143:8443`.
   The first time, tap **Advanced → Proceed**.
2. Tap the mic once and allow microphone access (easier to answer the permission prompt before AR).
3. Tap **View in AR** (top right).
4. **Pinch the orb** to turn the mic on or off. To **move the chat or the arm**, point roughly at it (a soft
   frame lights up around whatever a pinch will grab) or reach your hand near it, then pinch and carry it.
   Press the Meta button to leave AR.

The orb shows the same states as the page: still when off or waiting for "Gemini", ripples while you're
talking to it, a spinning arc while it thinks, and bars while it speaks. Answers stream onto the panel as
they're written. The AR view needs the `vein-arm-guide` folder next to this one (it uses its 3D code), and
loads three.js from the internet. To check it on a computer, add `?ar-preview` to the address.

## Config (`.env`)

| Variable | Default |
|---|---|
| `GEMINI_API_KEY` | (required) |
| `GEMINI_MODEL` | `gemini-flash-lite-latest` (about 1.5s per answer; `gemini-flash-latest` took 80s+ on this key) |
| `WHISPER_MODEL` | `auto`: tests this computer at startup and picks `small.en`, `base.en` or `tiny.en` (most to least accurate) |
| `CAMERA_STREAM_URL` | `http://192.168.1.143:8000/stream` |
| `PORT` | `8080` |
| `HTTPS_PORT` | `8443` (`off` to turn HTTPS off) |
| `TTS_VOICE` | `en_US-lessac-medium`: the Piper voice that reads answers aloud (any voice from huggingface.co/rhasspy/piper-voices) |

If your camera runs at a different address, change `CAMERA_STREAM_URL`.
The chat lives in memory only. Restarting the server starts a fresh session.

## If it's slow

The server window prints where the time goes:

```
[speech] small.en: test clip took 0.64s                 <- speed test at startup
[speech] 3.5s of speech -> text in 0.90s                <- speech recognition, on this computer
[timing] camera 0.05s · first words 1.20s · full answer 1.90s
```

- **Speech recognition is slow** (more than about 2s): it runs on this computer's processor. Plug the laptop in and
  set Windows power mode to **Best performance**. Keep `WHISPER_MODEL=auto`, or set `base.en` or `tiny.en`.
- **The camera step is slow, or says it can't reach the camera:** set `CAMERA_STREAM_URL` to the camera's real
  address on this network, or leave it empty if there's no camera. An unreachable camera only costs about a second,
  then the assistant skips it for 20 seconds.
- **Gemini's first words take more than 3 seconds:** check `GEMINI_MODEL` is `gemini-flash-lite-latest`, look for
  `429` (quota) errors in the window, and try another network. School or work Wi-Fi and VPNs can slow API calls.

## Notes

- Speech is transcribed locally with Whisper, so room audio never leaves this computer. Only the
  questions you ask after "Gemini", and the camera frames, are sent to the Gemini API. Check that this meets
  your organisation's rules before you use it with real patient information.
- This is a support tool. It doesn't replace clinical judgement or local protocols.
