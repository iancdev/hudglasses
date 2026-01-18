#include <Arduino.h>
#include <driver/i2s.h>

// ========= Pin preset =========
// Set to 1 for the known-working demo wiring (WS=2, SCK=1, SD=41).
// Set to 0 for the alternate wiring (WS=5, SCK=4, SD=6).
#define USE_DEMO_PINS 1

#if USE_DEMO_PINS
#define PIN_I2S_WS 2
#define PIN_I2S_SCK 1
#define PIN_I2S_SD 41
#else
#define PIN_I2S_WS 5
#define PIN_I2S_SCK 4
#define PIN_I2S_SD 6
#endif

// ========= Mic power (recommended: wire INMP441 VDD to board 3V3) =========
// If you insist on powering the mic from a GPIO, set this to that GPIO number.
// Leave as -1 to indicate external 3V3.
#define MIC_POWER_PIN -1

// Optional: wire mic VDD to an ADC-capable GPIO to print the measured millivolts at boot.
// Use a separate pin from MIC_POWER_PIN.
#define MIC_VDD_SENSE_PIN -1

// ========= Audio settings =========
#define SAMPLE_RATE_HZ 16000
#define BUFFER_SAMPLES 512

static int32_t g_i2s_buf[BUFFER_SAMPLES];
static bool g_use_right_channel = false;  // Start with LEFT
static bool g_tried_channel_swap = false;

static void printPinSanity() {
  pinMode(PIN_I2S_SD, INPUT_PULLUP);
  delay(10);
  int pullup = digitalRead(PIN_I2S_SD);

  pinMode(PIN_I2S_SD, INPUT_PULLDOWN);
  delay(10);
  int pulldown = digitalRead(PIN_I2S_SD);

  pinMode(PIN_I2S_SD, INPUT);
  Serial.printf("SD pin sanity (before I2S): pullup=%d pulldown=%d\n", pullup, pulldown);
  if (pullup == 0 && pulldown == 0) {
    Serial.println("SD pin looks stuck LOW (short to GND or external pull-down).");
  } else if (pullup == 1 && pulldown == 1) {
    Serial.println("SD pin looks stuck HIGH (short to 3V3 or external pull-up).");
  } else {
    Serial.println("SD pin looks floating/normal.");
  }
}

static void setupMicPower() {
  if (MIC_POWER_PIN >= 0) {
    pinMode(MIC_POWER_PIN, OUTPUT);
    digitalWrite(MIC_POWER_PIN, HIGH);
    delay(50);
    Serial.printf("Mic power: GPIO%d = HIGH\n", MIC_POWER_PIN);
  } else {
    Serial.println("Mic power: external 3V3 (MIC_POWER_PIN=-1)");
  }

  if (MIC_VDD_SENSE_PIN >= 0) {
    if (MIC_VDD_SENSE_PIN == MIC_POWER_PIN && MIC_POWER_PIN >= 0) {
      Serial.println("Mic VDD sense: ERROR (sense pin equals power pin). Use a separate ADC GPIO.");
      return;
    }
    delay(20);
    uint32_t mv = 0;
    const int samples = 8;
    for (int i = 0; i < samples; i++) {
      mv += (uint32_t)analogReadMilliVolts(MIC_VDD_SENSE_PIN);
      delay(5);
    }
    mv /= (uint32_t)samples;
    Serial.printf("Mic VDD sense: %u mV (requires VDD wired to MIC_VDD_SENSE_PIN)\n", (unsigned)mv);
  }
}

static esp_err_t initI2S(bool use_right_channel) {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE_HZ;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = use_right_channel ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = BUFFER_SAMPLES;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_I2S_SCK;
  pins.ws_io_num = PIN_I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = PIN_I2S_SD;

  esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
  if (err != ESP_OK) {
    Serial.printf("ERROR: i2s_driver_install err=%d\n", (int)err);
    return err;
  }

  err = i2s_set_pin(I2S_NUM_0, &pins);
  if (err != ESP_OK) {
    Serial.printf("ERROR: i2s_set_pin err=%d\n", (int)err);
    return err;
  }

  err = i2s_set_clk(I2S_NUM_0, SAMPLE_RATE_HZ, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_MONO);
  if (err != ESP_OK) {
    Serial.printf("ERROR: i2s_set_clk err=%d\n", (int)err);
    return err;
  }

  i2s_zero_dma_buffer(I2S_NUM_0);
  return ESP_OK;
}

static void reinitI2S(bool use_right_channel) {
  i2s_driver_uninstall(I2S_NUM_0);
  delay(50);
  esp_err_t err = initI2S(use_right_channel);
  Serial.printf("I2S reinit channel=%s err=%d\n", use_right_channel ? "RIGHT" : "LEFT", (int)err);
}

static void printI2SStats(const int32_t* data, int len) {
  int nonzero = 0;
  int32_t minv = INT32_MAX;
  int32_t maxv = INT32_MIN;
  for (int i = 0; i < len; i++) {
    int32_t v = data[i];
    if (v != 0) nonzero++;
    if (v < minv) minv = v;
    if (v > maxv) maxv = v;
  }

  Serial.printf(
      "I2S samples=%d nonzero=%d min=%ld max=%ld first=%08lx %08lx %08lx %08lx\n",
      len,
      nonzero,
      (long)minv,
      (long)maxv,
      (unsigned long)(len > 0 ? (uint32_t)data[0] : 0),
      (unsigned long)(len > 1 ? (uint32_t)data[1] : 0),
      (unsigned long)(len > 2 ? (uint32_t)data[2] : 0),
      (unsigned long)(len > 3 ? (uint32_t)data[3] : 0));

  if (nonzero == 0) {
    Serial.println("All raw samples are 0. Likely mic is unpowered, SD pin wrong/disconnected, or BCLK/WS not reaching mic.");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println("ESP32 INMP441 I2S sanity check");
  Serial.printf("Pins: WS=%d SCK=%d SD=%d\n", PIN_I2S_WS, PIN_I2S_SCK, PIN_I2S_SD);
  Serial.printf("Channel: %s\n", g_use_right_channel ? "RIGHT" : "LEFT");

  setupMicPower();
  printPinSanity();

  esp_err_t err = initI2S(g_use_right_channel);
  Serial.printf("I2S init err=%d\n", (int)err);
}

void loop() {
  size_t bytes_read = 0;
  esp_err_t err = i2s_read(I2S_NUM_0, (void*)g_i2s_buf, sizeof(g_i2s_buf), &bytes_read, pdMS_TO_TICKS(200));
  if (err != ESP_OK) {
    Serial.printf("i2s_read err=%d\n", (int)err);
    delay(500);
    return;
  }

  int samples = (int)(bytes_read / sizeof(int32_t));
  static uint32_t counter = 0;
  if (++counter % 10 == 0) {
    printI2SStats(g_i2s_buf, samples);

    // Try the other channel once if we're stuck at zeros.
    int nonzero = 0;
    for (int i = 0; i < samples; i++) {
      if (g_i2s_buf[i] != 0) {
        nonzero = 1;
        break;
      }
    }
    if (nonzero == 0 && !g_tried_channel_swap) {
      g_tried_channel_swap = true;
      g_use_right_channel = !g_use_right_channel;
      Serial.printf("Trying channel swap -> %s\n", g_use_right_channel ? "RIGHT" : "LEFT");
      reinitI2S(g_use_right_channel);
    }
  }
}
