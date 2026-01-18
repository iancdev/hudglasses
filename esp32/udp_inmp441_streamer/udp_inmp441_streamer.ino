#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/i2s.h>

// ========= Project settings (edit these) =========
static const char* WIFI_SSID = "nwHacks2026";
static const char* WIFI_PASSWORD = "nw_Hacks_2026";
static const char* LAPTOP_IP = "10.19.130.231";

// Flash TWO boards with different values:
// - Left:  ROLE="left",  DEVICE_ID="esp32-left-01",  UDP_PORT=12345
// - Right: ROLE="right", DEVICE_ID="esp32-right-01", UDP_PORT=12346
static const char* ROLE = "left";
static const char* DEVICE_ID = "esp32-left-01";
static const int UDP_PORT = 12345;

// ========= INMP441 wiring (adjust to your board) =========
#define I2S_WS 2   // LRCLK / WS
#define I2S_SCK 1  // BCLK
#define I2S_SD 41  // DOUT

// ========= Audio format (match server expectation) =========
#define SAMPLE_RATE_HZ 16000
#define FRAME_MS 20
#define SAMPLES_PER_FRAME ((SAMPLE_RATE_HZ * FRAME_MS) / 1000)  // 320
#define BYTES_PER_SAMPLE_IN 4                                   // 32-bit I2S
#define BYTES_PER_SAMPLE_OUT 2                                  // 16-bit PCM

// IMPORTANT: Disable AGC by default. We use relative loudness left vs right for direction.
#define ENABLE_AGC 0

// Optional DC high-pass filter
#define ENABLE_HPF 1
#define HPF_ALPHA 0.99f

WiFiUDP udp;
static int32_t i2s_read_buf[SAMPLES_PER_FRAME];
static int16_t pcm16_buf[SAMPLES_PER_FRAME];

// HPF state
static float hpf_prev_in = 0.0f;
static float hpf_prev_out = 0.0f;

static float highPass(float x) {
  float y = HPF_ALPHA * (hpf_prev_out + x - hpf_prev_in);
  hpf_prev_in = x;
  hpf_prev_out = y;
  return y;
}

static void setupWiFi() {
  Serial.printf("WiFi: connecting to %s...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(250);
    Serial.print(".");
    attempts++;
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi: failed to connect");
    return;
  }
  Serial.printf("WiFi: connected ip=%s rssi=%d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  Serial.printf("UDP: streaming role=%s deviceId=%s -> %s:%d\n", ROLE, DEVICE_ID, LAPTOP_IP, UDP_PORT);
}

static void setupI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;  // INMP441 is mono
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = SAMPLES_PER_FRAME;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_SCK;
  pins.ws_io_num = I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = I2S_SD;

  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pins);
  i2s_set_clk(I2S_NUM_0, SAMPLE_RATE_HZ, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_MONO);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("ESP32 UDP INMP441 streamer starting...");
  setupWiFi();
  setupI2S();
  Serial.printf("Audio: %dHz %dms (%d samples, %d bytes)\n", SAMPLE_RATE_HZ, FRAME_MS, SAMPLES_PER_FRAME, SAMPLES_PER_FRAME * BYTES_PER_SAMPLE_OUT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    setupWiFi();
    delay(500);
    return;
  }

  size_t bytes_read = 0;
  esp_err_t ok = i2s_read(I2S_NUM_0, (void*)i2s_read_buf, sizeof(i2s_read_buf), &bytes_read, portMAX_DELAY);
  if (ok != ESP_OK || bytes_read == 0) {
    return;
  }

  int samples_read = bytes_read / BYTES_PER_SAMPLE_IN;
  if (samples_read > SAMPLES_PER_FRAME) samples_read = SAMPLES_PER_FRAME;

  // Convert 32-bit I2S sample to 16-bit PCM.
  // INMP441 data is left-aligned within the 32-bit word; the shift may vary by wiring/module.
  for (int i = 0; i < samples_read; i++) {
    int16_t s = (int16_t)(i2s_read_buf[i] >> 14);
    if (ENABLE_HPF) {
      s = (int16_t)highPass((float)s);
    }
    pcm16_buf[i] = s;
  }

  // Stream raw PCM16 mono over UDP.
  udp.beginPacket(LAPTOP_IP, UDP_PORT);
  udp.write((uint8_t*)pcm16_buf, samples_read * sizeof(int16_t));
  udp.endPacket();

  // Periodic debug.
  static uint32_t counter = 0;
  if (++counter % 50 == 0) {
    Serial.printf("Streaming... samples=%d rssi=%d dBm\n", samples_read, WiFi.RSSI());
  }
}

