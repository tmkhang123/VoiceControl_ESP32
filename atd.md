# Giải thích Chi tiết Code — Voice Control ESP32

---

## PHẦN 1: main.cpp (Firmware ESP32)

---

### Các thư viện include

```cpp
#include <Arduino.h>      // Framework Arduino cơ bản
#include <WiFi.h>         // Kết nối WiFi
#include <WebServer.h>    // Tạo HTTP server trên ESP32
#include <FastLED.h>      // Điều khiển LED WS2812B RGB
#include <driver/i2s.h>   // Driver đọc mic I2S (built-in trong ESP-IDF)
```

---

### Định nghĩa chân (pin definitions)

```cpp
#define LED_ONBOARD_PIN 48  // GPIO 48: chân LED WS2812B tích hợp trên board
#define FAN_PIN 4           // GPIO 4:  chân điều khiển quạt qua PWM
#define I2S_WS  11          // GPIO 11: Word Select (kênh trái/phải)
#define I2S_SD  10          // GPIO 10: Serial Data (dữ liệu âm thanh)
#define I2S_SCK 12          // GPIO 12: Serial Clock (xung nhịp đồng bộ)
#define I2S_PORT I2S_NUM_0  // Dùng I2S bus số 0 (ESP32-S3 có 2 bus I2S)
```

**I2S là gì?**
I2S (Inter-IC Sound) là giao thức kỹ thuật số chuyên dùng cho âm thanh.
Thay vì truyền tín hiệu analog (dễ nhiễu), I2S truyền số nhị phân → chất lượng cao hơn.

---

### Buffer và biến toàn cục

```cpp
#define bufferLen 64         // Đọc 64 mẫu mỗi lần từ I2S (DMA buffer nhỏ)
#define SEND_BUFFER_SIZE 512 // Gom đủ 512 mẫu mới gửi lên laptop (gói lớn hơn = hiệu quả hơn)
static int16_t sendBuf[SEND_BUFFER_SIZE]; // Mảng chứa 512 mẫu 16-bit chờ gửi
static int sendBufIdx = 0;   // Con trỏ vị trí hiện tại trong sendBuf

int capDoHienTai = 0;        // Cấp độ quạt hiện tại: 0=tắt, 1, 2, 3
```

**Tại sao cần 2 buffer?**
- `bufferLen=64`: DMA (Direct Memory Access) của ESP32 đọc từng đợt nhỏ 64 mẫu từ mic
- `SEND_BUFFER_SIZE=512`: Gom 8 lần đọc DMA → gửi 1 gói lớn → ít overhead hơn

---

### Hàm `i2s_install()`

```cpp
void i2s_install() {
  const i2s_config_t i2s_config = {
    .mode = I2S_MODE_MASTER | I2S_MODE_RX,    // Master: ESP32 tạo clock; RX: chỉ nhận (không phát)
    .sample_rate = 16000,                      // 16000 Hz = chuẩn tối thiểu cho nhận dạng giọng nói
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT, // INMP441 xuất 24-bit nhưng ESP32 đọc 32-bit
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,  // Mic mono → chỉ đọc kênh trái
    .communication_format = I2S_COMM_FORMAT_STAND_I2S, // Chuẩn I2S Philips
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,  // Ưu tiên interrupt thấp (đủ dùng)
    .dma_buf_count = 8,   // 8 buffer DMA xoay vòng → không bị mất dữ liệu khi CPU bận
    .dma_buf_len = bufferLen, // Mỗi buffer DMA chứa 64 mẫu
    .use_apll = false     // Không dùng APLL (bộ tạo xung chính xác) → dùng clock thường
  };
  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
}
```

**Tại sao sample_rate = 16000?**
Whisper yêu cầu 16000 Hz. Nếu thấp hơn → thiếu dữ liệu. Nếu cao hơn → tốn bandwidth không cần thiết.

