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
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from faster_whisper import WhisperModel

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
# "auto" times the laptop at startup and picks the most accurate model that stays quick on it.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "auto").strip() or "auto"
WHISPER_THREADS = min(os.cpu_count() or 4, 8)
# Spoken answers: a Piper voice, downloaded once into voices/ (about 60 MB).
TTS_VOICE = os.getenv("TTS_VOICE", "en_US-lessac-medium").strip()
VOICES_DIR = BASE_DIR / "voices"
CAMERA_URL = os.getenv("CAMERA_STREAM_URL", "http://192.168.1.143:8000/stream").strip()
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
# HTTPS lets other devices use the mic and the Quest use AR. Set HTTPS_PORT=off to disable.
HTTPS_PORT = os.getenv("HTTPS_PORT", "8443").strip().lower()
CERT_DIR = BASE_DIR / "certs"
# The arm page is also served at /arm/ so the Quest can open it over the same HTTPS link.
ARM_DIR = Path(os.getenv("ARM_PAGE_DIR", str(BASE_DIR.parent / "vein-arm-guide"))).expanduser()

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
    threading.Thread(target=load_voice, daemon=True).start()
    yield


client = genai.Client(api_key=API_KEY) if API_KEY else None
app = FastAPI(title="Gemini Vein Assist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if (ARM_DIR / "index.html").exists():
    app.mount("/arm", StaticFiles(directory=ARM_DIR, html=True), name="arm")


def page_version() -> str:
    """Changes whenever a page file changes; open pages reload themselves when it does."""
    import hashlib
    digest = hashlib.sha1()
    for folder in (BASE_DIR / "static", ARM_DIR):
        for f in sorted(folder.glob("*")):
            if f.suffix in (".html", ".js", ".css"):
                digest.update(f.name.encode() + f.read_bytes())
    return digest.hexdigest()[:12]


PAGE_VERSION = page_version()


@app.middleware("http")
async def no_cache(request, call_next):
    # Make browsers check for updated page files on every load, so changes show up without a hard refresh.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response

# ---- session state (in memory: restarting the server starts a fresh session) ----
messages: list[dict] = []
sockets: set[WebSocket] = set()
current_task: asyncio.Task | None = None
current_region: dict | None = None  # {"site", "label", "reason"} shown on the arm page


# ---- local speech-to-text ----
whisper: WhisperModel | None = None
whisper_ready = threading.Event()
whisper_lock = threading.Lock()


whisper_name = WHISPER_MODEL
transcribe_secs = 0.5  # running average of how long a transcription takes on this computer

# Most accurate first. A model is kept if a 3-second test clip transcribes within this many seconds.
AUTO_MODELS = [("small.en", 1.0), ("base.en", 0.8), ("tiny.en", None)]


def benchmark(model: WhisperModel) -> float:
    clip = (np.random.default_rng(0).standard_normal(SAMPLE_RATE * 3) * 0.003).astype(np.float32)
    best = float("inf")
    for _ in range(2):  # the first run includes warm-up
        t = time.time()
        list(model.transcribe(clip, language="en", beam_size=1, vad_filter=False,
                              without_timestamps=True, condition_on_previous_text=False)[0])
        best = min(best, time.time() - t)
    return best


def load_whisper() -> None:
    global whisper, whisper_name, transcribe_secs
    t = time.time()
    if WHISPER_MODEL != "auto":
        whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=WHISPER_THREADS)
    else:
        for name, limit in AUTO_MODELS:
            model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=WHISPER_THREADS)
            secs = benchmark(model)
            print(f"[speech] {name}: test clip took {secs:.2f}s")
            whisper, whisper_name, transcribe_secs = model, name, secs
            if limit is None or secs <= limit:
                break
            print(f"[speech] {name} is too slow on this computer, trying a smaller model")
    whisper_ready.set()
    print(f"[speech] Whisper {whisper_name} ready in {time.time() - t:.1f}s ({WHISPER_THREADS} threads)")


