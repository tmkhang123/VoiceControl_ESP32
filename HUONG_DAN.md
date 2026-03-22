# Tài liệu Dự án Voice Control ESP32
> Giải thích toàn bộ từ đầu đến hiện tại

---

## 1. Tổng quan — Dự án này làm gì?

Bạn nói tiếng Việt vào mic → hệ thống hiểu lệnh → điều khiển quạt/đèn.

```
[Bạn nói] → [Mic INMP441 trên ESP32] → [ESP32 gửi audio qua USB Serial]
         → [Python server trên Laptop] → [Whisper nhận diện] → [Phân tích lệnh]
         → [Gửi lệnh HTTP về ESP32] → [Đèn LED / Quạt thay đổi]
```

---

## 2. Phần cứng

| Linh kiện | Vai trò |
|---|---|
| ESP32-S3 N16R8 | Vi điều khiển chính, 16MB Flash, 8MB PSRAM |
| Mic INMP441 | Thu âm thanh, chuẩn I2S |
| LED WS2812B | Hiển thị trạng thái (đỏ/vàng/xanh) |
| Quạt | Được điều khiển qua PWM |

**Kết nối mic INMP441:**
- WS (Word Select) → GPIO 11
- SD (Serial Data) → GPIO 10
- SCK (Clock) → GPIO 12

---

## 3. Luồng hoạt động chi tiết

### Bước 1: ESP32 thu âm
- Mic INMP441 dùng chuẩn **I2S** (Inter-IC Sound) — giao thức kỹ thuật số chuyên cho âm thanh
- ESP32 đọc dữ liệu âm thanh dạng **32-bit raw PCM** từ mic
- Sample rate: **16000 Hz** (16000 mẫu/giây — chuẩn cho nhận dạng giọng nói)

### Bước 2: ESP32 gửi audio lên Laptop
- ESP32 **không xử lý** âm thanh, chỉ chuyển tiếp
- Gom 512 mẫu → chuyển từ 32-bit xuống 16-bit → gửi qua **USB Serial (COM5)** với tốc độ **921600 baud**
- Định dạng gói tin gửi:
  ```
  [0xAA][0x55] + [512 mẫu × 2 bytes] = 1026 bytes/gói
  ```
  - `0xAA 0x55` là **header** để Python nhận biết đầu gói

### Bước 3: Python server nhận audio
- Python đọc từng byte từ Serial
- Tìm header `0xAA 0x55` → cắt ra đúng 512 mẫu
- Giải mã binary → list số nguyên int16

### Bước 4: VAD phát hiện tiếng nói
- **VAD (Voice Activity Detection)** = "cái tai sơ cấp"
- Không nhận dạng được nội dung, chỉ biết **có tiếng người hay không**
- Cách hoạt động: tính trung bình biên độ (độ to) của 512 mẫu
  ```
  avg_amplitude = sum(|sample|) / 512
  nếu avg > SPEECH_THRESHOLD (800) → có tiếng
  nếu im lặng > 0.8 giây sau khi có tiếng → câu kết thúc
  ```
- Khi câu kết thúc → gửi toàn bộ audio câu đó sang Whisper

### Bước 5: Whisper nhận dạng giọng nói
- **faster-whisper** = model AI của OpenAI, chạy trên Laptop
- Nhận vào: file audio WAV (16-bit, 16kHz, mono)
- Trả về: chuỗi text tiếng Việt
- Ví dụ: audio "bật quạt" → `"bật quạt"`

### Bước 6: Phân tích lệnh (NLP đơn giản)
- So sánh text với từ khóa:
  ```
  "bật quạt" → cấp 1
  "hai"      → cấp 2
  "ba"       → cấp 3
  "tắt quạt" → tắt
  "tăng tốc" → tăng 1 cấp
  ```

### Bước 7: Gửi lệnh về ESP32
- Python gọi HTTP đến ESP32:
  - `GET /tang` → tăng cấp
  - `GET /tat`  → tắt
- ESP32 nhận → cập nhật LED + quạt

---

## 4. Tại sao KHÔNG dùng Edge Impulse?

| | Edge Impulse | Cách đang dùng |
|---|---|---|
| Chạy ở đâu | Trong ESP32 | Laptop |
| Tiếng Việt | Rất kém | Tốt (Whisper được train trên nhiều ngôn ngữ) |
| Thêm lệnh mới | Phải train lại | Chỉ thêm từ khóa vào code |
| NLP phức tạp | Không làm được | Làm được |
| Cần WiFi | Không | Có |

**Kết luận:** Edge Impulse chỉ nhận được vài từ tiếng Anh đơn giản. Với tiếng Việt + đồ án HCI cần NLP → Whisper + Laptop là lựa chọn đúng.

---

## 5. Thư viện đã cài

### Trên ESP32 (platformio.ini)
```ini
lib_deps =
    fastled/FastLED        ; Điều khiển LED WS2812B
```
Framework Arduino đã có sẵn: `WiFi.h`, `WebServer.h`, `driver/i2s.h`

