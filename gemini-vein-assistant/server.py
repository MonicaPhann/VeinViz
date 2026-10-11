"""Gemini Vein Assist: a voice-driven clinical helper for vein procedures.

The browser streams speech from the mic to this server, which transcribes it
locally with Whisper. When the page hears the wake word "Gemini" it sends the
question back, and this server answers with Gemini, attaching the latest frame
from the infrared MJPEG vein camera. Every connected page sees the same live
chat, so a second screen can follow along.
"""

import asyncio
import json
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from faster_whisper import WhisperModel

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en").strip()
CAMERA_URL = os.getenv("CAMERA_STREAM_URL", "http://192.168.1.143:8000/stream").strip()
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))

SYSTEM_PROMPT = """You are a hands-free clinical assistant supporting nurses and doctors \
during procedures on a patient's veins: venipuncture and blood draws, peripheral IV \
cannulation, blood cultures, and similar vascular access work.

How you answer:
- Your replies are read aloud while the clinician's hands are busy. Keep them short: \
one to three plain sentences unless they ask for steps or detail.
- When listing steps, give at most five, each one short. Avoid tables, headings and \
heavy formatting. Light **bold** for a key number or warning is fine.
- Never say the word "Gemini" in a reply (it is the wake word and would re-trigger you).
- If the question is unclear or was probably mis-heard, ask a brief clarifying question.

The camera:
- When available, each question comes with the current frame from a near-infrared \
vein viewer. In near-infrared images, superficial veins usually appear as dark lines \
against lighter tissue.
- Only describe the image when it's relevant to the question. Describe what you can \
actually see (vein position, direction, apparent size, branching, anything obscuring the \
view) and say plainly when the image is blurry, dark, or doesn't show a usable vein.
- Never present an image read as certain. Infrared shows superficial veins only; it \
doesn't show depth, valves, or arteries reliably.

Safety:
- Support, don't replace, clinical judgement and local protocols. Give standard \
evidence-based guidance (e.g. order of draw, gauge selection, tourniquet time, site \
selection, infection control) and mention when local policy may differ.
- If something described sounds like an emergency or adverse event (arterial puncture, \
nerve injury signs, haematoma growth, syncope, anaphylaxis, extravasation), lead with \
the immediate action to take.
"""

# Biases Whisper toward the wake word and procedure vocabulary.
HOTWORDS = (
    "Gemini tourniquet venipuncture phlebotomy cannula cannulation antecubital "
    "cephalic basilic median cubital gauge butterfly vacutainer haematoma"
)
# Saying "recommended region" lights up a site on the separate arm page (vein-arm-guide).
REGION_TRIGGER = re.compile(r"\brecommend(?:ed)?\s+(?:a\s+|the\s+)?region\b", re.I)
REGION_SITES = {
    "median_cubital": "Median cubital vein, elbow crease",
    "cephalic_antecubital": "Cephalic vein at the elbow, thumb side",
    "basilic_antecubital": "Basilic vein at the elbow, little-finger side",
    "cephalic_forearm": "Cephalic vein on the forearm, thumb side",
    "basilic_forearm": "Basilic vein on the forearm, little-finger side",
    "dorsal_hand": "Dorsal hand veins, back of the hand",
}
REGION_PROMPT = """Choose the single most suitable region for this venipuncture from the allowed sites.
Use the near-infrared frame only if it clearly shows the patient's arm or hand; otherwise follow \
standard practice: median cubital first, then cephalic, with basilic last because the brachial \
artery and median nerve lie close to it. Take into account anything said earlier in this \
conversation (a failed attempt, a site to avoid, the procedure type). Keep the reason under 15 words."""

SAMPLE_RATE = 16000
PARTIAL_EVERY_BYTES = int(SAMPLE_RATE * 2 * 0.6)  # re-transcribe every 0.6s of new audio
MIN_UTTERANCE_BYTES = int(SAMPLE_RATE * 2 * 0.25)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=load_whisper, daemon=True).start()  # load in the background
    yield


