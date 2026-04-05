#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <FastLED.h>
#include <driver/i2s.h>
#include <WebSocketsClient.h>

// IP của Python server (máy tính chạy server.py)
#define SERVER_IP "YOUR_SERVER_IP"   // ← thay bằng IP máy tính chạy server.py
#define SERVER_PORT 5001

const char *ssid = "YOUR_WIFI_SSID";       // ← thay bằng tên WiFi
const char *password = "YOUR_WIFI_PASSWORD"; // ← thay bằng mật khẩu WiFi

#define LED_ONBOARD_PIN 48
#define FAN_PIN 4
#define I2S_WS 11
#define I2S_SD 10
#define I2S_SCK 12
#define I2S_PORT I2S_NUM_0

#define NUM_LEDS 1
CRGB leds[NUM_LEDS];
WebServer server(80);
WebSocketsClient webSocket;

int capDoHienTai = 0;

#define bufferLen 64
#define SEND_BUFFER_SIZE 512

// VAD
#define VAD_THRESHOLD 500
#define VAD_HANGOVER_PACKETS 12

// ─── FreeRTOS Queue ───────────────────────────────────────────────────────────
#define QUEUE_SIZE 4

typedef struct {
  int16_t data[SEND_BUFFER_SIZE];
} AudioPacket;

QueueHandle_t audioQueue;

// ─── I2S setup ────────────────────────────────────────────────────────────────
void i2s_install()
{
  const i2s_config_t i2s_config = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = 16000,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = bufferLen,
      .use_apll = false};
  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
}

void i2s_setpin()
{
  const i2s_pin_config_t pin_config = {
      .bck_io_num = I2S_SCK, .ws_io_num = I2S_WS, .data_out_num = -1, .data_in_num = I2S_SD};
  i2s_set_pin(I2S_PORT, &pin_config);
}

// ─── Web UI ───────────────────────────────────────────────────────────────────
String getHTML()
{
  String html = "<!DOCTYPE html><html><head><meta charset='UTF-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:Helvetica;text-align:center;background:#1a1a1a;color:white;padding:20px;}";
  html += ".btn{width:100%;max-width:300px;margin:15px auto;padding:20px;font-size:18px;border:none;border-radius:10px;color:white;cursor:pointer;}";
  html += "h1{color:#ffcc00;} h3{color:#aaa;}";
  html += ".xanh{background:#4CAF50;} .xam{background:#555;}</style></head><body>";
  html += "<h1>QUẠT THÔNG MINH</h1>";
  html += "<h3>Trạng thái: Số " + String(capDoHienTai) + "</h3>";
  html += "<a href='/tang'><button class='btn xanh'>TĂNG TỐC (+)</button></a>";
  html += "<a href='/tat'><button class='btn xam'>TẮT QUẠT (OFF)</button></a>";
  html += "<p style='margin-top:20px;color:#888'>Mo Serial Plotter de xem song am!</p>";
  html += "</body></html>";
  return html;
}

void handleRoot() { server.send(200, "text/html", getHTML()); }

void capNhatTrangThai()
{
  switch (capDoHienTai)
  {
  case 0:
    analogWrite(FAN_PIN, 0);
    leds[0] = CRGB::Black;
    break;
  case 1:
    analogWrite(FAN_PIN, 80);
    leds[0] = CRGB::Red;
    break;
  case 2:
    analogWrite(FAN_PIN, 150);
    leds[0] = CRGB::Yellow;
    break;
  case 3:
    analogWrite(FAN_PIN, 255);
    leds[0] = CRGB::Green;
    break;
  }
  FastLED.show();

  if (WiFi.status() == WL_CONNECTED)
  {
    server.send(200, "text/html", getHTML());
  }
}

void onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length)
{
  if (type == WStype_CONNECTED)
    Serial.println("[WS] Ket noi server thanh cong!");
  else if (type == WStype_DISCONNECTED)
    Serial.println("[WS] Mat ket noi, dang thu lai...");
}