def transcribe(pcm: bytes, partial: bool) -> str | None:
    """16 kHz mono int16 PCM -> text. Partials are skipped (None) if Whisper is busy."""
    if not whisper_ready.is_set():
        return None
    if not whisper_lock.acquire(blocking=not partial):
        return None
    global transcribe_secs
    started = time.time()
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
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        took = time.time() - started
        transcribe_secs = 0.7 * transcribe_secs + 0.3 * took
        if not partial:
            print(f"[speech] {len(pcm) / (SAMPLE_RATE * 2):.1f}s of speech -> text in {took:.2f}s")
        return text
    finally:
        whisper_lock.release()


# ---- spoken answers ----
tts_voice = None
tts_lock = threading.Lock()


def load_voice() -> None:
    """Load the Piper voice, downloading it the first time. Without it, pages use the browser's voice."""
    global tts_voice
    try:
        from piper import PiperVoice

        model = VOICES_DIR / f"{TTS_VOICE}.onnx"
        if not model.exists():
            lang, speaker, quality = TTS_VOICE.split("-")  # e.g. en_US-lessac-medium
            base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{TTS_VOICE}"
            VOICES_DIR.mkdir(exist_ok=True)
            print(f"[voice] downloading {TTS_VOICE} (one time, about 60 MB)...")
            for suffix in (".onnx.json", ".onnx"):
                target = VOICES_DIR / f"{TTS_VOICE}{suffix}"
                part = target.with_name(target.name + ".part")
                with httpx.stream("GET", base + suffix, follow_redirects=True, timeout=120) as resp:
                    resp.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
                part.replace(target)
        t = time.time()
        tts_voice = PiperVoice.load(str(model))
        print(f"[voice] {TTS_VOICE} ready in {time.time() - t:.1f}s")
    except Exception as exc:
        print(f"[voice] server voice unavailable ({exc!r}); devices will use their browser's voice")


def synthesize(text: str) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with tts_lock, wave.open(buf, "wb") as wav:
        tts_voice.synthesize_wav(text, wav)
    return buf.getvalue()


async def broadcast(event: dict) -> None:
    dead = []
    for ws in list(sockets):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sockets.discard(ws)


camera_retry_at = 0.0  # after a failed grab, questions skip the camera until this time
CAMERA_RETRY_SECS = 20


async def grab_frame(timeout: float = 2.0) -> bytes | None:
    """Pull one complete JPEG out of the MJPEG stream, or None if unavailable.

    If the camera can't be reached, later questions skip it for CAMERA_RETRY_SECS instead of
    each waiting for a timeout (on another network, the default camera address doesn't exist).
    """
    global camera_retry_at
    if not CAMERA_URL or time.time() < camera_retry_at:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=1.0)) as http:
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
        print(f"[camera] can't reach {CAMERA_URL} ({exc.__class__.__name__}); answering without it for {CAMERA_RETRY_SECS}s")
    camera_retry_at = time.time() + CAMERA_RETRY_SECS
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

        started = time.time()
        frame = await grab_frame()
        camera_secs = time.time() - started
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
        first_words = None
        async for chunk in stream:
            text = chunk.text or ""
            if text and first_words is None:
                first_words = time.time() - started
            if text:
                reply["text"] += text
                await broadcast({"type": "delta", "id": reply["id"], "text": text})
        print(f"[timing] camera {camera_secs:.2f}s · first words {first_words or 0:.2f}s · "
              f"full answer {time.time() - started:.2f}s ({MODEL})")
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
    await ws.send_json({"type": "snapshot", "messages": messages, "region": current_region, "version": PAGE_VERSION})
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ask":
                await handle_ask(str(data.get("text", "")), str(data.get("origin", "")))
    except WebSocketDisconnect:
        pass
    finally:
        sockets.discard(ws)


@app.post("/api/tts")
async def tts(request: Request) -> Response:
    """One sentence of an answer -> WAV audio, so every device speaks with the same voice."""
    text = str((await request.json()).get("text", "")).strip()[:600]
    if tts_voice is None or not text:
        return Response(status_code=503 if tts_voice is None else 400)
    audio = await asyncio.to_thread(synthesize, text)
    return Response(audio, media_type="audio/wav")