client = genai.Client(api_key=API_KEY) if API_KEY else None
app = FastAPI(title="Gemini Vein Assist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# ---- session state (in memory: restarting the server starts a fresh session) ----
messages: list[dict] = []
sockets: set[WebSocket] = set()
current_task: asyncio.Task | None = None
current_region: dict | None = None  # {"site", "label", "reason"} shown on the arm page


# ---- local speech-to-text ----
whisper: WhisperModel | None = None
whisper_ready = threading.Event()
whisper_lock = threading.Lock()


def load_whisper() -> None:
    global whisper
    t = time.time()
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    whisper_ready.set()
    print(f"[speech] Whisper {WHISPER_MODEL} ready in {time.time() - t:.1f}s")


def transcribe(pcm: bytes, partial: bool) -> str | None:
    """16 kHz mono int16 PCM -> text. Partials are skipped (None) if Whisper is busy."""
    if not whisper_ready.is_set():
        return None
    if not whisper_lock.acquire(blocking=not partial):
        return None
    try:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = whisper.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,  # drops non-speech, which stops Whisper inventing words on noise
            hotwords=HOTWORDS,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
    finally:
        whisper_lock.release()


async def broadcast(event: dict) -> None:
    dead = []
    for ws in list(sockets):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.discard(ws)


async def grab_frame(timeout: float = 3.0) -> bytes | None:
    """Pull one complete JPEG out of the MJPEG stream, or None if unavailable."""
    if not CAMERA_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            async with http.stream("GET", CAMERA_URL) as resp:
                resp.raise_for_status()
                buf = b""
                async with asyncio.timeout(timeout):
                    async for chunk in resp.aiter_bytes():
                        buf += chunk
                        start = buf.find(b"\xff\xd8")
                        if start == -1:
                            buf = buf[-1:]
                            continue
                        end = buf.find(b"\xff\xd9", start + 2)
                        if end != -1:
                            return buf[start : end + 2]
                        if len(buf) > 8_000_000:
                            return None
    except Exception as exc:  # camera offline, timeout, bad stream
        print(f"[camera] frame grab failed: {exc!r}")
    return None


def build_contents(history: list[dict], question: str, frame: bytes | None) -> list[types.Content]:
    contents: list[types.Content] = []
    # Prior turns go in as text only; only the current question carries the image.
    for m in [m for m in history if m["text"].strip()][-HISTORY_TURNS:]:
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["text"])]))

    parts: list[types.Part] = []
    if frame:
        parts.append(types.Part.from_bytes(data=frame, mime_type="image/jpeg"))
        parts.append(types.Part.from_text(text="(Current near-infrared vein camera frame attached.)"))
    else:
        parts.append(types.Part.from_text(text="(Vein camera frame unavailable for this question.)"))
    parts.append(types.Part.from_text(text=question))
    contents.append(types.Content(role="user", parts=parts))
    return contents


async def pick_region(history: list[dict], question: str, frame: bytes | None) -> dict:
    """Ask Gemini for one site from the fixed list (structured output), so the arm can only show real zones."""
    site, reason = "median_cubital", "Standard first choice."
    try:
        resp = await client.aio.models.generate_content(
            model=MODEL,
            contents=build_contents(history, f"{question}\n\n{REGION_PROMPT}", frame),
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "site": types.Schema(type=types.Type.STRING, enum=list(REGION_SITES)),
                        "reason": types.Schema(type=types.Type.STRING),
                    },
                    required=["site", "reason"],
                ),
            ),
        )
        data = json.loads(resp.text or "{}")
        if data.get("site") in REGION_SITES:
            site, reason = data["site"], str(data.get("reason", "")).strip() or reason
    except Exception as exc:
        print(f"[region] falling back to the standard site: {exc!r}")
    return {"site": site, "label": REGION_SITES[site], "reason": reason}


