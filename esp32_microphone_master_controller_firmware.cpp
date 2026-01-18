#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/i2s.h>

// I2S Microphone Pins (INMP441)
// Using safe GPIO pins that avoid flash (47-48), PSRAM (33-37), USB (19-20), and strapping pins
// Set to 1 to use the original "demo" wiring (WS=2, SCK=1, SD=41).
// Set to 0 to use the alternate wiring used by this file previously (WS=5, SCK=4, SD=6).
#define USE_DEMO_PINS 1

#if USE_DEMO_PINS
#define I2S0_WS 2   // Word Select (LRCLK)
#define I2S0_SCK 1  // Bit Clock (BCLK)
#define I2S0_SD 41  // Serial Data (DOUT from mic -> DIN on ESP32)
#else
#define I2S0_WS 5   // Word Select (LRCLK)
#define I2S0_SCK 4  // Bit Clock (BCLK)
#define I2S0_SD 6   // Serial Data (DOUT from mic -> DIN on ESP32)
#endif

#define VDD1 7  // Power pin (optional)

// If you power the microphone from GPIO (not recommended long-term), set this to true.
// Prefer wiring INMP441 VDD directly to the board's 3V3 pin.
#define MIC_POWER_FROM_GPIO false

// Optional: wire INMP441 VDD to an ADC-capable GPIO to measure/print VDD in firmware.
// Use a separate pin from VDD1 (don't try to power + measure from the same GPIO).
// Example (ESP32-S3): set to 8 and wire mic VDD -> GPIO8 as a sense lead.
#define MIC_VDD_SENSE_PIN -1

// Set to true if your INMP441 L/R pin is connected to 3.3V (right channel)
// Set to false if L/R is connected to GND (left channel)
#define DEFAULT_USE_RIGHT_CHANNEL false

// Some boards use GPIO48 for flash/other critical functions. Leave this off unless you know you need it.
#define FORCE_GPIO48_LOW false

// WiFi credentials - CHANGE THESE!
const char* ssid = "nwHacks2026";
const char* password = "nw_Hacks_2026";

// UDP settings
const char* udpAddress = "10.19.134.79";  // CHANGE to your computer's IP
const int udpPort = 12345;

WiFiUDP udp;

// I2S configuration
#define SAMPLE_RATE 16000
#define BUFFER_SIZE 512
int32_t i2s_buffer[BUFFER_SIZE];
uint8_t udp_buffer[BUFFER_SIZE * 2];  // 16-bit samples

static bool g_use_right_channel = DEFAULT_USE_RIGHT_CHANNEL;

static void setupI2S(bool use_right_channel) {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = use_right_channel ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = BUFFER_SIZE,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S0_SCK,
    .ws_io_num = I2S0_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S0_SD
  };

  esp_err_t err = i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
  if (err != ESP_OK) {
    Serial.print("ERROR: i2s_driver_install failed: ");
    Serial.println(err);
  }

  err = i2s_set_pin(I2S_NUM_0, &pin_config);
  if (err != ESP_OK) {
    Serial.print("ERROR: i2s_set_pin failed: ");
    Serial.println(err);
  }

  err = i2s_set_clk(I2S_NUM_0, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_MONO);
  if (err != ESP_OK) {
    Serial.print("ERROR: i2s_set_clk failed: ");
    Serial.println(err);
  }

  i2s_zero_dma_buffer(I2S_NUM_0);
  
  Serial.print("I2S initialized - Channel: ");
  Serial.println(use_right_channel ? "RIGHT" : "LEFT");
}

static void reinitI2S(bool use_right_channel) {
  i2s_driver_uninstall(I2S_NUM_0);
  delay(20);
  setupI2S(use_right_channel);
}