@app.get("/camera/stream")
async def camera_stream() -> Response:
    """The vein camera's live MJPEG feed, passed through this server so https pages (the Quest's AR view)
    can show it: browsers block an https page from loading the camera's plain-http stream directly."""
    if not CAMERA_URL:
        return Response("No camera configured (CAMERA_STREAM_URL)", status_code=404)
    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=2.0))
    try:
        upstream = await http.send(http.build_request("GET", CAMERA_URL), stream=True)
        upstream.raise_for_status()
    except Exception as exc:
        await http.aclose()
        return Response(f"Camera not reachable at {CAMERA_URL} ({exc.__class__.__name__})", status_code=503)

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        except Exception:
            pass  # camera stopped or the viewer left
        finally:
            await upstream.aclose()
            await http.aclose()

    media_type = upstream.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame")
    return StreamingResponse(relay(), media_type=media_type)


@app.post("/api/ar-log")
async def ar_log(request: Request) -> dict:
    """AR events from a headset (hands found/lost, pauses, pinches), printed so problems can be traced."""
    data = await request.json()
    who = request.client.host if request.client else "?"
    for line in data.get("lines", [])[:50]:
        print(f"[ar {who}] {str(line)[:200]}")
    return {}


@app.post("/api/mic-check")
async def mic_check(request: Request) -> dict:
    """A short clip from the browser's own recorder, sent when the live mic is silent: is there sound in it?"""
    data = await request.body()
    who = request.client.host if request.client else "?"

    def measure() -> dict:
        import io

        import av

        parts, rate = [], 0
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.audio[0]
            rate = stream.rate
            for frame in container.decode(stream):
                arr = frame.to_ndarray()
                if arr.dtype.kind in "iu":
                    arr = arr.astype(np.float32) / np.iinfo(arr.dtype).max
                parts.append(arr.astype(np.float32).ravel())
        samples = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        level = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        return {"rate": rate, "level": round(level, 4), "peak": round(peak, 4)}

    try:
        info = await asyncio.to_thread(measure)
        verdict = "HAS SOUND: the browser's audio engine is where it's lost" if info["peak"] > 0 else "SILENT too: the headset gives the browser no mic sound"
        print(f"[mic check {who}] recorder clip at {info['rate']} Hz: level {info['level']} (peak {info['peak']}) -> {verdict}")
    except Exception as exc:
        info = {"error": repr(exc)}
        print(f"[mic check {who}] couldn't read the recorder clip ({len(data)} bytes): {exc!r}")
    return info


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
    who = ws.client.host if ws.client else "?"
    reports = 0
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
                every = max(PARTIAL_EVERY_BYTES, int(2 * transcribe_secs * SAMPLE_RATE * 2))
                if len(buf) - last_partial >= every and (partial_task is None or partial_task.done()):
                    last_partial = len(buf)
                    partial_task = asyncio.create_task(send_partial(utt_id, bytes(buf)))
                continue
            data = json.loads(msg.get("text") or "{}")
            if data.get("type") == "stats":
                # The page's mic report: logged for the first few reports, then every 10th.
                reports += 1
                if reports <= 4 or reports % 10 == 0:
                    mic = data.get("mic") or {}
                    print(f"[mic {who}] {data.get('sampleRate')} Hz, audio {data.get('audio')}, "
                          f"{data.get('frames')} frames/3s, level {data.get('level')} (peak {data.get('peak')}), "
                          f"speech threshold {data.get('threshold')}, speech starts {data.get('speechStarts')}, "
                          f"engine peak {data.get('enginePeak')}, mode {data.get('mode')}, mic \"{mic.get('label', '?')}\" "
                          f"at {mic.get('rate')} Hz (muted {mic.get('muted')}, enabled {mic.get('enabled')}, {mic.get('state')})")
            elif data.get("type") == "start":
                utt_id, active, last_partial = int(data.get("id", 0)), True, 0
                buf = bytearray()
            elif data.get("type") == "end":
                active = False
                pcm, buf = bytes(buf), bytearray()
                text = ""
                if len(pcm) >= MIN_UTTERANCE_BYTES:
                    text = await asyncio.to_thread(transcribe, pcm, False) or ""
                else:
                    print(f"[speech] {who}: ignored a {len(pcm) / (SAMPLE_RATE * 2):.2f}s blip")
                await ws.send_json({"type": "final", "id": utt_id, "text": text})
    except WebSocketDisconnect:
        pass
    finally:
        ready_task.cancel()


