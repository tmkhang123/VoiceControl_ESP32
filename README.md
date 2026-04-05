# Voice Control ESP32 — Smart Fan

Điều khiển quạt bằng giọng nói tiếng Việt. ESP32 thu âm qua mic I2S, stream audio qua WiFi WebSocket đến Python server, nhận diện giọng nói bằng Whisper + PhoBERT, điều khiển quạt qua HTTP.

## Pipeline

```
ESP32 (mic I2S)
  → VAD on-device (energy-based)
  → WiFi WebSocket
  → Python Server
      → DeepFilterNet3 (speech enhancement)
      → faster-whisper (speech-to-text)
      → PhoBERT (intent classification)
  → HTTP → ESP32 (điều khiển quạt)
```

---

## Yêu cầu phần cứng

- ESP32-S3
- Mic INMP441 (I2S): WS=11, SD=10, SCK=12
- LED WS2812B: pin 48
- Quạt DC + driver: pin 4

---

## Cài đặt

### 1. ESP32 Firmware

**Cài đặt PlatformIO** (VS Code extension hoặc CLI).

Mở `src/main.cpp`, thay các placeholder:

```cpp
#define SERVER_IP "YOUR_SERVER_IP"    // IP máy tính chạy server.py
const char *ssid = "YOUR_WIFI_SSID";
const char *password = "YOUR_WIFI_PASSWORD";
```

Build & Upload:
```
pio run --target upload
```

Sau khi upload, mở Serial Monitor (921600 baud) — ESP32 sẽ in IP của nó:
```
IP ESP32: 192.168.x.x
```

---

### 2. Python Server

**Yêu cầu:** Python 3.10+

```bash
cd web_interface
pip install -r requirements.txt
pip install torch torchaudio==2.0.2 --index-url https://download.pytorch.org/whl/cpu
```

> **Nếu có GPU NVIDIA**, thay dòng cuối bằng `pip install torch torchaudio` và đổi trong `server.py`:
> ```python
> whisper_model = WhisperModel("medium", device="cuda", compute_type="float16")
> ```
>
> **Nếu dùng CPU** (mặc định), giữ nguyên `server.py`:
> ```python
> whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
> ```

Mở `server.py`, thay:
```python
ESP32_IP = "http://YOUR_ESP32_IP"   # IP in ra ở Serial Monitor bước trên
```

**Tải PhoBERT model** (fine-tuned, không có trong repo do file nặng):
https://drive.google.com/drive/folders/18fSoBI-JMfTt-7BwKpW60mQ3coG_aB9B?usp=sharing
Giải nén vào `nlp/phobert_intent/` sao cho có:
```
nlp/phobert_intent/
  model.safetensors
  tokenizer_config.json
  vocab.txt
  label_map.json
  ...
```

Chạy server:
```bash
python server.py
```

Server khởi động 2 port:
- `5000` — Web UI + Socket.IO (Flutter app, trình duyệt)
- `5001` — WebSocket nhận audio từ ESP32

---

### 3. Flutter App (Android)

**Yêu cầu:** Flutter SDK 3.x+

Mở `fan_control_app/lib/main.dart`, thay:
```dart
const String serverUrl = 'http://YOUR_SERVER_IP:5000';
```

Cắm điện thoại Android (bật USB Debugging), chạy:
```bash
cd fan_control_app
flutter run
```

Hoặc build APK:
```bash
flutter build apk --debug
```

---

## Cách dùng

1. Cắm ESP32 vào nguồn (power bank hoặc ổ điện)
2. Chạy `python server.py` trên máy tính
3. Mở app Flutter hoặc trình duyệt `http://localhost:5000`
4. Nói lệnh bằng tiếng Việt:

| Lệnh ví dụ | Hành động |
|---|---|
| "bật quạt", "nóng quá" | Bật cấp 1 |
| "tắt quạt", "thôi ngủ rồi" | Tắt quạt |
| "số hai", "vừa thôi" | Cấp 2 |
| "mạnh nhất", "số ba" | Cấp 3 |
| "tăng lên", "mạnh hơn" | Tăng 1 cấp |
| "nhỏ lại", "bớt đi" | Giảm 1 cấp |

---

## Cấu trúc thư mục

```
VoiceControl_ESP32/
├── src/
│   └── main.cpp          # ESP32 firmware (FreeRTOS + VAD + WebSocket)
├── web_interface/
│   ├── server.py          # Python server (Whisper + PhoBERT + Flask)
│   ├── templates/
│   │   └── index.html     # Web UI
│   └── requirements.txt
├── nlp/
│   ├── dataset.csv        # Training data (140+ câu, 8 intent)
│   ├── train_phobert.ipynb # Notebook fine-tune PhoBERT (Google Colab)
│   └── phobert_intent/    # Model đã train (không có trong repo)
├── fan_control_app/       # Flutter mobile app
│   └── lib/main.dart
└── platformio.ini
```

---

## Công nghệ

| Thành phần | Công nghệ |
|---|---|
| Vi điều khiển | ESP32-S3, FreeRTOS |
| Thu âm | I2S (INMP441), VAD energy-based |
| Truyền audio | WiFi WebSocket (không cần USB) |
| Lọc nhiễu | DeepFilterNet3 (CNN+RNN) |
| Speech-to-text | faster-whisper medium |
| NLP | PhoBERT (VinAI) fine-tuned |
| Backend | Python, Flask, Flask-SocketIO |
| Mobile app | Flutter |
