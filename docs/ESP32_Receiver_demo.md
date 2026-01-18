"""
INMP441 Audio Stream Receiver
Receives real-time audio from ESP32, plays it through speakers, and optionally saves to file
"""

import socket
import pyaudio
import struct
import numpy as np
import wave
import argparse
from datetime import datetime
import sys

# Configuration (must match ESP32 settings)
UDP_PORT = 12345
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512

def main():
    parser = argparse.ArgumentParser(description='Receive and play audio from ESP32')
    parser.add_argument('--save', action='store_true', help='Save audio to WAV file')
    parser.add_argument('--output', type=str, help='Output filename (default: audio_TIMESTAMP.wav)')
    parser.add_argument('--no-play', action='store_true', help='Disable audio playback')
    parser.add_argument('--show-levels', action='store_true', help='Show audio level meter')
    args = parser.parse_args()

    # Generate output filename if saving
    output_filename = None
    wav_file = None
    if args.save:
        if args.output:
            output_filename = args.output
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"audio_{timestamp}.wav"
        
        # Create WAV file
        wav_file = wave.open(output_filename, 'wb')
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)  # 16-bit = 2 bytes
        wav_file.setframerate(SAMPLE_RATE)
        print(f"Recording to: {output_filename}")

    # Audio setup for playback
    stream = None
    if not args.no_play:
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        output=True,
                        frames_per_buffer=CHUNK_SIZE)

    # Setup UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', UDP_PORT))
    sock.settimeout(5.0)

    print(f"Listening for audio on port {UDP_PORT}...")
    print(f"Sample Rate: {SAMPLE_RATE} Hz")
    print(f"Playback: {'Enabled' if not args.no_play else 'Disabled'}")
    print(f"Recording: {'Enabled' if args.save else 'Disabled'}")
    print("Press Ctrl+C to stop\n")

    packet_count = 0
    total_samples = 0
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                
                if packet_count == 0:
                    print(f"Connected to ESP32: {addr[0]}:{addr[1]}\n")
                
                # Play the received audio
                if stream:
                    stream.write(data)
                
                # Save to file
                if wav_file:
                    wav_file.writeframes(data)
                
                # Convert to numpy array for analysis
                samples = np.frombuffer(data, dtype=np.int16)
                total_samples += len(samples)
                
                # Show audio level meter
                if args.show_levels:
                    level = np.abs(samples).mean()
                    max_level = np.abs(samples).max()
                    bars = int(level / 1000)
                    meter = '█' * bars + '░' * (50 - bars)
                    print(f"\rLevel: {meter} | Avg: {level:6.0f} | Peak: {max_level:6d}", end='')
                    sys.stdout.flush()
                
                packet_count += 1
                if packet_count % 100 == 0 and not args.show_levels:
                    # Show activity every ~3 seconds
                    duration = total_samples / SAMPLE_RATE
                    print(f"Packets: {packet_count:6d} | Duration: {duration:6.1f}s | Last packet: {len(samples)} samples")
                    
            except socket.timeout:
                if not args.show_levels:
                    print("No data received for 5 seconds... waiting")
                packet_count = 0
                
    except KeyboardInterrupt:
        print("\n\nStopping...")

    finally:
        if stream:
            stream.stop_stream()
            stream.close()
            p.terminate()
        
        if wav_file:
            wav_file.close()
            duration = total_samples / SAMPLE_RATE
            print(f"\nRecording saved: {output_filename}")
            print(f"Duration: {duration:.1f} seconds ({total_samples} samples)")
        
        sock.close()
        print("Audio stream closed.")

if __name__ == "__main__":
    main()
