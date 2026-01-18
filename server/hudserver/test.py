import asyncio
import websockets

# --- CONFIGURATION ---
# REPLACE THIS with the IP address printed in your Arduino Serial Monitor
ESP_IP = "192.168.154.105" 
PORT = 81
URI = f"ws://{ESP_IP}:{PORT}"

async def communicate():
    print(f"Attempting to connect to {URI}...")
    try:
        async with websockets.connect(URI) as websocket:
            print(f"Successfully connected to ESP32!")
            print("------------------------------------------------")
            print("Type 'q' to quit.")
            print("Type 's' to STOP motor immediately.")
            print("Otherwise, follow prompts for Duration/Intensity.")
            print("------------------------------------------------")

            while True:
                cmd = input("\nEnter Duration (ms) or command: ").strip().lower()

                # 1. Handle Quit
                if cmd == 'q':
                    print("Closing connection...")
                    break
                
                # 2. Handle Emergency Stop
                if cmd == 's':
                    await websocket.send("0,0")
                    print("Sent STOP command (0,0)")
                    
                    # Wait for response to keep connection alive
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                        print(f"ESP32 Reply: {response}")
                    except asyncio.TimeoutError:
                        print("Warning: No response from ESP32 (timeout)")
                    continue

                # 3. Handle Normal Motor Command
                try:
                    duration = int(cmd) # Check if input is a number
                    
                    intensity = input("Enter Intensity (0-255): ")
                    if not intensity.isdigit():
                        print("Error: Intensity must be a number.")
                        continue
                    
                    # Create the payload: "duration,intensity"
                    message = f"{duration},{intensity}"
                    
                    # Send to ESP32
                    await websocket.send(message)
                    print(f"Sent: {message}")
                    
                    # Wait for ESP32 response to keep connection alive
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                        print(f"ESP32 Reply: {response}")
                    except asyncio.TimeoutError:
                        print("Warning: No response from ESP32 (timeout)")
                    
                except ValueError:
                    print("Invalid input. Please enter a number (ms), 's', or 'q'.")

    except ConnectionRefusedError:
        print(f"Error: Could not connect to {URI}.")
        print("Make sure the ESP32 is powered on and connected to the same Wi-Fi.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(communicate())
