"""
server.py — Web Interface cho Quạt Thông Minh ESP32
Khởi chạy: python server.py
Mở trình duyệt: http://localhost:5000
"""

import os
import sys

# Thêm đường dẫn DLL CUDA vào PATH cho Windows
_nvidia_base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
if os.path.isdir(_nvidia_base):
    for _pkg in os.listdir(_nvidia_base):
        _dll_path = os.path.join(_nvidia_base, _pkg, "bin")
        if os.path.isdir(_dll_path):
            os.environ["PATH"] = _dll_path + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(_dll_path)

import threading
import queue
import time
import wave
import io
import struct
import asyncio
import websockets
from flask import Flask, render_template, jsonify, send_file
from flask_socketio import SocketIO
from faster_whisper import WhisperModel
import requests
import numpy as np
import torch
import json
from scipy.signal import resample_poly
from df import enhance, init_df
from transformers import AutoTokenizer, AutoModelForSequenceClassification

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


@app.after_request
def add_no_cache_headers(resp):
    # Prevent browser/proxy from reusing old HTML/JS during live tuning.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# =============================================================
#  CẤU HÌNH — THAY ĐỔI CHO PHÙ HỢP VỚI MÁY 
# =============================================================
ESP32_IP    = "http://YOUR_ESP32_IP"   # IP ESP32 (xem Serial Monitor sau khi upload)
SERIAL_PORT = "COM5"                 # Cổng COM của ESP32 (auto-detected khi upload)
BAUD_RATE     = 921600
CHUNK_SAMPLES = 512
HEADER        = bytes([0xAA, 0x55])
# Fallback sample rate if timing data is unavailable.
SAMPLE_RATE = 16000
# =============================================================

COLOR_MAP = {
    0: "#333333",
    1: "#ff4444",
    2: "#ffcc00",
    3: "#44ff44",
}
LEVEL_NAME = {
    0: "TẮT",
    1: "Số 1 — Chậm",
    2: "Số 2 — Vừa",
    3: "Số 3 — Nhanh",
}

# Load DeepFilterNet model 1 lần lúc khởi động
print("[DeepFilter] Đang load model...")
df_model, df_state, _ = init_df()
DF_SR = df_state.sr()  # 48000
print(f"[DeepFilter] Model sẵn sàng! (sr={DF_SR})")

# Load PhoBERT intent model
PHOBERT_DIR = os.path.join(os.path.dirname(__file__), "..", "nlp", "phobert_intent")
print("[PhoBERT] Đang load model...")
phobert_tokenizer = AutoTokenizer.from_pretrained(PHOBERT_DIR)
phobert_model     = AutoModelForSequenceClassification.from_pretrained(PHOBERT_DIR)
phobert_model.eval()
with open(os.path.join(PHOBERT_DIR, "label_map.json"), encoding="utf-8") as f:
    _label_map = json.load(f)
    INTENT_LABELS = {int(k): v for k, v in _label_map.items()}
print("[PhoBERT] Model sẵn sàng!")

# Load Whisper model 1 lần lúc khởi động
print("[Whisper] Đang load model...")
whisper_model = WhisperModel("medium", device="cuda", compute_type="float16")
print("[Whisper] Model sẵn sàng!")

# Hàng đợi audio giữa serial_reader và VAD
audio_queue = queue.Queue()

# Tham số VAD
SPEECH_THRESHOLD    = 500   # ngưỡng biên độ phát hiện tiếng (ESP32 đã pre-filter ở 800)
SILENCE_DURATION    = 0.8   # giây im lặng để kết thúc câu
MIN_SPEECH_DURATION = 0.3   # câu tối thiểu để xử lý

current_level = 0

# Recording state
is_recording     = False
recording_buffer = []
recording_lock   = threading.Lock()
last_wav_bytes   = None
recording_started_at = None


# ─── WebSocket audio receiver (nhận audio từ ESP32) ──────────

async def esp32_audio_handler(websocket):
    print(f"[AudioWS] ESP32 kết nối: {websocket.remote_address}")
    try:
        async for message in websocket:
            if not isinstance(message, bytes):
                continue
            if len(message) != CHUNK_SAMPLES * 2:
                continue

            samples = list(struct.unpack(f'<{CHUNK_SAMPLES}h', message))

            with recording_lock:
                if is_recording:
                    recording_buffer.extend(samples)

            socketio.emit("mic_batch", {"values": samples})
            audio_queue.put(samples)
    except websockets.exceptions.ConnectionClosed:
        print("[AudioWS] ESP32 ngắt kết nối")

async def _audio_ws_main():
    async with websockets.serve(esp32_audio_handler, "0.0.0.0", 5001):
        print("[AudioWS] Đang lắng nghe ESP32 tại port 5001...")
        await asyncio.Future()

def run_audio_ws():
    asyncio.run(_audio_ws_main())


# ─── VAD + Whisper ───────────────────────────────────────────