// ─── Task 1: Audio Capture + VAD (Core 0) ────────────────────────────────────
void audioTask(void *pvParameters)
{
  int32_t sBuffer[bufferLen];
  int16_t localBuf[SEND_BUFFER_SIZE];
  int localIdx = 0;
  int vadHangover = 0;

  Serial.println("[AudioTask] Bat dau tren Core 0");

  while (true)
  {
    size_t bytesIn = 0;
    esp_err_t result = i2s_read(I2S_PORT, &sBuffer, bufferLen * 4, &bytesIn, portMAX_DELAY);

    if (result == ESP_OK && bytesIn > 0)
    {
      int samples = bytesIn / 4;
      for (int i = 0; i < samples; i++)
      {
        localBuf[localIdx++] = (int16_t)(sBuffer[i] >> 16);

        if (localIdx >= SEND_BUFFER_SIZE)
        {
          // VAD: tính năng lượng trung bình
          int32_t energy = 0;
          for (int j = 0; j < SEND_BUFFER_SIZE; j++)
            energy += abs(localBuf[j]);
          int32_t avgAmp = energy / SEND_BUFFER_SIZE;

          if (avgAmp > VAD_THRESHOLD)
            vadHangover = VAD_HANGOVER_PACKETS;

          if (vadHangover > 0)
          {
            // Đưa buffer vào Queue để networkTask gửi đi
            AudioPacket pkt;
            memcpy(pkt.data, localBuf, sizeof(pkt.data));
            xQueueSend(audioQueue, &pkt, 0); // non-blocking: bỏ qua nếu queue đầy
            vadHangover--;
          }
          localIdx = 0;
        }
      }
    }
  }
}

// ─── Task 2: Network — WebSocket + HTTP (Core 1) ─────────────────────────────
void networkTask(void *pvParameters)
{
  AudioPacket pkt;

  Serial.println("[NetworkTask] Bat dau tren Core 1");

  while (true)
  {
    webSocket.loop();
    server.handleClient();

    // Nhận packet từ audioTask (non-blocking)
    if (xQueueReceive(audioQueue, &pkt, 0) == pdTRUE)
    {
      webSocket.sendBIN((uint8_t *)pkt.data, SEND_BUFFER_SIZE * 2);
    }

    vTaskDelay(1 / portTICK_PERIOD_MS); // yield cho các task khác
  }
}

// ─── Setup ────────────────────────────────────────────────────────────────────
void setup()
{
  Serial.begin(921600);

  pinMode(FAN_PIN, OUTPUT);
  FastLED.addLeds<WS2812B, LED_ONBOARD_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(50);

  i2s_install();
  i2s_setpin();
  i2s_start(I2S_PORT);

  WiFi.begin(ssid, password);

  int soLanThu = 0;
  while (WiFi.status() != WL_CONNECTED && soLanThu < 20)
  {
    delay(500);
    soLanThu++;
  }

  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.print("IP ESP32: ");
    Serial.println(WiFi.localIP());
    leds[0] = CRGB::Blue;
    FastLED.show();
    delay(500);
    server.on("/", handleRoot);
    server.on("/tang", []()
              { capDoHienTai++; if(capDoHienTai>3) capDoHienTai=0; capNhatTrangThai(); });
    server.on("/tat", []()
              { capDoHienTai=0; capNhatTrangThai(); });
    server.begin();
  }
  else
  {
    leds[0] = CRGB::Red;
    FastLED.show();
    delay(500);
  }

  capNhatTrangThai();

  // Kết nối WebSocket tới Python server
  webSocket.begin(SERVER_IP, SERVER_PORT, "/audio");
  webSocket.onEvent(onWebSocketEvent);
  webSocket.setReconnectInterval(3000);

  // ── Tạo Queue và các FreeRTOS Task ──────────────────────────────────────────
  audioQueue = xQueueCreate(QUEUE_SIZE, sizeof(AudioPacket));

  xTaskCreatePinnedToCore(
      audioTask,    // hàm task
      "AudioTask",  // tên (debug)
      4096,         // stack size (bytes)
      NULL,         // tham số truyền vào
      2,            // priority (cao hơn networkTask)
      NULL,         // task handle (không cần)
      0             // Core 0
  );

  xTaskCreatePinnedToCore(
      networkTask,
      "NetworkTask",
      8192,         // stack lớn hơn vì WebSocket dùng nhiều bộ nhớ
      NULL,
      1,            // priority thấp hơn
      NULL,
      1             // Core 1
  );
}

// ─── Loop trống — mọi việc do Task xử lý ─────────────────────────────────────
void loop()
{
  vTaskDelay(portMAX_DELAY); // nhường hoàn toàn cho FreeRTOS
}
