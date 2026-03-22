# Workflow Documentation — Voice Control ESP32
## Hệ thống Điều khiển Quạt bằng Giọng nói Tiếng Việt

---

## 1. Tổng quan hệ thống

```
┌─────────────────────────────────────────────────────────────────┐
│                        NGƯỜI DÙNG                               │
│                   Nói: "bật quạt"                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ESP32-S3 (Hardware)                           │
│  Mic INMP441 → I2S Driver → Binary Stream → USB Serial 921600  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ USB Cable
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Python Server (Laptop)                         │
│  Serial Reader → VAD → faster-whisper → NLP → HTTP Command     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WiFi (HTTP)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ESP32-S3 (Hardware)                           │
│              Nhận lệnh → LED + Quạt thay đổi                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Luồng dữ liệu chi tiết

### Giai đoạn 1: Thu âm (ESP32)

**Dữ liệu đầu vào:** Sóng âm thanh vật lý từ không khí

**Xử lý:**
```
Sóng âm
  → INMP441 chuyển đổi → tín hiệu điện kỹ thuật số I2S
  → ESP32 đọc qua DMA buffer (64 mẫu/lần)
  → Gom vào sendBuf (512 mẫu)
  → Chuyển đổi: int32 (32-bit) >> 16 → int16 (16-bit)
  → Thêm header [0xAA, 0x55]
  → Gửi qua Serial USB tốc độ 921600 baud
```

**Thông số:**
| Thông số | Giá trị | Lý do |
|---|---|---|
| Sample Rate | 16000 Hz | Chuẩn tối thiểu cho nhận dạng giọng nói |
| Bit depth gốc | 32-bit | ESP32 I2S đọc 32-bit từ INMP441 |
| Bit depth gửi | 16-bit | Tiết kiệm băng thông, đủ cho Whisper |
| Baud rate | 921600 | ~92KB/s — đủ cho 16000 mẫu/giây × 2 bytes |
| Kích thước gói | 512 mẫu = 1026 bytes | Cân bằng giữa độ trễ và hiệu quả |

**Dạng dữ liệu gửi lên:** Raw PCM Binary
```
[0xAA][0x55][s0_lo][s0_hi][s1_lo][s1_hi]...[s511_lo][s511_hi]
  header(2B)        512 mẫu × 2 bytes = 1024 bytes
```

---

### Giai đoạn 2: Nhận và giải mã Serial (Python)

**Dữ liệu đầu vào:** Raw bytes từ cổng COM

**Xử lý:**
```python
# Tích lũy bytes vào buffer
buf.extend(chunk)

# Tìm header để căn chỉnh gói
idx = buf.find(bytes([0xAA, 0x55]))

# Giải mã binary → list số nguyên
samples = struct.unpack('<512h', data)
# '<' = little-endian (Intel/ESP32)
# 'h' = signed short = int16 = [-32768, 32767]
```

**Dạng dữ liệu sau bước này:** `List[int]` — 512 số nguyên 16-bit, ví dụ:
```
[234, -156, 1024, -2048, 512, ...]
```

**Phân phối song song:**
- → `audio_queue` (cho VAD)
- → `socketio.emit("mic_batch")` (vẽ sóng âm lên web)
- → `recording_buffer` (nếu đang thu âm thủ công)

---

### Giai đoạn 3: VAD — Voice Activity Detection (Python)

**Mục đích:** Phát hiện khi nào có tiếng người nói để cắt câu, tránh gửi cả giờ audio liên tục vào Whisper.

**Thuật toán:** Energy-based VAD (tự xây dựng, không dùng thư viện)

```python
avg_amplitude = sum(abs(s) for s in samples) / len(samples)
```

**Trạng thái máy:**
```
┌─────────┐  avg > 1200      ┌──────────┐
│  Im lặng│ ──────────────→  │Đang nói  │
│         │                  │          │
│         │ ←────────────── │          │
└─────────┘  im lặng > 0.8s  └──────────┘
                                  │
                                  │ im lặng > 0.8s
                                  │ VÀ speech >= 0.3s
                                  ▼
                          Gửi audio → Whisper
```

**Các ngưỡng:**
| Tham số | Giá trị | Ý nghĩa |
|---|---|---|
| `SPEECH_THRESHOLD` | 1200 | Biên độ tối thiểu để coi là có tiếng người |
| `SILENCE_DURATION` | 0.8s = 12800 mẫu | Im lặng liên tiếp đủ dài → câu kết thúc |
| `MIN_SPEECH_DURATION` | 0.3s = 4800 mẫu | Câu quá ngắn → bỏ qua (tránh tiếng click) |

**Dạng dữ liệu đầu ra:** `List[int]` — toàn bộ mẫu của 1 câu nói (thường 8000–48000 mẫu = 0.5–3 giây)

---

### Giai đoạn 4: Đóng gói WAV (Python)

Whisper yêu cầu file audio có header WAV chuẩn, không nhận raw PCM.

```python
wav_io = io.BytesIO()          # File ảo trong RAM (không ghi ra ổ cứng)
with wave.open(wav_io, "wb") as wf:
    wf.setnchannels(1)         # Mono
    wf.setsampwidth(2)         # 16-bit = 2 bytes/mẫu
    wf.setframerate(16000)     # 16000 Hz
    wf.writeframes(pcm_bytes)  # Ghi raw PCM vào body WAV