### Trên Laptop (Python — conda env HCI)
```bash
pip install flask              # Web server
pip install flask-socketio     # WebSocket real-time
pip install pyserial           # Đọc dữ liệu từ cổng COM
pip install faster-whisper     # Nhận dạng giọng nói AI
pip install nvidia-cublas-cu12 # GPU CUDA support
pip install nvidia-cudnn-cu12  # GPU CUDA support
pip install nvidia-cuda-runtime-cu12  # GPU CUDA support
```

---

## 6. Tại sao dùng Binary thay vì Text?

Ban đầu ESP32 gửi text:
```
>mic:1043392\n   ← 14 bytes cho 1 mẫu → ~6500 mẫu/giây
```

Sau khi đổi sang binary:
```
[0xAA][0x55][data]  ← 2 bytes cho 1 mẫu → ~46000 mẫu/giây
```

**Tốc độ tăng 7 lần** → audio mượt hơn, nhận dạng chính xác hơn.

---

## 7. VAD là gì? Khác gì Thu âm và Whisper?

```
Thu âm (Recording)  → Lưu lại audio để nghe lại (nút THU ÂM trên web)
VAD                 → Phát hiện khi nào có tiếng người đang nói
Whisper             → Nghe audio và chuyển thành chữ
```

**Quy trình:**
```
Audio liên tục → VAD lọc → chỉ gửi đoạn có tiếng → Whisper dịch → lệnh
```

Nếu không có VAD: Whisper phải xử lý toàn bộ audio kể cả im lặng → chậm + tốn tài nguyên.

---

## 8. Cấu hình Whisper

```python
whisper_model = WhisperModel("medium", device="cuda", compute_type="float16")

segments, _ = whisper_model.transcribe(
    audio,
    language="vi",                    # Tiếng Việt
    beam_size=5,                      # Tìm kiếm 5 khả năng, chọn tốt nhất
    temperature=0,                    # Kết quả ổn định, không "sáng tạo"
    initial_prompt="bật quạt, tắt quạt, ...",  # Gợi ý từ vựng
    no_speech_threshold=0.6,          # Bỏ qua nếu không đủ tin là có tiếng người
    condition_on_previous_text=False, # Mỗi câu độc lập, không bị ảnh hưởng câu trước
    compression_ratio_threshold=2.4   # Chặn lặp vô tận ("Tạm biệt. Tạm biệt. Tạm biệt...")
)
```

**initial_prompt là gì?**
Không phải lệnh, là "từ điển gợi ý". Nói cho Whisper biết trong ngữ cảnh này hay xuất hiện những từ nào → nhận dạng chính xác hơn.

**Model sizes:**
| Model | RAM cần | Độ chính xác tiếng Việt |
|---|---|---|
| tiny | ~400MB | Thấp |
| base | ~500MB | Trung bình |
| small | ~1GB | Tốt |
| **medium** | ~2.5GB | **Rất tốt ← đang dùng** |
| large-v3 | ~6GB | Tốt nhất |

---

## 9. Mapping lệnh hiện tại

| Nói | Whisper nhận | ESP32 làm |
|---|---|---|
| "bật quạt" | "bật quạt" | LED đỏ, quạt cấp 1 |
| "một" / "máu" | "một" hoặc "máu" | LED đỏ, quạt cấp 1 |
| "hai" | "hai" | LED vàng, quạt cấp 2 |
| "ba" | "ba" | LED xanh, quạt cấp 3 |
| "quạt mạnh" | "quạt mạnh" | LED xanh, quạt cấp 3 |
| "tắt quạt" | "tắt quạt" | LED tắt, quạt tắt |
| "tăng tốc" | "tăng tốc" | Tăng 1 cấp |

---

## 10. Bước tiếp theo — NLP nâng cao

Hiện tại đang dùng **keyword matching** (so từ khóa đơn giản).

Vấn đề:
- Nói "ơi bật giúp tôi cái quạt với" → không match vì không có chữ "bật quạt" liền nhau
- Nói "trời nóng quá" → không hiểu ý muốn bật quạt

**Giải pháp: PhoBERT (NLP tiếng Việt)**
```
Text từ Whisper → PhoBERT phân tích ý nghĩa → ra lệnh
```

PhoBERT là model NLP được train trên tiếng Việt, hiểu được:
- "trời nóng" → bật quạt
- "thôi ngủ rồi" → tắt quạt
- "mạnh thêm chút" → tăng tốc

**Train PhoBERT trên Google Colab Pro:**
1. Tạo dataset: câu tiếng Việt + nhãn lệnh
2. Fine-tune PhoBERT trên Colab A100
3. Export model
4. Tích hợp vào server.py thay cho keyword matching

---

## 11. Cấu trúc file dự án

```
VoiceControl_ESP32/
├── src/
│   └── main.cpp          # Firmware ESP32 (I2S mic + WiFi + HTTP server)
├── web_interface/
│   ├── server.py         # Python server (Serial + VAD + Whisper + Flask)
│   └── templates/
│       └── index.html    # Web dashboard
├── platformio.ini        # Cấu hình build ESP32
└── HUONG_DAN.md          # File này
```