**Tại sao 32-bit nhưng chỉ dùng 16-bit?**
INMP441 xuất 24-bit âm thanh thực trong 32-bit frame (8 bit thừa = 0). Dữ liệu quan trọng nằm ở 16 bit cao nhất (bit 31→16). Khi gửi lên laptop ta dịch phải 16 bit (`>> 16`) để lấy 16-bit có nghĩa.

---

### Hàm `i2s_setpin()`

```cpp
void i2s_setpin() {
  const i2s_pin_config_t pin_config = {
    .bck_io_num  = I2S_SCK,  // Clock → GPIO 12
    .ws_io_num   = I2S_WS,   // Word Select → GPIO 11
    .data_out_num = -1,       // -1 = không dùng (ta chỉ nhận, không phát)
    .data_in_num  = I2S_SD   // Data In → GPIO 10
  };
  i2s_set_pin(I2S_PORT, &pin_config);
}
```

---

### Hàm `capNhatTrangThai()`

```cpp
void capNhatTrangThai() {
  switch (capDoHienTai) {
    case 0: analogWrite(FAN_PIN, 0);   leds[0] = CRGB::Black;  break; // Tắt
    case 1: analogWrite(FAN_PIN, 80);  leds[0] = CRGB::Red;    break; // Chậm
    case 2: analogWrite(FAN_PIN, 150); leds[0] = CRGB::Yellow; break; // Vừa
    case 3: analogWrite(FAN_PIN, 255); leds[0] = CRGB::Green;  break; // Nhanh
  }
  FastLED.show(); // Cập nhật LED vật lý
}
```

**`analogWrite` là gì?**
Tạo tín hiệu **PWM (Pulse Width Modulation)**: bật/tắt cực nhanh theo tỉ lệ.
- `analogWrite(FAN_PIN, 80)` = 31% thời gian bật → quạt quay chậm
- `analogWrite(FAN_PIN, 255)` = 100% thời gian bật → quạt full

---

### Hàm `setup()` — Chạy 1 lần khi khởi động

```cpp
void setup() {
  Serial.begin(921600); // Tốc độ 921600 baud = ~92000 bytes/giây (đủ cho audio 16-bit 16kHz)

  pinMode(FAN_PIN, OUTPUT);
  FastLED.addLeds<WS2812B, LED_ONBOARD_PIN, GRB>(leds, NUM_LEDS); // Khai báo LED WS2812B
  FastLED.setBrightness(50); // Độ sáng 50/255

  i2s_install();  // Cài driver I2S
  i2s_setpin();   // Gán chân vật lý
  i2s_start(I2S_PORT); // Bắt đầu chạy I2S

  WiFi.begin(ssid, password); // Kết nối WiFi
  // Thử 20 lần, mỗi lần cách 500ms = tối đa 10 giây
  while (WiFi.status() != WL_CONNECTED && soLanThu < 20) { delay(500); }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(WiFi.localIP()); // In IP để biết địa chỉ ESP32
    // Đăng ký các route HTTP:
    server.on("/tang", []() { capDoHienTai++; capNhatTrangThai(); }); // Tăng cấp
    server.on("/tat",  []() { capDoHienTai=0; capNhatTrangThai(); }); // Tắt
    server.begin();
  }
}
```

---

### Hàm `loop()` — Chạy liên tục

```cpp
void loop() {
  server.handleClient(); // Xử lý HTTP request từ laptop (nếu có lệnh /tang hoặc /tat)

  int32_t sBuffer[bufferLen]; // Buffer tạm: 64 mẫu 32-bit
  size_t bytesIn = 0;
  // Đọc từ I2S, không chờ (timeout=0)
  i2s_read(I2S_PORT, &sBuffer, bufferLen * 4, &bytesIn, 0);

  if (result == ESP_OK && bytesIn > 0) {
    int samples = bytesIn / 4; // Số mẫu thực sự đọc được

    for (int i = 0; i < samples; i++) {
      // Chuyển 32-bit → 16-bit: lấy 16 bit cao (dữ liệu thực của INMP441)
      sendBuf[sendBufIdx++] = (int16_t)(sBuffer[i] >> 16);

      if (sendBufIdx >= SEND_BUFFER_SIZE) { // Đủ 512 mẫu?
        uint8_t header[2] = {0xAA, 0x55};   // Header nhận dạng gói
        Serial.write(header, 2);             // Gửi header
        Serial.write((uint8_t*)sendBuf, SEND_BUFFER_SIZE * 2); // Gửi 1024 bytes data
        sendBufIdx = 0; // Reset con trỏ
      }
    }
  }
}
```