def vad_and_transcribe():
    speech_buffer = []
    silence_samples = 0
    is_speaking = False
    silence_limit      = int(SILENCE_DURATION    * SAMPLE_RATE)
    min_speech_samples = int(MIN_SPEECH_DURATION * SAMPLE_RATE)

    while True:
        try:
            samples = audio_queue.get(timeout=0.5)
        except queue.Empty:
            # Không nhận packet trong 0.5s = ESP32 VAD đã dừng gửi = im lặng
            if is_speaking:
                if len(speech_buffer) >= min_speech_samples:
                    threading.Thread(
                        target=process_speech,
                        args=(speech_buffer.copy(),),
                        daemon=True
                    ).start()
                speech_buffer.clear()
                silence_samples = 0
                is_speaking = False
            continue

        avg_amp = sum(abs(s) for s in samples) / len(samples)

        if avg_amp > SPEECH_THRESHOLD:
            if not is_speaking:
                is_speaking = True
                print("[VAD] Phat hien giong noi...")
            speech_buffer.extend(samples)
            silence_samples = 0
        else:
            if is_speaking:
                speech_buffer.extend(samples)
                silence_samples += len(samples)
                if silence_samples >= silence_limit:
                    if len(speech_buffer) >= min_speech_samples:
                        threading.Thread(
                            target=process_speech,
                            args=(speech_buffer.copy(),),
                            daemon=True
                        ).start()
                    speech_buffer.clear()
                    silence_samples = 0
                    is_speaking = False


def process_speech(samples):
    # Speech Enhancement với DeepFilterNet
    audio = np.array(samples, dtype=np.float32) / 32768.0
    if DF_SR != SAMPLE_RATE:
        audio = resample_poly(audio, DF_SR // SAMPLE_RATE, 1)
    audio_tensor = torch.from_numpy(audio[np.newaxis, :].copy())
    enhanced = enhance(df_model, df_state, audio_tensor)
    audio_out = enhanced[0].numpy()
    if DF_SR != SAMPLE_RATE:
        audio_out = resample_poly(audio_out, 1, DF_SR // SAMPLE_RATE)
    samples = (audio_out * 32768.0).clip(-32768, 32767).astype(np.int16).tolist()

    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    wav_io.seek(0)

    segments, _ = whisper_model.transcribe(
        wav_io,
        language="vi",
        beam_size=5,
        temperature=0,
        initial_prompt="bật quạt, tắt quạt, tăng tốc, quạt số một, quạt số hai, quạt số ba, quạt mạnh, một, hai, ba",
        no_speech_threshold=0.6,
        condition_on_previous_text=False,
        compression_ratio_threshold=1.8
    )
    segments = [seg for seg in segments if seg.no_speech_prob < 0.6]
    if not segments:
        print("[Whisper] BỎ QUA (no_speech_prob cao)")
        return
    text = " ".join(seg.text for seg in segments).strip()

    # Lọc hallucination: Whisper lặp initial_prompt khi audio mờ
    if "quạt mạnh, một" in text or text.count(",") > 5:
        print(f"[Whisper] BỎ QUA (hallucination): {text[:60]}...")
        return

    print(f"[Whisper] {text}")
    socketio.emit("transcription", {"text": text})
    parse_and_execute(text)


last_command_time = 0

def predict_intent(text: str) -> str:
    inputs = phobert_tokenizer(text, return_tensors="pt", truncation=True, max_length=64)
    with torch.no_grad():
        logits = phobert_model(**inputs).logits
    probs = torch.softmax(logits, dim=-1)
    confidence, idx = probs.max(dim=-1)
    return INTENT_LABELS[idx.item()], confidence.item()

def parse_and_execute(text):
    global current_level, last_command_time

    if time.time() - last_command_time < 2.0:
        return

    KEYWORDS = ["quạt", "bật", "tắt", "số", "tăng", "giảm", "mạnh",
                "nhẹ", "nóng", "lạnh", "một", "hai", "ba", "cấp", "vừa"]
    has_keyword = any(w in text.lower() for w in KEYWORDS)

    intent, confidence = predict_intent(text)
    print(f"[PhoBERT] intent: {intent} ({confidence:.2f})")

    if not has_keyword and confidence < 0.5:
        print(f"[PhoBERT] BỎ QUA (không có từ khóa, confidence: {confidence:.2f})")
        return
    if confidence < 0.22:
        print(f"[PhoBERT] BỎ QUA (confidence quá thấp: {confidence:.2f})")
        return

    if intent == "TURN_OFF":
        _call_esp32("/tat")
        current_level = 0
        socketio.emit("command", {"level": 0, "name": LEVEL_NAME[0]})
    elif intent == "TURN_ON" or intent == "LEVEL_1":
        _call_esp32("/tat")
        _call_esp32("/tang")
        current_level = 1
        socketio.emit("command", {"level": 1, "name": LEVEL_NAME[1]})
    elif intent == "LEVEL_2":
        _call_esp32("/tat")
        for _ in range(2): _call_esp32("/tang")
        current_level = 2
        socketio.emit("command", {"level": 2, "name": LEVEL_NAME[2]})
    elif intent == "LEVEL_3":
        _call_esp32("/tat")
        for _ in range(3): _call_esp32("/tang")
        current_level = 3
        socketio.emit("command", {"level": 3, "name": LEVEL_NAME[3]})
    elif intent == "INCREASE":
        if current_level < 3:
            _call_esp32("/tang")
            current_level += 1
            socketio.emit("command", {"level": current_level, "name": LEVEL_NAME[current_level]})
    elif intent == "DECREASE":
        if current_level > 1:
            _call_esp32("/tat")
            for _ in range(current_level - 1): _call_esp32("/tang")
            current_level -= 1
            socketio.emit("command", {"level": current_level, "name": LEVEL_NAME[current_level]})
        elif current_level == 1:
            _call_esp32("/tat")
            current_level = 0
            socketio.emit("command", {"level": 0, "name": LEVEL_NAME[0]})
    else:
        return

    last_command_time = time.time()


# ─── Routes ──────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "level": current_level,
        "color": COLOR_MAP[current_level],
        "name":  LEVEL_NAME[current_level],
    })


