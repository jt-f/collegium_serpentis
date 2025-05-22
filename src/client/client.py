import asyncio
import json
import uuid
import random
from datetime import datetime, timezone

import websockets

SERVER_URL = "ws://server:8000/ws"
CLIENT_ID = str(uuid.uuid4())
STATUS_INTERVAL = 10  # seconds
RECONNECT_DELAY = 5  # seconds

is_paused = False  # Global flag to control status updates


def get_current_status_payload() -> dict:
    """Generates the current status payload (CPU, memory, etc.)."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu_usage": round(random.uniform(0.0, 100.0), 2),
        "memory_usage": round(random.uniform(0.0, 100.0), 2),
        # "client_state": "paused" if is_paused else "running", # Add if server needs explicit state in every update
    }


async def send_status_message(websocket, status_attributes: dict):
    """Sends a generic status message including client_id."""
    message = {
        "client_id": CLIENT_ID,
        "status": status_attributes
    }
    await websocket.send(json.dumps(message))
    print(f"Sent message: {message}")


async def send_full_status_update(websocket):
    """Sends a regular, full status update to the WebSocket server."""
    current_payload = get_current_status_payload()
    # Add client_state for clarity, especially if it's a regular update
    current_payload["client_state"] = "paused" if is_paused else "running"
    await send_status_message(websocket, current_payload)


async def listen_for_commands(websocket):
    """Listens for commands from the server and updates the client's state."""
    global is_paused
    try:
        async for message_json in websocket:
            try:
                message = json.loads(message_json)
                print(f"Received command message: {message}")
                command = message.get("command")

                if command == "pause":
                    if not is_paused:
                        is_paused = True
                        print(f"Client {CLIENT_ID}: Paused command received. Halting status updates.")
                        ack_status = {
                            "client_state": "paused",
                            "acknowledged_command": "pause",
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        await send_status_message(websocket, ack_status)
                    else:
                        print(f"Client {CLIENT_ID}: Already paused. Pause command ignored.")
                elif command == "resume":
                    if is_paused:
                        is_paused = False
                        print(f"Client {CLIENT_ID}: Resume command received. Resuming status updates.")
                        # Send ack with current full status
                        ack_status = {
                            "client_state": "running",
                            "acknowledged_command": "resume",
                            **get_current_status_payload() # Add current metrics
                        }
                        await send_status_message(websocket, ack_status)
                        # No need to call send_full_status_update here,
                        # the periodic sender will pick it up or the ack itself is enough.
                    else:
                        print(f"Client {CLIENT_ID}: Already running. Resume command ignored.")
                else:
                    print(f"Client {CLIENT_ID}: Unknown command received: {command}")

            except json.JSONDecodeError:
                print(f"Client {CLIENT_ID}: Received invalid JSON: {message_json}")
            except Exception as e:
                print(f"Client {CLIENT_ID}: Error processing command: {e}")
                # Decide if to break or continue based on error severity

    except websockets.exceptions.ConnectionClosed as e:
        print(f"Client {CLIENT_ID}: Connection closed while listening for commands. Code: {e.code}, Reason: {e.reason}")
        # This exception will propagate to connect_and_send_updates and trigger reconnection.
        raise  # Re-raise to allow connect_and_send_updates to handle it
    except Exception as e:
        print(f"Client {CLIENT_ID}: Unexpected error in command listener: {e}")
        raise # Re-raise to allow connect_and_send_updates to handle it


async def send_status_update_periodically(websocket):
    """Periodically sends status updates if the client is not paused."""
    try:
        while True:
            if not is_paused:
                # print(f"Client {CLIENT_ID}: Sending periodic status (is_paused={is_paused})...")
                await send_full_status_update(websocket)
            # else:
                # print(f"Client {CLIENT_ID}: Paused. Skipping periodic status update.")
            await asyncio.sleep(STATUS_INTERVAL)
    except websockets.exceptions.ConnectionClosed as e:
        print(f"Client {CLIENT_ID}: Connection closed during periodic status update. Code: {e.code}, Reason: {e.reason}")
        raise # Re-raise to allow connect_and_send_updates to handle it
    except Exception as e:
        print(f"Client {CLIENT_ID}: Error in periodic status sender: {e}")
        raise # Re-raise to allow connect_and_send_updates to handle it


async def connect_and_send_updates():
    """Connects to the server, sends initial status, and manages concurrent tasks."""
    while True:
        try:
            async with websockets.connect(SERVER_URL) as websocket:
                print(f"Client {CLIENT_ID}: Connected to server {SERVER_URL}")
                
                # Send initial status update to register client and indicate it's running
                initial_status = {
                    "client_state": "running",
                    "connected_at": datetime.now(timezone.utc).isoformat(),
                    **get_current_status_payload()
                }
                await send_status_message(websocket, initial_status)
                is_paused = False # Ensure client starts in non-paused state on new connection

                listener_task = asyncio.create_task(listen_for_commands(websocket))
                periodic_sender_task = asyncio.create_task(send_status_update_periodically(websocket))

                # await asyncio.gather to keep connection alive and handle tasks
                # If one task fails (e.g. ConnectionClosed), gather will raise that exception
                # which will be caught by the outer try/except block for reconnection.
                await asyncio.gather(listener_task, periodic_sender_task)

        except (ConnectionRefusedError, websockets.exceptions.ConnectionClosedError, websockets.exceptions.InvalidURI, OSError) as e: # Added OSError for DNS issues etc.
            print(f"Client {CLIENT_ID}: Connection error ({type(e).__name__}: {e}). Retrying in {RECONNECT_DELAY} seconds...")
        except Exception as e: # Catch-all for other unexpected errors during connection setup or if gather re-raises something else
            print(f"Client {CLIENT_ID}: An unexpected error occurred ({type(e).__name__}: {e}). Retrying in {RECONNECT_DELAY} seconds...")
        
        # If any task in gather failed or loop broke, we end up here.
        # Wait before retrying connection.
        await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    print(f"Starting client with ID: {CLIENT_ID}")
    asyncio.run(connect_and_send_updates())