static int countNonZero(const int32_t* data, int len) {
  int count = 0;
  for (int i = 0; i < len; i++) {
    if (data[i] != 0) count++;
  }
  return count;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  if (FORCE_GPIO48_LOW) {
    pinMode(48, OUTPUT);
    digitalWrite(48, LOW);
    Serial.println("GPIO48 forced LOW (FORCE_GPIO48_LOW=true)");
  }
  
  Serial.println("ESP32 I2S Audio UDP Streamer");
  Serial.println("============================");
  
  // Optional: Set VDD1 as power output for microphone
  if (MIC_POWER_FROM_GPIO) {
    pinMode(VDD1, OUTPUT);
    digitalWrite(VDD1, HIGH);
    Serial.print("Mic power: GPIO ");
    Serial.print(VDD1);
    Serial.println(" = HIGH");
  } else {
    Serial.println("Mic power: external 3V3 (MIC_POWER_FROM_GPIO=false)");
  }
  delay(100);

  if (MIC_VDD_SENSE_PIN >= 0) {
    if (MIC_VDD_SENSE_PIN == VDD1 && MIC_POWER_FROM_GPIO) {
      Serial.println("Mic VDD sense: ERROR (sense pin equals power pin). Use a separate ADC GPIO.");
    } else {
      delay(20);
      uint32_t mv = 0;
      const int samples = 8;
      for (int i = 0; i < samples; i++) {
        mv += (uint32_t)analogReadMilliVolts(MIC_VDD_SENSE_PIN);
        delay(5);
      }
      mv /= (uint32_t)samples;
      Serial.print("Mic VDD sense: ");
      Serial.print(mv);
      Serial.println(" mV (requires VDD wired to MIC_VDD_SENSE_PIN)");
    }
  }
  
  // Diagnostics
  Serial.println("\n--- Pin Configuration ---");
  Serial.print("I2S WS  (LRCLK): GPIO ");
  Serial.println(I2S0_WS);
  Serial.print("I2S SCK (BCLK):  GPIO ");
  Serial.println(I2S0_SCK);
  Serial.print("I2S SD  (DIN):   GPIO ");
  Serial.println(I2S0_SD);
  Serial.print("VDD Power:       GPIO ");
  Serial.println(VDD1);
  Serial.println("\n⚠️  CRITICAL: INMP441 L/R pin MUST be connected to GND (or 3.3V for right channel)");
  Serial.println("⚠️  If you see all 0x00 samples, check: mic VDD, GND, SD wiring, and L/R channel selection.");
  delay(100);
  
  // Connect to WiFi
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\nWiFi connected!");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
  Serial.print("Streaming to: ");
  Serial.print(udpAddress);
  Serial.print(":");
  Serial.println(udpPort);
  
  // Initialize I2S
  Serial.println("Initializing I2S...");
  setupI2S(g_use_right_channel);

  // Quick self-test to help diagnose "all zeros" issues.
  // If you power from GPIO, we can power-cycle and see if raw samples change.
  if (MIC_POWER_FROM_GPIO) {
    Serial.println("Mic power self-test (GPIO power): sampling with VDD LOW then HIGH...");
    digitalWrite(VDD1, LOW);
    delay(150);
    size_t bytes_read_low = 0;
    (void)i2s_read(I2S_NUM_0, i2s_buffer, BUFFER_SIZE * sizeof(int32_t), &bytes_read_low, pdMS_TO_TICKS(200));
    int samples_low = (int)(bytes_read_low / sizeof(int32_t));
    int nz_low = countNonZero(i2s_buffer, samples_low);
    Serial.print("  VDD LOW  nonzero=");
    Serial.print(nz_low);
    Serial.print(" samples=");
    Serial.println(samples_low);

    digitalWrite(VDD1, HIGH);
    delay(150);
    size_t bytes_read_high = 0;
    (void)i2s_read(I2S_NUM_0, i2s_buffer, BUFFER_SIZE * sizeof(int32_t), &bytes_read_high, pdMS_TO_TICKS(200));
    int samples_high = (int)(bytes_read_high / sizeof(int32_t));
    int nz_high = countNonZero(i2s_buffer, samples_high);
    Serial.print("  VDD HIGH nonzero=");
    Serial.print(nz_high);
    Serial.print(" samples=");
    Serial.println(samples_high);

    if (nz_low == 0 && nz_high == 0) {
      Serial.println("  Result: still all zeros. Likely VDD pin not actually powering mic, SD wiring wrong, or L/R mismatch.");
    } else if (nz_low == 0 && nz_high > 0) {
      Serial.println("  Result: power GPIO is working (mic wakes up when VDD is HIGH).");
    } else if (nz_low > 0 && nz_high > 0) {
      Serial.println("  Result: mic seems powered regardless of VDD pin (maybe wired to 3V3).");
    }
  }
  
  Serial.println("Starting audio stream...");
  Serial.print("Sample Rate: ");
  Serial.print(SAMPLE_RATE);
  Serial.println(" Hz");
}

