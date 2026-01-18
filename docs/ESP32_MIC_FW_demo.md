#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/i2s.h>

// ========== WiFi Configuration ==========
const char* ssid = "nwHacks2026";        // Change this to your WiFi name
const char* password = "nw_Hacks_2026"; // Change this to your WiFi password
const char* laptopIP = "10.19.134.79";     // Change this to your laptop's IP address
const int udpPort = 12345;                   // UDP port for streaming

// ========== I2S Configuration for INMP441 ==========
#define I2S_WS    2    // Word Select (LRCLK) - Connect to WS on INMP441
#define I2S_SCK   1    // Bit Clock (BCLK) - Connect to SCK on INMP441
#define I2S_SD    41    // Serial Data (DIN) - Connect to SD on INMP441

#define I2S_PORT I2S_NUM_0
#define SAMPLE_RATE 16000       // 16kHz sample rate (good for voice)
#define BUFFER_SIZE 512         // Number of samples to read at once
#define BYTES_PER_SAMPLE 4      // 32-bit samples from I2S
#define UDP_PACKET_SIZE 1024    // UDP packet size

// ========== Audio Processing Configuration ==========
#define HPF_ALPHA 0.99        // High-pass filter coefficient (0.95-0.99)
#define AGC_TARGET 8000         // Target RMS level for normalization
#define AGC_ATTACK 0.01         // How fast gain increases
#define AGC_RELEASE 0.001       // How fast gain decreases
#define MIN_GAIN 0.5            // Minimum gain multiplier
#define MAX_GAIN 8.0            // Maximum gain multiplier

WiFiUDP udp;
int32_t i2s_read_buff[BUFFER_SIZE];
int16_t audio_buffer[BUFFER_SIZE]; // Converted to 16-bit for streaming

// High-pass filter state
float hpf_prev_input = 0;
float hpf_prev_output = 0;

// AGC state
float current_gain = 1.0;

void setupI2S() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = BUFFER_SIZE,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  i2s_set_clk(I2S_PORT, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_MONO);
}

// High-pass filter to remove DC offset
float highPassFilter(float input) {
  float output = HPF_ALPHA * (hpf_prev_output + input - hpf_prev_input);
  hpf_prev_input = input;
  hpf_prev_output = output;
  return output;
}

// Automatic gain control for normalization
void applyAGC(int16_t* buffer, int length) {
  // Calculate RMS level of current buffer
  float sum_squares = 0;
  for (int i = 0; i < length; i++) {
    float sample = buffer[i];
    sum_squares += sample * sample;
  }
  float rms = sqrt(sum_squares / length);
  
  // Adjust gain based on RMS level
  if (rms > 0) {
    float target_gain = AGC_TARGET / rms;
    
    // Smooth gain changes
    if (target_gain > current_gain) {
      current_gain += (target_gain - current_gain) * AGC_ATTACK;
    } else {
      current_gain += (target_gain - current_gain) * AGC_RELEASE;
    }
    
    // Clamp gain to safe range
    if (current_gain < MIN_GAIN) current_gain = MIN_GAIN;
    if (current_gain > MAX_GAIN) current_gain = MAX_GAIN;
  }
  
  // Apply gain to buffer
  for (int i = 0; i < length; i++) {
    int32_t sample = (int32_t)(buffer[i] * current_gain);
    // Clipping protection
    if (sample > 32767) sample = 32767;
    if (sample < -32768) sample = -32768;
    buffer[i] = (int16_t)sample;
  }
}

void setupWiFi() {
  Serial.println("Connecting to WiFi...");
  WiFi.begin(ssid, password);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("ESP32 IP address: ");
    Serial.println(WiFi.localIP());
    Serial.print("Streaming to: ");
    Serial.print(laptopIP);
    Serial.print(":");
    Serial.println(udpPort);
  } else {
    Serial.println("\nFailed to connect to WiFi!");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("INMP441 Audio Streamer Starting...");
  
  setupWiFi();
  setupI2S();
  
  Serial.println("System ready! Starting audio stream...");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected! Reconnecting...");
    setupWiFi();
    delay(1000);
    return;
  }

  size_t bytes_read = 0;
  
  // Read audio data from I2S microphone
  esp_err_t result = i2s_read(I2S_PORT, &i2s_read_buff, 
                               BUFFER_SIZE * BYTES_PER_SAMPLE, 
                               &bytes_read, portMAX_DELAY);
  
  if (result == ESP_OK && bytes_read > 0) {
    int samples_read = bytes_read / BYTES_PER_SAMPLE;
    
    // Convert 32-bit samples to 16-bit and apply high-pass filter
    for (int i = 0; i < samples_read; i++) {
      // The INMP441 outputs data in the upper bits of the 32-bit word
      int16_t raw_sample = (i2s_read_buff[i] >> 14);
      
      // Apply high-pass filter to remove DC offset
      float filtered = highPassFilter((float)raw_sample);
      audio_buffer[i] = (int16_t)filtered;
    }
    
    // Apply automatic gain control
    applyAGC(audio_buffer, samples_read);
    
    // Send audio data via UDP
    udp.beginPacket(laptopIP, udpPort);
    udp.write((uint8_t*)audio_buffer, samples_read * sizeof(int16_t));
    udp.endPacket();
    
    // Optional: Print status every ~1 second (at 16kHz, ~31 buffers/sec)
    static int counter = 0;
    if (++counter >= 31) {
      Serial.print("Streaming... Samples: ");
      Serial.print(samples_read);
      Serial.print(" | Gain: ");
      Serial.print(current_gain, 2);
      Serial.print(" | WiFi: ");
      Serial.print(WiFi.RSSI());
      Serial.println(" dBm");
      counter = 0;
    }
  }
}