**Tại sao `>> 16`?**
```
Dữ liệu INMP441 trong 32-bit frame:
[Bit 31 .... Bit 16] [Bit 15 .... Bit 0]
   ← Dữ liệu thật →    ← Toàn số 0 →

Dịch phải 16 bit → chỉ còn lại phần dữ liệu thật → int16
```

---

## PHẦN 2: server.py (Python — Não xử lý)

---

### Khởi động và cấu hình

```python
ESP32_IP    = "http://192.168.1.52"  # IP của ESP32 trên mạng WiFi
SERIAL_PORT = "COM5"                 # Cổng USB của ESP32
BAUD_RATE   = 921600                 # Phải khớp với Serial.begin() trong main.cpp
CHUNK_SAMPLES = 512                  # Phải khớp với SEND_BUFFER_SIZE
HEADER      = bytes([0xAA, 0x55])    # Phải khớp với header trong main.cpp
SAMPLE_RATE = 16000                  # Phải khớp với sample_rate I2S
```

---

### Hàm `serial_reader()` — Thread đọc Serial

```python
def serial_reader():
    buf = bytearray()  # Buffer tích lũy bytes chưa xử lý

    while True:  # Vòng ngoài: tự kết nối lại nếu bị ngắt
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        ser.reset_input_buffer()  # Xóa dữ liệu cũ trong buffer

        while True:  # Vòng trong: đọc liên tục
            chunk = ser.read(ser.in_waiting or 1)  # Đọc hết bytes đang có
            if chunk:
                buf.extend(chunk)  # Thêm vào buffer tích lũy

            # Tìm và xử lý các gói hoàn chỉnh
            while True:
                idx = buf.find(HEADER)  # Tìm vị trí header [0xAA, 0x55]
                if idx == -1: break     # Chưa có header → chờ thêm

                packet_end = idx + 2 + CHUNK_SAMPLES * 2  # Vị trí kết thúc gói
                if len(buf) < packet_end: break  # Chưa đủ dữ liệu → chờ thêm

                # Lấy đúng 1024 bytes data (bỏ 2 bytes header)
                data = buf[idx + 2 : packet_end]
                buf  = buf[packet_end:]  # Cắt bỏ gói đã xử lý

                # Giải mã binary → list 512 số int16
                # '<' = little-endian (ESP32 dùng little-endian)
                # 'h' = signed short = int16
                samples = list(struct.unpack(f'<{CHUNK_SAMPLES}h', data))

                audio_queue.put(samples)           # Đưa vào hàng đợi cho VAD
                socketio.emit("mic_batch", {...})  # Vẽ sóng âm lên web
```

---

### Hàm `vad_and_transcribe()` — Thread VAD

