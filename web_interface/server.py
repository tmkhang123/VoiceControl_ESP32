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
import serial
from flask import Flask, render_template, jsonify, send_file
from flask_socketio import SocketIO
from faster_whisper import WhisperModel
import requests

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
ESP32_IP    = "http://172.28.36.182"    # IP ESP32 trên hotspot iPhone
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

# Load Whisper model 1 lần lúc khởi động
print("[Whisper] Đang load model...")
whisper_model = WhisperModel("medium", device="cuda", compute_type="float16")
print("[Whisper] Model sẵn sàng!")

# Hàng đợi audio giữa serial_reader và VAD
audio_queue = queue.Queue()

# Tham số VAD
SPEECH_THRESHOLD    = 1200  # ngưỡng biên độ phát hiện tiếng
SILENCE_DURATION    = 0.8   # giây im lặng để kết thúc câu
MIN_SPEECH_DURATION = 0.3   # câu tối thiểu để xử lý

current_level = 0

# Recording state
is_recording     = False
recording_buffer = []
recording_lock   = threading.Lock()
last_wav_bytes   = None
recording_started_at = None


# ─── Serial reader thread ────────────────────────────────────

def serial_reader():
    buf = bytearray()
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1,
                                dsrdtr=False, rtscts=False)
            time.sleep(0.1)
            ser.reset_input_buffer()
            print(f"[Serial] Đã kết nối {SERIAL_PORT}")

            while True:
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    buf.extend(chunk)

                while True:
                    idx = buf.find(HEADER)
                    if idx == -1:
                        buf = buf[-1:]
                        break

                    packet_end = idx + 2 + CHUNK_SAMPLES * 2
                    if len(buf) < packet_end:
                        buf = buf[idx:]
                        break

                    data = buf[idx + 2 : packet_end]
                    buf = buf[packet_end:]

                    samples = list(struct.unpack(f'<{CHUNK_SAMPLES}h', data))

                    with recording_lock:
                        if is_recording:
                            recording_buffer.extend(samples)

                    socketio.emit("mic_batch", {"values": samples})
                    audio_queue.put(samples)

        except serial.SerialException as e:
            print(f"[Serial] Lỗi: {e}. Thử lại sau 3 giây…")
            socketio.emit("mic_batch", {"values": [0] * 16})
            buf = bytearray()
            time.sleep(3)


# ─── VAD + Whisper ───────────────────────────────────────────

def vad_and_transcribe():
    speech_buffer = []
    silence_samples = 0
    is_speaking = False
    silence_limit      = int(SILENCE_DURATION    * SAMPLE_RATE)
    min_speech_samples = int(MIN_SPEECH_DURATION * SAMPLE_RATE)

    while True:
        try:
            samples = audio_queue.get(timeout=1)
        except queue.Empty:
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
    text = " ".join(seg.text for seg in segments).strip()

    # Lọc hallucination: Whisper lặp initial_prompt khi audio mờ
    if "quạt mạnh, một" in text or text.count(",") > 5:
        print(f"[Whisper] BỎ QUA (hallucination): {text[:60]}...")
        return

    print(f"[Whisper] {text}")
    socketio.emit("transcription", {"text": text})
    parse_and_execute(text)


last_command_time = 0

def parse_and_execute(text):
    global current_level, last_command_time

    # Cooldown: bỏ qua nếu vừa thực hiện lệnh < 2 giây trước
    if time.time() - last_command_time < 2.0:
        return

    t = text.lower()
    if any(w in t for w in ["tắt quạt", "tắt đèn", "tắc"]):
        _call_esp32("/tat")
        current_level = 0
        socketio.emit("command", {"level": 0, "name": LEVEL_NAME[0]})
    elif any(w in t for w in ["số ba", "quạt ba", "quạt mạnh", "tốc độ ba"]) or "ba" in t.split():
        _call_esp32("/tat")
        for _ in range(3): _call_esp32("/tang")
        current_level = 3
        socketio.emit("command", {"level": 3, "name": LEVEL_NAME[3]})
    elif any(w in t for w in ["số hai", "quạt hai", "tốc độ hai"]) or "hai" in t.split():
        _call_esp32("/tat")
        for _ in range(2): _call_esp32("/tang")
        current_level = 2
        socketio.emit("command", {"level": 2, "name": LEVEL_NAME[2]})
    elif any(w in t for w in ["bật quạt", "bật đèn"]) or \
         any(w in t.split() for w in ["một", "máu", "mốt"]):
        _call_esp32("/tat")
        _call_esp32("/tang")
        current_level = 1
        socketio.emit("command", {"level": 1, "name": LEVEL_NAME[1]})
    elif any(w in t for w in ["tăng tốc", "tăng lên"]):
        _call_esp32("/tang")
        current_level = min(3, current_level + 1)
        socketio.emit("command", {"level": current_level, "name": LEVEL_NAME[current_level]})
    else:
        return  # Không match → không cập nhật cooldown

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
    except Exception:
        return False


# ─── Entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()
    t2 = threading.Thread(target=vad_and_transcribe, daemon=True)
    t2.start()
    print("[Server] Truy cap: http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
