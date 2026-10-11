# Vein Arm Guide

A small page showing a generic arm. It lights up the **recommended region** for a blood draw
when a clinician asks the Vein Assist chatbot for one. It's meant to be viewed on a Meta Quest
in passthrough.

## Start it

You need Python 3 (it only serves the page) and the Vein Assist assistant from
`../gemini-vein-assistant`, which supplies the recommendations. Start Vein Assist first; see its README.

### Windows

1. Start Vein Assist: double-click `gemini-vein-assistant\run.bat`.
2. Double-click **`vein-arm-guide\serve.bat`**. If Windows Firewall asks about Python, click **Allow**
   (private networks) so the Quest can reach it.
3. Open **http://localhost:8081**.

### macOS / Linux

1. Start Vein Assist in one terminal: `cd gemini-vein-assistant && ./run.sh`
2. In a second terminal:
   ```
   cd vein-arm-guide
   ./serve.sh
   ```
3. Open **http://localhost:8081**.

### On the Quest

Open `http://<this-computer's-IP>:8081` in the Quest browser. It needs no microphone, so plain http
over your Wi-Fi works. To find the IP, run `ipconfig getifaddr en0` on a Mac, or `ipconfig` on
Windows and look for the "IPv4 Address".

To stop it, press Ctrl+C. The page shows "Waiting for Vein Assist…" until the assistant is running.

## Use

Say **"Gemini, recommended region"**. You can add context, for example "Gemini, the median cubital
failed, what's the recommended region now?". Gemini picks one of six fixed sites, the arm lights it
up and labels it, and the chatbot says why.

| Site | Where |
|---|---|
| `median_cubital` | Elbow crease |
| `cephalic_antecubital` | Elbow, thumb side |
| `basilic_antecubital` | Elbow, little-finger side |
| `cephalic_forearm` | Forearm, thumb side |
| `basilic_forearm` | Forearm, little-finger side |
| `dorsal_hand` | Back of the hand |

The button in the top-right corner cycles the theme: light (white), dark (black, best for
passthrough) and Frutiger Aero.

The arm is a right arm, palm up, so the thumb side is on the left of the picture. The highlight
stays until the next recommendation.

## Options

- `?server=host:port`: point at a Vein Assist server somewhere else (default: same host, port 8080).
- `?site=median_cubital`: preview a highlight without the server.

This shows a *recommended region* on a generic arm, not this patient's veins. Always confirm by
palpation and follow local protocols.
