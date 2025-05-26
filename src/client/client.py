import asyncio
import json
import os
import random

# import string # No longer needed for random name generation
import uuid
from datetime import UTC, datetime

import websockets
from faker import Faker  # Added for realistic name generation
from websockets import ConnectionClosed, InvalidURI
from websockets.connection import State as WebSocketState  # Import State enum

# Use environment variable with fallback for flexibility
SERVER_URL = os.getenv("SERVER_URL", "ws://localhost:8000/ws")
CLIENT_ID = str(uuid.uuid4())
CLIENT_ROLE = "worker"  # Added: Define the role of this client
CLIENT_TYPE = "python agent"  # Added: Define the type of this client

# Initialize Faker instance
fake = Faker()


# Function to generate a random name using Faker
def generate_realistic_name() -> str:
    """Generates a realistic-sounding name using Faker."""
    return fake.name()


CLIENT_NAME = generate_realistic_name()  # Generate name at startup

# --- Control Flags ---
manual_disconnect_initiated = False  # Flag to signal graceful shutdown
is_paused = False  # Global flag to control status updates
# --- End Control Flags ---


class ClientInitiatedDisconnect(SystemExit):
    pass


STATUS_INTERVAL = 10  # seconds
RECONNECT_DELAY = 5  # seconds


def get_current_status_payload() -> dict:
    """Generates the current status payload (CPU, memory, etc.)."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "cpu_usage": round(random.uniform(0.0, 100.0), 2),
        "memory_usage": round(random.uniform(0.0, 100.0), 2),
    }


async def send_status_message(websocket, status_attributes: dict):
    # Allow sending disconnect acknowledgment even if manual_disconnect_initiated is true
    is_disconnect_ack = status_attributes.get("acknowledged_command") == "disconnect"
    if manual_disconnect_initiated and not is_disconnect_ack:
        return
    message = {"client_id": CLIENT_ID, "status": status_attributes}
    await websocket.send(json.dumps(message))
    print(f"Sent message: {message}")


async def send_full_status_update(websocket):
    if manual_disconnect_initiated or is_paused:
        return
    current_payload = get_current_status_payload()
    current_payload["client_state"] = "running"  # Should only send if running
    await send_status_message(websocket, current_payload)


async def listen_for_commands(websocket):
    """Listens for commands from the server and updates the client's state."""
    global is_paused, manual_disconnect_initiated
    try:
        async for message_json in websocket:
            if manual_disconnect_initiated:
                break  # Exit loop if disconnect initiated elsewhere
            try:
                message = json.loads(message_json)

                # Check if this is a server acknowledgment message (not a command)
                if (
                    message.get("result") == "message_processed"
                    or message.get("result") == "registration_complete"
                ):
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
                        f"Client {CLIENT_ID}: Disconnect command received. Initiating shutdown."
                    )
                    manual_disconnect_initiated = True
                    ack_status = {
                        "client_state": "disconnecting",
                        "acknowledged_command": "disconnect",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    await send_status_message(websocket, ack_status)
                    # No longer raise ClientInitiatedDisconnect here; let flag handle it.
                    # We will close the websocket from connect_and_send_updates after tasks complete.
                    return  # Exit this task, gather will then complete.
                else:
                    print(
                        f"Client {CLIENT_ID}: " f"Unknown command received: {command}"
                    )

            except json.JSONDecodeError:
                print(f"Client {CLIENT_ID}: Received invalid JSON: {message_json}")
            except Exception as e:
                if (
                    not manual_disconnect_initiated
                ):  # Don't log errors if we are trying to shut down
                    print(f"Client {CLIENT_ID}: Error processing command: {e}")
                else:
                    print(f"Client {CLIENT_ID}: Error during shutdown: {e}")
                # If a critical error occurs, might also want to set manual_disconnect_initiated = True

    except ConnectionClosed as e:
        if manual_disconnect_initiated:
            print(
                f"Client {CLIENT_ID}: Connection closed during manual disconnect sequence."
            )
            return  # Expected if server closes after we ack disconnect command

        close_code = e.rcvd.code if e.rcvd else None
        close_reason = e.rcvd.reason if e.rcvd else ""
        print(
            f"Client {CLIENT_ID}: Connection closed. Code: {close_code}, Reason: '{close_reason}'"
        )
        if (
            close_code == 1000
        ):  # Server initiated normal disconnect (e.g. after sending command)
            print(
                f"Client {CLIENT_ID}: Server closed connection normally (code 1000). Signaling shutdown."
            )
            manual_disconnect_initiated = True  # Signal to not reconnect
            # No need to raise ClientInitiatedDisconnect if flag handles it
        # For other close codes, the outer loop will handle reconnection attempts if flag not set.
        raise  # Re-raise to be caught by connect_and_send_updates to potentially retry
    except Exception as e:
        if not manual_disconnect_initiated:
            print(f"Client {CLIENT_ID}: Unexpected error in command listener: {e}")
            manual_disconnect_initiated = (
                True  # Safety: signal disconnect on unknown errors in listener
            )
        raise


async def send_status_update_periodically(websocket):
    """Periodically sends status updates if the client is not paused."""
    global manual_disconnect_initiated, is_paused
    try:
        while not manual_disconnect_initiated:
            if not is_paused:
                await send_full_status_update(websocket)
            # Sleep for the defined interval before the next update or check.
            await asyncio.sleep(STATUS_INTERVAL)
        print(f"Client {CLIENT_ID}: Exiting periodic sender due to disconnect signal.")
    except ConnectionClosed:
        if not manual_disconnect_initiated:
            print(f"Client {CLIENT_ID}: Connection lost during periodic update.")
        raise  # Re-raise for outer loop to handle (or not if flag is set)
    except Exception as e:
        if not manual_disconnect_initiated:
            print(f"Client {CLIENT_ID}: Error in periodic sender: {e}")
            manual_disconnect_initiated = True  # Safety net
        raise


async def connect_and_send_updates():
    """Connects to the server, sends initial status, and manages concurrent tasks."""
    global manual_disconnect_initiated, is_paused

    while not manual_disconnect_initiated:
        websocket: websockets.client.ClientConnection | None = None
        try:
            async with websockets.connect(SERVER_URL) as ws_connection:
                websocket = ws_connection
                print(f"Client {CLIENT_ID} ({CLIENT_NAME}): Connected to {SERVER_URL}")
                manual_disconnect_initiated = False
                is_paused = False

                initial_status = {
                    "client_name": CLIENT_NAME,
                    "client_state": "running",
                    "client_role": CLIENT_ROLE,
                    "client_type": CLIENT_TYPE,
                    **get_current_status_payload(),
                }
                await send_status_message(websocket, initial_status)

                listener_task = asyncio.create_task(listen_for_commands(websocket))
                periodic_sender_task = asyncio.create_task(
                    send_status_update_periodically(websocket)
                )

                done, pending = await asyncio.wait(
                    [listener_task, periodic_sender_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                for task in done:
                    try:
                        task.result()
                    except ConnectionClosed:
                        if not manual_disconnect_initiated:
                            print(
                                f"Client {CLIENT_ID}: A task ended due to connection closure."
                            )
                    except asyncio.CancelledError:
                        print(f"Client {CLIENT_ID}: A task was cancelled externally.")
                    except Exception as task_exc:
                        if not manual_disconnect_initiated:
                            print(
                                f"Client {CLIENT_ID}: A task failed: {task_exc}. Initiating shutdown."
                            )
                            manual_disconnect_initiated = True

                if manual_disconnect_initiated:
                    print(
                        f"Client {CLIENT_ID}: Graceful shutdown signaled from tasks. Closing websocket."
                    )
                    if websocket and websocket.state == WebSocketState.OPEN:
                        await websocket.close(reason="Client task initiated shutdown")
                    break

        except ClientInitiatedDisconnect as e:
            print(
                f"Client {CLIENT_ID}: ClientInitiatedDisconnect caught: {e}. Ensuring shutdown."
            )
            manual_disconnect_initiated = True
            if websocket and websocket.state == WebSocketState.OPEN:
                await websocket.close(code=1000, reason="ClientInitiatedDisconnect")
            break
        except SystemExit as e:
            print(f"Client {CLIENT_ID}: SystemExit caught: {e}. Shutting down.")
            manual_disconnect_initiated = True
            if websocket and websocket.state == WebSocketState.OPEN:
                await websocket.close(code=1000, reason="SystemExit")
            break
        except asyncio.CancelledError:
            print(f"Client {CLIENT_ID}: Main connection loop cancelled.")
            manual_disconnect_initiated = True
            raise
        except ConnectionRefusedError as e:
            print(f"Client {CLIENT_ID}: Connection refused: {e}. Retrying...")
            # manual_disconnect_initiated remains False, rely on generic sleep and loop
        except (InvalidURI, OSError) as e:
            print(
                f"Client {CLIENT_ID}: Invalid configuration or OS error: {e}. Cannot connect. Shutting down."
            )
            manual_disconnect_initiated = True
            break
        except ConnectionClosed as e:
            if manual_disconnect_initiated:
                print(
                    f"Client {CLIENT_ID}: ConnectionClosed caught during shutdown sequence."
                )
            else:
                close_code = e.code
                print(
                    f"Client {CLIENT_ID}: Connection closed unexpectedly. Code: {close_code}. Retrying..."
                )
                if close_code == 1000:
                    print(
                        f"Client {CLIENT_ID}: Server closed connection (1000). Shutting down."
                    )
                    manual_disconnect_initiated = True
            if manual_disconnect_initiated:
                break
        except Exception as e:
            print(
                f"Client {CLIENT_ID}: Unexpected error in connection loop: {type(e).__name__} - {e}. Retrying..."
            )
            if websocket and websocket.state == WebSocketState.OPEN:
                await websocket.close(code=1011, reason="Unexpected loop error")

        if not manual_disconnect_initiated:
            print(
                f"Client {CLIENT_ID}: Attempting reconnection in {RECONNECT_DELAY} seconds..."
            )
            await asyncio.sleep(RECONNECT_DELAY)
        else:
            print(f"Client {CLIENT_ID}: Shutdown initiated, not reconnecting.")
            if websocket and websocket.state == WebSocketState.OPEN:
                await websocket.close(reason="Ensuring closure on shutdown")
            break

    print(f"Client {CLIENT_ID} ({CLIENT_NAME}) has shut down.")


if __name__ == "__main__":
    print(f"Starting client with ID: {CLIENT_ID}, Name: {CLIENT_NAME}")
    try:
        asyncio.run(connect_and_send_updates())
    except KeyboardInterrupt:
        print(f"Client {CLIENT_ID}: Keyboard interrupt received. Shutting down...")
    finally:
        # This ensures the flag is set if shutdown initiated by Ctrl+C
        manual_disconnect_initiated = True
        print(f"Client {CLIENT_ID}: Final cleanup.")