wav_io.seek(0)                 # Quay về đầu để Whisper đọc
```

**Dạng dữ liệu:** WAV in-memory (BytesIO object)
- Header: 44 bytes chuẩn WAV
- Body: raw PCM 16-bit little-endian
- **Không qua bất kỳ tiền xử lý nào** (không normalize, không filter)

---

### Giai đoạn 5: Nhận dạng giọng nói — faster-whisper

**Model:** `medium` — cân bằng giữa tốc độ và độ chính xác tiếng Việt
**Device:** CUDA (GPU RTX 4060) — xử lý nhanh gần real-time
**Compute type:** `float16` — dùng half-precision để tăng tốc trên GPU

#### Tham số transcribe và ý nghĩa:

```python
segments, _ = whisper_model.transcribe(
    wav_io,
    language="vi",
    beam_size=5,
    temperature=0,
    initial_prompt="bật quạt, tắt quạt, tăng tốc, quạt số một, ...",
    no_speech_threshold=0.6,
    condition_on_previous_text=False,
    compression_ratio_threshold=1.8
)
```

| Tham số | Giá trị | Ý nghĩa chi tiết |
|---|---|---|
| `language` | `"vi"` | Chỉ định tiếng Việt, không cần auto-detect → nhanh hơn và chính xác hơn |
| `beam_size` | `5` | Tìm kiếm 5 khả năng song song, chọn kết quả tốt nhất. beam=1 nhanh nhất nhưng kém chính xác |
| `temperature` | `0` | Kết quả deterministic (ổn định). temperature > 0 → ngẫu nhiên hơn, đa dạng hơn nhưng dễ sai |
| `initial_prompt` | Chuỗi từ khóa | Gợi ý từ vựng cho Whisper. Không phải lệnh — chỉ là "từ điển ngữ cảnh" giúp Whisper nhận dạng đúng từ chuyên ngành |
| `no_speech_threshold` | `0.6` | Whisper tự chấm điểm xác suất "có tiếng người". Nếu xác suất không có tiếng > 60% → trả về chuỗi rỗng |
| `condition_on_previous_text` | `False` | Tắt memory câu trước. Mỗi câu được nhận dạng độc lập, tránh bị ảnh hưởng bởi lệnh vừa nói |
| `compression_ratio_threshold` | `1.8` | Nếu text có tỉ lệ lặp > 1.8 → model đang hallucinate → bỏ kết quả |

#### Dữ liệu vào Whisper: Có thô hay qua xử lý?

**Dữ liệu vào Whisper là dữ liệu CHƯA QUA XỬ LÝ âm thanh** (không normalize, không denoise).

Tuy nhiên đã qua 2 bước tiền lọc:
1. **VAD lọc** — chỉ gửi đoạn có tiếng người, không gửi im lặng
2. **Cắt đúng câu** — gửi từng câu hoàn chỉnh thay vì stream liên tục

Whisper xử lý nội bộ: tự normalize + chuyển về Mel spectrogram trước khi đưa vào encoder.

**Dạng dữ liệu đầu ra Whisper:** `List[Segment]`
```python
text = " ".join(seg.text for seg in segments).strip()
# Ví dụ: "bật quạt"
```

---

### Giai đoạn 6: Lọc Hallucination (Python)

Sau khi Whisper trả về text, áp thêm 1 lớp lọc thủ công:

```python
if "quạt mạnh, một" in text or text.count(",") > 5:
    return  # Bỏ qua — đây là hallucination
```

**Lý do:** Khi audio mờ nhạt (tiếng ồn), Whisper "bịa" bằng cách lặp lại `initial_prompt`. Pattern này luôn có nhiều dấu phẩy liên tiếp — dễ phát hiện.

---

### Giai đoạn 7: NLP — Phân tích lệnh (Python)

**Phương pháp hiện tại:** Keyword Matching (so khớp từ khóa)

```python
t = text.lower()
if any(w in t for w in ["tắt quạt", "tắt đèn", "tắc"]):  → cấp 0
elif "ba" in t.split():                                    → cấp 3
elif "hai" in t.split():                                   → cấp 2
elif any(w in t for w in ["bật quạt", "bật đèn"]):        → cấp 1
elif any(w in t for w in ["tăng tốc", "tăng lên"]):       → tăng 1 cấp
```

**Cooldown 2 giây:** Sau mỗi lệnh thực hiện, bỏ qua tất cả input trong 2 giây tiếp theo → tránh lệnh liên tiếp do VAD kích hoạt nhiều lần.

**Bảng mapping lệnh:**
| Câu nói | Từ khóa khớp | Lệnh ESP32 | Kết quả |
|---|---|---|---|
| "bật quạt" / "bật đèn" | bật quạt, bật đèn | /tat + /tang | Cấp 1 — LED đỏ |
| "quạt số hai" / "hai" | hai | /tat + /tang×2 | Cấp 2 — LED vàng |
| "quạt mạnh" / "ba" | ba, quạt mạnh | /tat + /tang×3 | Cấp 3 — LED xanh |
| "tắt quạt" / "tắt đèn" / "tắc" | tắt quạt, tắt đèn, tắc | /tat | Cấp 0 — tắt |
| "tăng tốc" / "tăng lên" | tăng tốc, tăng lên | /tang | +1 cấp |

---

### Giai đoạn 8: Gửi lệnh về ESP32 (HTTP)

```python
requests.get("http://192.168.x.x/tang", timeout=3)
requests.get("http://192.168.x.x/tat",  timeout=3)
```

ESP32 chạy HTTP server nhúng (WebServer.h) trên port 80. Nhận GET request → cập nhật `capDoHienTai` → gọi `capNhatTrangThai()`.

---

## 3. Sơ đồ dữ liệu đầy đủ

```
[Sóng âm]
    ↓ INMP441 (I2S)
