# I2S Mic Sanity Check (INMP441)

Minimal ESP32 sketch to answer one question: **are we getting any non-zero I2S samples from the mic?**

It prints:
- Pin sanity (`SD` pull-up vs pull-down) before enabling I2S
- I2S stats: `nonzero/min/max/first samples`
- Automatically tries LEFT then RIGHT channel once (L/R pin mismatch is common)

## Configure
Edit `i2s_mic_sanity_check.ino`:
- `USE_DEMO_PINS` to match your wiring
- `MIC_POWER_PIN` (recommended `-1` and wire mic `VDD` to `3V3`)
- Optional: `MIC_VDD_SENSE_PIN` (wire mic `VDD` to an ADC-capable GPIO to print millivolts)

## Run
- Open this folder in Arduino IDE / PlatformIO and flash.
- Open Serial Monitor at `115200`.

## Interpreting results
- `nonzero=0` on both channels:
  - Mic not powered, SD not connected, BCLK/WS not reaching the mic, SD shorted low, or a dead mic module.
- `nonzero>0`:
  - The mic is alive; any “silence” is likely downstream (shift/scaling, UDP receiver, etc.).

