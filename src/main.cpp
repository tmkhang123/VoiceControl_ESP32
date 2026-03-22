#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <FastLED.h>
#include <driver/i2s.h>

// const char* ssid = "Wutdaheo";
// const char* password = "AlwaysRed";

const char *ssid = "YOUR_WIFI_NAME";
const char *password = "YOUR_WIFI_PASSWORD";

#define LED_ONBOARD_PIN 48
#define FAN_PIN 4
#define I2S_WS 11
#define I2S_SD 10
#define I2S_SCK 12
#define I2S_PORT I2S_NUM_0

#define NUM_LEDS 1
CRGB leds[NUM_LEDS];
WebServer server(80);

int capDoHienTai = 0;

#define bufferLen 64
#define SEND_BUFFER_SIZE 512
static int16_t sendBuf[SEND_BUFFER_SIZE];
static int sendBufIdx = 0;

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
}

void loop()
{
  if (WiFi.status() == WL_CONNECTED)
  {
    server.handleClient();
  }

  int32_t sBuffer[bufferLen];
  size_t bytesIn = 0;
  esp_err_t result = i2s_read(I2S_PORT, &sBuffer, bufferLen * 4, &bytesIn, 0);

  if (result == ESP_OK && bytesIn > 0)
  {
    int samples = bytesIn / 4;

    for (int i = 0; i < samples; i++)
    {
      sendBuf[sendBufIdx++] = (int16_t)(sBuffer[i] >> 16);

      if (sendBufIdx >= SEND_BUFFER_SIZE)
      {
        uint8_t header[2] = {0xAA, 0x55};
        Serial.write(header, 2);
        Serial.write((uint8_t *)sendBuf, SEND_BUFFER_SIZE * 2);
        sendBufIdx = 0;
      }
    }
  }
}