void loop() {
  size_t bytes_read = 0;
  // Read from I2S
  esp_err_t result = i2s_read(I2S_NUM_0, i2s_buffer, BUFFER_SIZE * sizeof(int32_t), &bytes_read, portMAX_DELAY);
  if (result != ESP_OK) {
    static uint32_t err_count = 0;
    if (++err_count % 50 == 0) {
      Serial.print("ERROR: i2s_read failed: ");
      Serial.println(result);
    }
    return;
  }

  if (bytes_read > 0) {
    int samples_read = bytes_read / sizeof(int32_t);
    
    // Convert 32-bit samples to 16-bit for UDP transmission
    for (int i = 0; i < samples_read; i++) {
      // INMP441 outputs 24-bit data in upper bits of 32-bit word
      // Common Arduino/ESP32 INMP441 wiring expects a >>14 shift for PCM16.
      int16_t sample = (int16_t)(i2s_buffer[i] >> 14);
      udp_buffer[i * 2] = sample & 0xFF;
      udp_buffer[i * 2 + 1] = (sample >> 8) & 0xFF;
    }
    
    // Send via UDP
    udp.beginPacket(udpAddress, udpPort);
    udp.write(udp_buffer, samples_read * 2);
    udp.endPacket();
    
    // Debug: Print raw I2S values and converted samples every 100 packets
    static int packet_count = 0;
    static bool tried_channel_swap = false;
    if (++packet_count >= 100) {
      int32_t raw_min = INT32_MAX;
      int32_t raw_max = INT32_MIN;
      int raw_nonzero = 0;
      for (int i = 0; i < samples_read; i++) {
        int32_t v = i2s_buffer[i];
        if (v != 0) raw_nonzero++;
        if (v < raw_min) raw_min = v;
        if (v > raw_max) raw_max = v;
      }

      Serial.print("Streaming... samples: ");
      Serial.print(samples_read);
      Serial.print(" | RawNonZero: ");
      Serial.print(raw_nonzero);
      Serial.print(" | RawMin: ");
      Serial.print(raw_min);
      Serial.print(" | RawMax: ");
      Serial.print(raw_max);
      Serial.print(" | Raw I2S[0-4]: ");
      for(int i = 0; i < 5 && i < samples_read; i++) {
        Serial.print(i2s_buffer[i], HEX);
        Serial.print(" ");
      }
      Serial.print(" | Converted[0]: ");
      int16_t sample0 = (int16_t)(i2s_buffer[0] >> 14);
      Serial.println(sample0);
      if (raw_nonzero == 0) {
        Serial.println("WARNING: All raw samples are 0. Likely mic is unpowered, SD pin is wrong/disconnected, or L/R channel mismatch.");
        if (!tried_channel_swap) {
          tried_channel_swap = true;
          g_use_right_channel = !g_use_right_channel;
          Serial.print("Attempting channel swap. New channel: ");
          Serial.println(g_use_right_channel ? "RIGHT" : "LEFT");
          reinitI2S(g_use_right_channel);
        }
      }
      packet_count = 0;
    }
  }
}
