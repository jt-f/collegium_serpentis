import asyncio
import json
import os
import random
import uuid
from datetime import UTC, datetime

import websockets
from websockets import ConnectionClosed, InvalidURI

# Use environment variable with fallback for flexibility
SERVER_URL = os.getenv("SERVER_URL", "ws://localhost:8000/ws")
CLIENT_ID = str(uuid.uuid4())


class ClientInitiatedDisconnect(SystemExit):
    pass


STATUS_INTERVAL = 10  # seconds
RECONNECT_DELAY = 5  # seconds

is_paused = False  # Global flag to control status updates


def get_current_status_payload() -> dict:
    """Generates the current status payload (CPU, memory, etc.)."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "cpu_usage": round(random.uniform(0.0, 100.0), 2),
        "memory_usage": round(random.uniform(0.0, 100.0), 2),
    }


async def send_status_message(websocket, status_attributes: dict):
    """Sends a generic status message including client_id."""
    message = {"client_id": CLIENT_ID, "status": status_attributes}
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

                # Check if this is a server acknowledgment message (not a command)
                if message.get("result") == "message_processed":
                    # This is just a server acknowledgment, not a command
                    print(
                        f"Client {CLIENT_ID}: " f"Status update acknowledged by server"
                    )
                    continue

                # Check if this is an actual command message
                command = message.get("command")
                if command is None:
                    # If no command field but not an acknowledgment, log and ignore
                    if "result" not in message:
                        print(
                            f"Client {CLIENT_ID}: "
                            f"Received non-command msg: {message}"
                        )
                    continue

                print(f"Client {CLIENT_ID}: Received command: {command}")

                if command == "pause":
                    if not is_paused:
                        is_paused = True
                        print(
                            f"Client {CLIENT_ID}: Paused command received. "
                            f"Halting status updates."
                        )
                        ack_status = {
                            "client_state": "paused",
                            "acknowledged_command": "pause",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                        await send_status_message(websocket, ack_status)
                    else:
                        print(
                            f"Client {CLIENT_ID}: Already paused. "
                            f"Pause command ignored."
                        )
                elif command == "resume":
                    if is_paused:
                        is_paused = False
                        print(
                            f"Client {CLIENT_ID}: Resume command received. "
                            f"Resuming status updates."
                        )
                        # Send ack with current full status
                        ack_status = {
                            "client_state": "running",
                            "acknowledged_command": "resume",
                            **get_current_status_payload(),
                        }
                        await send_status_message(websocket, ack_status)
                    else:
                        print(
                            f"Client {CLIENT_ID}: Already running. "
                            f"Resume command ignored."
                        )
                elif command == "disconnect":
                    print(
                        f"Client {CLIENT_ID}: Disconnect command received. Shutting down."
                    )
                    ack_status = {
                        "client_state": "disconnecting",
                        "acknowledged_command": "disconnect",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    await send_status_message(websocket, ack_status)
                    raise ClientInitiatedDisconnect("Server requested disconnect.")
                else:
                    print(
                        f"Client {CLIENT_ID}: " f"Unknown command received: {command}"
                    )

            except json.JSONDecodeError:
                print(f"Client {CLIENT_ID}: Received invalid JSON: {message_json}")
            except Exception as e:
                print(f"Client {CLIENT_ID}: Error processing message: {e}")

    except ConnectionClosed as e:
        # Get the close code and reason from the rcvd attribute
        close_code = e.rcvd.code if e.rcvd else None
        close_reason = e.rcvd.reason if e.rcvd else ""

        msg = (
            f"Client {CLIENT_ID}: Connection closed while listening for commands. "
            f"Code: {close_code}, Reason: {close_reason}"
        )
        print(msg)

        # Check if this was a server-initiated disconnect (should not reconnect)
        if close_code == 1000:  # Normal closure - likely server disconnect
            print(
                f"Client {CLIENT_ID}: Server initiated disconnect. " f"Shutting down."
            )
            raise ClientInitiatedDisconnect(
                "Server disconnected client - shutting down"
            ) from None

        # This exception will propagate to connect_and_send_updates
        # and trigger reconnection
        raise
    except Exception as e:
        print(f"Client {CLIENT_ID}: Unexpected error in command listener: {e}")
        raise  # Re-raise to allow connect_and_send_updates to handle it


async def send_status_update_periodically(websocket):
    """Periodically sends status updates if the client is not paused."""
    try:
        while True:
            if not is_paused:
                await send_full_status_update(websocket)
            await asyncio.sleep(STATUS_INTERVAL)
    except ConnectionClosed as e:
        # Get the close code and reason from the rcvd attribute
        close_code = e.rcvd.code if e.rcvd else None
        close_reason = e.rcvd.reason if e.rcvd else ""

        msg = (
            f"Client {CLIENT_ID}: Connection closed during periodic status update. "
            f"Code: {close_code}, Reason: {close_reason}"
        )
        print(msg)
        raise  # Re-raise to allow connect_and_send_updates to handle it
    except Exception as e:
        print(f"Client {CLIENT_ID}: Error in periodic status sender: {e}")
        raise  # Re-raise to allow connect_and_send_updates to handle it


async def connect_and_send_updates():
    """Connects to the server, sends initial status, and manages concurrent tasks."""
    while True:
        try:
            async with websockets.connect(SERVER_URL) as websocket:
                print(f"Client {CLIENT_ID}: Connected to server {SERVER_URL}")

                # Send initial status update to register client
                initial_status = {
                    "client_state": "running",
                    "connected_at": datetime.now(UTC).isoformat(),
                    **get_current_status_payload(),
                }
                await send_status_message(websocket, initial_status)
                # Ensure client starts in non-paused state on new connection
                global is_paused
                is_paused = False

                listener_task = asyncio.create_task(listen_for_commands(websocket))
                periodic_sender_task = asyncio.create_task(
                    send_status_update_periodically(websocket)
                )

                # await asyncio.gather to keep connection alive and handle tasks
                # If one task fails (e.g. ConnectionClosed), gather will raise
                # that exception which will be caught by the outer try/except
                # block for reconnection.
                await asyncio.gather(listener_task, periodic_sender_task)

        except ClientInitiatedDisconnect as e:
            print(
                f"Client {CLIENT_ID}: Shutting down due to client-initiated disconnect: {e}"
            )
            return  # Exit the function, stopping the client
        except SystemExit as e:
            print(f"Client {CLIENT_ID}: Shutting down as requested: {e}")
            return  # Exit the function, stopping the client
        except (ConnectionClosed, InvalidURI, OSError) as e:
            conn_err_msg = (
                f"Client {CLIENT_ID}: Connection error ({type(e).__name__}: {e}). "
                f"Retrying in {RECONNECT_DELAY} seconds..."
            )
            print(conn_err_msg)
        except Exception as e:
            unexpected_err_msg = (
                f"Client {CLIENT_ID}: An unexpected error occurred "
                f"({type(e).__name__}: {e}). "
                f"Retrying in {RECONNECT_DELAY} seconds..."
            )
            print(unexpected_err_msg)

        # If any task in gather failed or loop broke, we end up here.
        # Wait before retrying connection.
        await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    print(f"Starting client with ID: {CLIENT_ID}")
    asyncio.run(connect_and_send_updates())