async def answer(history: list[dict], question: str, origin: str) -> None:
    reply = {
        "id": uuid.uuid4().hex,
        "role": "assistant",
        "text": "",
        "ts": time.time(),
        "origin": origin,
        "camera": False,
        "done": False,
        "error": None,
    }
    messages.append(reply)
    await broadcast({"type": "message", "message": reply})

    try:
        if client is None:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to the .env file and restart.")

        frame = await grab_frame()
        reply["camera"] = frame is not None
        await broadcast({"type": "meta", "id": reply["id"], "camera": reply["camera"]})

        if REGION_TRIGGER.search(question):
            global current_region
            current_region = await pick_region(history, question, frame)
            await broadcast({"type": "region", "region": current_region})
            # Keep the spoken answer consistent with what the arm page shows.
            question = (
                f"{question}\n\n(The arm display now highlights the recommended region: "
                f"{current_region['label']}. Reason: {current_region['reason']} In one or two sentences, "
                "tell the clinician this recommended region and why. Present it as a recommendation, not a certainty.)"
            )

        contents = build_contents(history, question, frame)
        stream = await client.aio.models.generate_content_stream(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.4),
        )
        async for chunk in stream:
            text = chunk.text or ""
            if text:
                reply["text"] += text
                await broadcast({"type": "delta", "id": reply["id"], "text": text})
    except asyncio.CancelledError:
        reply["error"] = "Interrupted by a new question."
        raise
    except Exception as exc:
        print(f"[gemini] error: {exc!r}")
        reply["error"] = str(exc) or exc.__class__.__name__
    finally:
        reply["done"] = True
        await broadcast({"type": "done", "id": reply["id"], "error": reply["error"]})


async def handle_ask(text: str, origin: str) -> None:
    global current_task
    text = text.strip()
    if not text:
        return
    # The newest question wins: stop any answer still being generated.
    if current_task and not current_task.done():
        current_task.cancel()
        try:
            await current_task
        except (asyncio.CancelledError, Exception):
            pass

    history = list(messages)
    user_msg = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "text": text,
        "ts": time.time(),
        "origin": origin,
        "done": True,
    }
    messages.append(user_msg)
    await broadcast({"type": "message", "message": user_msg})
    current_task = asyncio.create_task(answer(history, text, origin))


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    sockets.add(ws)
    await ws.send_json({"type": "snapshot", "messages": messages, "region": current_region})
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ask":
                await handle_ask(str(data.get("text", "")), str(data.get("origin", "")))
    except WebSocketDisconnect:
        pass
    finally:
        sockets.discard(ws)


@app.websocket("/audio")
async def audio_ws(ws: WebSocket) -> None:
    """Speech from one page's mic: {start,id} -> PCM chunks -> {end,id}.

    Sends back {partial,id,text} while the person is talking and {final,id,text}
    once they stop. The page does the wake-word logic.
    """
    await ws.accept()

    async def announce_ready() -> None:
        await asyncio.to_thread(whisper_ready.wait)
        await ws.send_json({"type": "ready"})

    ready_task = asyncio.create_task(announce_ready())
    buf = bytearray()
    utt_id = 0
    active = False
    last_partial = 0
    partial_task: asyncio.Task | None = None

    async def send_partial(uid: int, pcm: bytes) -> None:
        text = await asyncio.to_thread(transcribe, pcm, True)
        if text:
            await ws.send_json({"type": "partial", "id": uid, "text": text})

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            chunk = msg.get("bytes")
            if chunk is not None:
                if not active:
                    continue
                buf += chunk
                if len(buf) - last_partial >= PARTIAL_EVERY_BYTES and (partial_task is None or partial_task.done()):
                    last_partial = len(buf)
                    partial_task = asyncio.create_task(send_partial(utt_id, bytes(buf)))
                continue
            data = json.loads(msg.get("text") or "{}")
            if data.get("type") == "start":
                utt_id, active, last_partial = int(data.get("id", 0)), True, 0
                buf = bytearray()
            elif data.get("type") == "end":
                active = False
                pcm, buf = bytes(buf), bytearray()
                text = ""
                if len(pcm) >= MIN_UTTERANCE_BYTES:
                    text = await asyncio.to_thread(transcribe, pcm, False) or ""
                await ws.send_json({"type": "final", "id": utt_id, "text": text})
    except WebSocketDisconnect:
        pass
    finally:
        ready_task.cancel()


if __name__ == "__main__":
    import uvicorn

    if not API_KEY:
        print("WARNING: GEMINI_API_KEY is not set in .env — answers will fail until it is.")
    port = int(os.getenv("PORT", "8080"))
    print(f"Model: {MODEL}\nSpeech: local Whisper {WHISPER_MODEL}\nCamera: {CAMERA_URL or '(disabled)'}")
    print(f"Open http://localhost:{port} in your browser.")
    uvicorn.run(app, host="0.0.0.0", port=port)