def network_ip() -> str | None:
    """This computer's address on the local network (what other devices should open)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # picks the outgoing interface; nothing is sent
            return sock.getsockname()[0]
    except OSError:
        return None


def ensure_certificate(ip: str | None) -> tuple[Path, Path]:
    """A self-signed certificate for this computer, made once and remade when its IP changes."""
    import datetime
    import ipaddress
    import socket

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    ips = {"127.0.0.1"} | ({ip} if ip else set())
    now = datetime.datetime.now(datetime.timezone.utc)
    if cert_path.exists() and key_path.exists():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covered = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
            if ips <= covered and cert.not_valid_after_utc > now + datetime.timedelta(days=30):
                return cert_path, key_path
        except Exception:
            pass  # unreadable or old: make a new one

    host = socket.gethostname()
    names = {"localhost", host, host.split(".")[0] + ".local"}
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vein Assist (this computer)")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(n) for n in sorted(names)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]
        ), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    CERT_DIR.mkdir(exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[https] made a new certificate for {', '.join(sorted(ips))}")
    return cert_path, key_path


async def serve(port: int, https_port: int | None, cert: tuple[Path, Path] | None) -> None:
    import uvicorn

    servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port))]
    if https_port and cert:
        servers.append(uvicorn.Server(uvicorn.Config(
            app, host="0.0.0.0", port=https_port, ssl_certfile=str(cert[0]), ssl_keyfile=str(cert[1]),
            lifespan="off",  # the http server already runs startup (loading Whisper)
        )))
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:  # Ctrl+C (or one failing) stops both
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True


if __name__ == "__main__":
    if not API_KEY:
        print("WARNING: GEMINI_API_KEY is not set in .env — answers will fail until it is.")
    port = int(os.getenv("PORT", "8080"))
    https_port = None if HTTPS_PORT in ("", "0", "off", "no", "false") else int(HTTPS_PORT)
    ip = network_ip()
    cert = None
    if https_port:
        try:
            cert = ensure_certificate(ip)
        except Exception as exc:
            print(f"[https] couldn't set up HTTPS ({exc!r}); running http only")
    print(f"Model: {MODEL}\nSpeech: local Whisper ({WHISPER_MODEL})\nCamera: {CAMERA_URL or '(disabled)'}")
    print(f"Open http://localhost:{port} in your browser.")
    if ip:
        print(f"Other devices on this network: http://{ip}:{port}  (view the chat; the mic works on this computer)")
    if cert:
        host = ip or "localhost"
        print(f"One link for everything (mic on any device; on the Quest, View in AR shows the live camera, chat, mic and arm):")
        print(f"  https://{host}:{https_port}   (first visit on each device: tap Advanced -> Proceed)")
        if (ARM_DIR / "index.html").exists():
            print(f"  Arm only: https://{host}:{https_port}/arm/")
    busy = [p for p in (port, https_port if cert else None) if p and port_in_use(p)]
    if busy:
        ports = " and ".join(str(p) for p in busy)
        print(f"\nPort {ports} is already in use: Vein Assist is probably still running in another window.")
        print("Close that window or press Ctrl+C in it, then run this again.")
        print(f"To see what's using it: lsof -i :{busy[0]}   (Windows: netstat -ano | findstr :{busy[0]})")
        raise SystemExit(1)
    asyncio.run(serve(port, https_port, cert))