[int32 raw, 32-bit, 16kHz]
    ↓ >> 16 (lấy 16 bit có nghĩa)
[int16, 512 mẫu/gói + header 0xAA55]
    ↓ USB Serial 921600 baud
[Raw bytes]
    ↓ struct.unpack('<512h')
[List[int16], 512 phần tử]
    ↓ VAD: avg_amplitude > 1200
[List[int16], 1 câu hoàn chỉnh ~0.5-3s]
    ↓ wave.open + BytesIO
[WAV in-memory, 16-bit, mono, 16kHz]
    ↓ faster-whisper medium (GPU)
[str] — ví dụ: "bật quạt"
    ↓ filter hallucination
[str đã lọc] hoặc bỏ qua
    ↓ keyword matching + cooldown
[HTTP GET /tang hoặc /tat]
    ↓ WiFi → ESP32
[analogWrite + FastLED]
    ↓
[Quạt + LED thay đổi]
```

---

## 4. Các thư viện sử dụng

### ESP32 (C++ / Arduino Framework)
| Thư viện | Mục đích |
|---|---|
| `driver/i2s.h` | Driver đọc mic I2S — built-in ESP-IDF |
| `WiFi.h` | Kết nối WiFi — built-in Arduino ESP32 |
| `WebServer.h` | HTTP server nhúng — built-in Arduino ESP32 |
| `FastLED` | Điều khiển LED WS2812B RGB |

### Python (Laptop)
| Thư viện | Mục đích |
|---|---|
| `pyserial` | Đọc dữ liệu từ cổng USB Serial |
| `struct` | Giải mã binary (built-in Python) |
| `wave` | Đóng gói audio thành WAV (built-in Python) |
| `faster-whisper` | Nhận dạng giọng nói AI (OpenAI Whisper, tối ưu ONNX/CTranslate2) |
| `flask` | Web server Python |
| `flask-socketio` | WebSocket real-time (hiển thị sóng âm + trạng thái lên web) |
| `requests` | Gửi HTTP request đến ESP32 |
| `threading` | Chạy song song nhiều luồng |

---

## 5. Kiến trúc đa luồng (Multi-threading)

```
Main Thread:    Flask HTTP server (xử lý request từ browser)
Thread 1:       serial_reader()      — đọc COM5 liên tục
Thread 2:       vad_and_transcribe() — lắng nghe audio_queue
Thread 3+:      process_speech()     — tạo mới mỗi câu, tự kết thúc

Giao tiếp giữa Thread 1 và Thread 2: audio_queue (Queue thread-safe)
Giao tiếp giữa Thread 1 và recording: recording_lock (Lock)
```

---

## 6. Hướng phát triển tiếp theo — NLP nâng cao

**Hạn chế của keyword matching:**
- Phải nói đúng từ khóa cứng nhắc
- Không hiểu câu phức tạp: "trời nóng quá", "thôi ngủ rồi"

**Giải pháp: Fine-tune PhoBERT**

PhoBERT là model BERT pre-trained trên corpus tiếng Việt lớn (VinAI Research).

```
Kiến trúc pipeline NLP mới:
[text từ Whisper]
    ↓
[PhoBERT Tokenizer] — tokenize tiếng Việt
    ↓
[PhoBERT Encoder] — hiểu ngữ nghĩa câu
    ↓
[Classification Head] — phân loại ý định
    ↓
[Intent: TURN_ON / TURN_OFF / INCREASE / DECREASE / SET_LEVEL_X]
    ↓
[Lệnh ESP32]
```

**Dataset cần xây dựng:**
```
"bật quạt đi"           → TURN_ON
"cho tôi bật cái quạt"  → TURN_ON
"trời nóng quá"         → TURN_ON
"tắt hết đi"            → TURN_OFF
"thôi ngủ rồi"          → TURN_OFF
"mạnh lên một chút"     → INCREASE
"nhỏ lại"               → DECREASE
"để số hai thôi"        → SET_LEVEL_2
```

**Train trên Google Colab Pro (A100):**
```python
from transformers import AutoModelForSequenceClassification
model = AutoModelForSequenceClassification.from_pretrained(
    "vinai/phobert-base", num_labels=6
)
# Fine-tune với dataset trên → export → tích hợp vào server.py
```