@app.route("/api/tang", methods=["POST"])
def api_tang():
    global current_level
    esp32_ok = _call_esp32("/tang")
    current_level = (current_level + 1) % 4
    return jsonify({
        "level": current_level,
        "color": COLOR_MAP[current_level],
        "name":  LEVEL_NAME[current_level],
        "esp32_ok": esp32_ok,
    })


@app.route("/api/tat", methods=["POST"])
def api_tat():
    global current_level
    esp32_ok = _call_esp32("/tat")
    current_level = 0
    return jsonify({
        "level": 0,
        "color": COLOR_MAP[0],
        "name":  LEVEL_NAME[0],
        "esp32_ok": esp32_ok,
    })


@app.route("/api/set/<int:level>", methods=["POST"])
def api_set_level(level):
    global current_level
    level = max(0, min(3, level))
    _call_esp32("/tat")
    for _ in range(level):
        _call_esp32("/tang")
    current_level = level
    socketio.emit("command", {"level": current_level, "name": LEVEL_NAME[current_level]})
    return jsonify({"level": current_level, "color": COLOR_MAP[current_level], "name": LEVEL_NAME[current_level]})


@app.route("/api/giam", methods=["POST"])
def api_giam():
    global current_level
    if current_level > 0:
        current_level -= 1
        _call_esp32("/tat")
        for _ in range(current_level):
            _call_esp32("/tang")
    socketio.emit("command", {"level": current_level, "name": LEVEL_NAME[current_level]})
    return jsonify({"level": current_level, "color": COLOR_MAP[current_level], "name": LEVEL_NAME[current_level]})


@app.route("/api/record/start", methods=["POST"])
def api_record_start():
    global is_recording, recording_started_at
    with recording_lock:
        recording_buffer.clear()
    recording_started_at = time.monotonic()
    is_recording = True
    return jsonify({"ok": True})


@app.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    global is_recording, last_wav_bytes, recording_started_at
    is_recording = False
    with recording_lock:
        buf = list(recording_buffer)
    if not buf:
        return jsonify({"ok": False, "error": "Không có dữ liệu"}), 400

    elapsed = None
    if recording_started_at is not None:
        elapsed = max(0.001, time.monotonic() - recording_started_at)
    recording_started_at = None

    # Determine actual capture rate from sample count / elapsed seconds.
    if elapsed is not None and elapsed > 0:
        measured_rate = int(round(len(buf) / elapsed))
        wav_rate = max(200, min(48000, measured_rate))
    else:
        wav_rate = SAMPLE_RATE

    samples16 = buf  # đã là int16 từ ESP32
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(wav_rate)
        wf.writeframes(struct.pack(f"<{len(samples16)}h", *samples16))
    last_wav_bytes = wav_io.getvalue()
    duration = round(len(buf) / wav_rate, 2)
    return jsonify({"ok": True, "samples": len(buf), "duration": duration, "sample_rate": wav_rate})


@app.route("/api/record/audio")
def api_record_audio():
    if last_wav_bytes is None:
        return jsonify({"error": "Chưa có bản ghi nào"}), 404
    return send_file(io.BytesIO(last_wav_bytes), mimetype="audio/wav")


def _call_esp32(path: str) -> bool:
    """Gửi GET request đến ESP32. Trả về True nếu thành công."""
    try:
        requests.get(f"{ESP32_IP}{path}", timeout=3)
        return True
    except Exception as e:
        print(f"[ESP32] Lỗi gọi {path}: {e}")
        return False


# ─── Entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=run_audio_ws, daemon=True).start()
    threading.Thread(target=vad_and_transcribe, daemon=True).start()
    print("[Server] Truy cap: http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
