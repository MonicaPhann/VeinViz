# VeinViz

Hands-free tools for vein procedures, built around a near-infrared vein camera.

| Folder | What it is |
|---|---|
| [`gemini-vein-assistant/`](gemini-vein-assistant/) | **Vein Assist.** A voice assistant: say "Gemini" and ask a question. It answers out loud using Gemini and the live infrared camera frame, and shows a live chat page. |
| [`vein-arm-guide/`](vein-arm-guide/) | **Vein Arm Guide.** A small arm diagram that lights up the recommended region when you say "Gemini, recommended region". Made to view on a Meta Quest in passthrough. |

## Quick start

Each folder's README has the full steps.

```
# terminal 1: the assistant (needs a Gemini API key in gemini-vein-assistant/.env)
cd gemini-vein-assistant
cp .env.example .env
./run.sh

# terminal 2: the arm page
cd vein-arm-guide
./serve.sh
```

Then open http://localhost:8080 (assistant) and http://localhost:8081 (arm page).