```python
def vad_and_transcribe():
    speech_buffer  = []   # Tích lũy audio khi đang nói
    silence_samples = 0   # Đếm số mẫu im lặng liên tiếp
    is_speaking = False   # Đang trong câu nói hay không

    # Tính ngưỡng số mẫu:
    silence_limit      = int(0.8 * 16000)  = 12800 mẫu im lặng → kết thúc câu
    min_speech_samples = int(0.3 * 16000)  = 4800 mẫu tối thiểu → mới xử lý

    while True:
        samples = audio_queue.get()  # Lấy 512 mẫu từ hàng đợi

        # Tính biên độ trung bình: đây là "VAD thủ công"
        avg_amp = sum(abs(s) for s in samples) / len(samples)

        if avg_amp > SPEECH_THRESHOLD (800):  # Có tiếng
            is_speaking = True
            speech_buffer.extend(samples)     # Thêm vào buffer câu nói
            silence_samples = 0               # Reset bộ đếm im lặng

        else:  # Im lặng
            if is_speaking:                   # Đang nói mà gặp im lặng
                speech_buffer.extend(samples) # Vẫn thêm (phòng ngừa ngắt giữa từ)
                silence_samples += 512        # Đếm mẫu im lặng

                if silence_samples >= 12800:  # Im lặng đủ 0.8 giây
                    # Gửi toàn bộ câu sang Whisper (trong thread riêng)
                    threading.Thread(target=process_speech,
                                     args=(speech_buffer.copy(),)).start()
                    speech_buffer.clear()
                    is_speaking = False
```

**VAD ở đây là "thủ công" hay dùng thư viện?**
→ **Thủ công hoàn toàn.** Chỉ dùng phép tính trung bình biên độ, không cần thư viện VAD nào.
→ Đây là điểm bạn có thể trình bày với thầy: "tự viết VAD dựa trên năng lượng tín hiệu"

---

### Hàm `process_speech()` — Xử lý một câu nói

```python
def process_speech(samples):
    # Bước 1: Đóng gói audio thành file WAV trong RAM (không ghi ra ổ cứng)
    wav_io = io.BytesIO()  # BytesIO = file ảo trong RAM
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)         # Mono
        wf.setsampwidth(2)         # 2 bytes = 16-bit
        wf.setframerate(16000)     # 16000 Hz
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))  # Ghi data
    wav_io.seek(0)  # Quay về đầu file để Whisper đọc

    # Bước 2: Cho Whisper "nghe"
    segments, _ = whisper_model.transcribe(wav_io, language="vi", ...)

    # Bước 3: Ghép các đoạn text lại
    text = " ".join(seg.text for seg in segments).strip()

    # Bước 4: Hiển thị lên web + phân tích lệnh
    socketio.emit("transcription", {"text": text})
    parse_and_execute(text)
```

---

### Hàm `parse_and_execute()` — Phân tích lệnh

```python
def parse_and_execute(text):
    t = text.lower()  # Chuyển về chữ thường để so sánh không phân biệt hoa/thường

    if "tắt quạt" in t:            # Tìm chuỗi con "tắt quạt" trong text
        _call_esp32("/tat")
    elif "ba" in t.split():        # t.split() tách thành từng từ → tìm từ "ba" riêng lẻ
        ...                         # (tránh nhầm "bàn", "bàng" có chứa "ba")
    elif "hai" in t.split():
        ...
    elif "bật quạt" in t or any(w in t.split() for w in ["một", "máu", "mốt"]):
        ...
```

**Tại sao "một" bị nhầm thành "máu"?**
Trong tiếng Việt "một" và "máu" có âm vị gần nhau. Whisper đôi khi nhầm. Giải pháp: thêm "máu" vào keyword của cấp 1.

**Tại sao dùng `t.split()` thay vì `in t`?**
```
t = "quạt ba lá"
"ba" in t          → True  (đúng)
"ba" in t.split()  → True  (đúng)

t = "bàn"
"ba" in t          → True  (SAI! "bàn" chứa "ba")
"ba" in t.split()  → False (đúng! "bàn" ≠ "ba")
```

---

### Hàm `_call_esp32()` — Gửi lệnh HTTP

```python
def _call_esp32(path: str) -> bool:
    try:
        requests.get(f"{ESP32_IP}{path}", timeout=3)
        # Ví dụ: GET http://192.168.1.52/tang
        return True
    except Exception:
        return False  # ESP32 offline hoặc mạng lỗi → không crash, trả về False
```

---

### Threading — Tại sao cần nhiều thread?

