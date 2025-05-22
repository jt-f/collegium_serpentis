import asyncio
import json
import uuid
import random
import os
from datetime import datetime, timezone

import websockets

# Get server URL from environment or use default
SERVER_URL = os.getenv("SERVER_URL", "ws://localhost:8000/ws")
CLIENT_ID = str(uuid.uuid4())
STATUS_INTERVAL = int(os.getenv("STATUS_INTERVAL", "10"))  # seconds
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))  # seconds


async def send_status_update(websocket):
    """Sends a status update to the WebSocket server."""
    status_data = {
        "client_id": CLIENT_ID,
        "status": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_usage": round(random.uniform(0.0, 100.0), 2),
            "memory_usage": round(random.uniform(0.0, 100.0), 2),
        },
    }
    await websocket.send(json.dumps(status_data))
    print(f"Sent status update: {status_data}")


async def connect_and_send_updates():
    """Connects to the server, sends periodic status updates,
    and handles reconnection."""
    while True:
        try:
            async with websockets.connect(SERVER_URL) as websocket:
                print(f"Connected to server: {SERVER_URL}")
                while True:
                    await send_status_update(websocket)
                    await asyncio.sleep(STATUS_INTERVAL)
        except ConnectionRefusedError:
            print(f"Connection refused. Retrying in {RECONNECT_DELAY} seconds...")
            await asyncio.sleep(RECONNECT_DELAY)
        except websockets.exceptions.ConnectionClosedError:
            print(f"Connection closed. Retrying in {RECONNECT_DELAY} seconds...")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            print(f"An error occurred: {e}. Retrying in {RECONNECT_DELAY} seconds...")
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    print(f"Starting client with ID: {CLIENT_ID}")
    print(f"Using server URL: {SERVER_URL}")
    print(f"Status update interval: {STATUS_INTERVAL} seconds")
    print(f"Reconnect delay: {RECONNECT_DELAY} seconds")
    asyncio.run(connect_and_send_updates())
