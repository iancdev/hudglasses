# HUD Glasses for the Deaf
The HUD Glasses project is a production ready application for Android, designed to work for Viture Pro XR Glasses, using its SDKs, to provide speech to text in the form of subtitles, radar using microphone to pinpoint speaker location, and ElevenLabs API for the speech to text service. 

Refer to ``Draft.md`` 

## Tech Stack
Creating a native Android applicatoin using Koitlin, and Viture SDK, we can interact with the glasses natively.
- Viture SDK
- Native Android Koitlin
- ESP32 (2 microphones connects to 2 seperate ESP32, which becomes the data source for directional audio detection and speech to text)
- The on-device android microphone can also be used to combine audio sources for speech recognition. We should pick one that is best for our use case, but should be a selector for now.
- ElevenLabs API for speech to text (live)
Refer to ``/VitureSDK`` for what API is available

## Setup
- Viture Pro XR glasses displays the HUD, connects to an Android device to display the HUD
- The Android device displays a black background in horizontal orientation to ensure HUD display shows up on the glasses like AR. The android device's function is only to display what would be shown on the glasses.
- Using the Viture SDK, the application sends a command on initialization to turn off the electrochromic lens.
- The target device is Pixel 8a.
- The user wears a wristband that emit haptic feedback to show which direction detected audio is coming from


## Detected Audios
- Speaking
- Fire Alarm
- Car Horn

## Detected Audio Behavior
- Speaking would activate haptics, and show on the radar, and uses speech to text using ElevenLabs API to show who is speaking, and what they're saying
- Fire Alarm would cause the HUD to glow RED and warn the user that there is a current fire alarm. After 10 seconds of no longer detecting the fire alarm audio, the HUD returns to normal. The radar shows detected location here as well.
- Car horn, the Radar would show yellow for which direction the car horn is coming from.

## HUD behavior
The HUD proactively, and passively detects for speech, and activates the ElevenLabs API for speech to text when audio is detected.
Using the head tracking API from Viture SDK to show the accurate relative location of where the audio is coming from. The edges of the screen glows based on where the audio is coming from.