```
Main thread:       Flask web server (xử lý HTTP từ browser)
Thread 1 (t):      serial_reader    (đọc COM5 liên tục, không chờ)
Thread 2 (t2):     vad_and_transcribe (lắng nghe audio_queue)
Thread 3 (tạm):    process_speech   (tạo mới mỗi câu, chạy Whisper, tự kết thúc)
```

Nếu chạy tất cả trong 1 thread → khi Whisper xử lý (mất 2-5s) → serial không đọc được → audio bị mất → sóng âm web bị gián đoạn.

---

### WebSocket — Tại sao dùng?

HTTP thông thường: Browser hỏi → Server trả lời (1 chiều, phải hỏi liên tục)
WebSocket: Kết nối 2 chiều thường trực → Server tự đẩy dữ liệu xuống Browser

```python
socketio.emit("mic_batch", {"values": samples})   # Server → Browser: vẽ sóng
socketio.emit("transcription", {"text": text})    # Server → Browser: hiện text
socketio.emit("command", {"level": 1, ...})       # Server → Browser: cập nhật LED
```

---

## PHẦN 3: Câu hỏi thầy có thể hỏi

### "Tại sao dùng Whisper thay vì tự xây dựng model?"

Trả lời đúng:
- Whisper là model **Speech-to-Text** (chuyển âm thanh → chữ), không phải model điều khiển
- Phần **tự làm** là: VAD, command parsing, hệ thống điều khiển, giao tiếp ESP32-Laptop
- Phần **NLP** (hiểu lệnh) mới là trọng tâm đồ án → sẽ train PhoBERT

### "Tại sao không nhúng vào ESP32 (Edge Impulse)?"

- ESP32-S3 đủ mạnh cho model nhỏ, nhưng Whisper tiếng Việt cần ít nhất model `medium` (~500MB)
- ESP32 chỉ có 8MB PSRAM → không thể chứa
- Edge Impulse chỉ nhận vài từ tiếng Anh, không phù hợp NLP tiếng Việt

### "VAD có phải thư viện không?"

Không. VAD trong code này là **tự viết** dựa trên năng lượng tín hiệu:
```python
avg_amp = sum(abs(s) for s in samples) / len(samples)
if avg_amp > threshold: # đang nói
```
Đây là thuật toán **Energy-based VAD** — phương pháp cổ điển trong xử lý tín hiệu số.

---

## PHẦN 4: Bước tiếp theo — NLP với PhoBERT

### Vấn đề của keyword matching hiện tại

```
"bật quạt" → ✓ nhận dạng được
"ơi bật giúp tôi cái quạt với" → ✗ không nhận (không có "bật quạt" liền nhau)
"trời nóng quá" → ✗ không nhận (dù ý là muốn bật quạt)
```

### Giải pháp: Fine-tune PhoBERT

PhoBERT là BERT được pre-train trên tiếng Việt. Ta sẽ:

1. **Tạo dataset** (câu → nhãn lệnh):
   ```
   "bật quạt đi"          → TURN_ON
   "tắt hết đi"           → TURN_OFF
   "trời nóng quá"        → TURN_ON
   "tăng lên một chút"    → INCREASE
   "thôi ngủ rồi"         → TURN_OFF
   ```

2. **Fine-tune trên Colab Pro** (A100 GPU):
   ```python
   from transformers import AutoModelForSequenceClassification
   model = AutoModelForSequenceClassification.from_pretrained("vinai/phobert-base")
   # Train với dataset trên
   ```

3. **Export và tích hợp** vào server.py:
   ```python
   # Thay parse_and_execute bằng:
   intent = phobert_model.predict(text)  # → "TURN_ON", "TURN_OFF", ...
   ```

**Colab Pro dùng để làm gì?**
- A100 GPU train nhanh hơn 10-20x so với laptop
- Sau khi train xong → tải model về laptop → chạy inference (dự đoán) trên laptop
- Colab **không** dùng để inference real-time vì cần kết nối internet ổn định
