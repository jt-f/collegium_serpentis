import asyncio
import json
import os
import random
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis_async
import websockets
from dotenv import load_dotenv
from faker import Faker  # Added for realistic name generation
from mistralai import Mistral
from websockets import ConnectionClosed, InvalidURI
from websockets.protocol import State as WebSocketState  # Corrected import

from src.shared.utils.logging import get_logger

# Load environment variables from .env file
load_dotenv()

logger = get_logger(__name__)

# Use environment variable with fallback for flexibility
SERVER_URL = os.getenv("SERVER_URL", "ws://localhost:8000/ws")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CLIENT_ID = str(uuid.uuid4())
CLIENT_ROLE = "worker"  # Added: Define the role of this client
CLIENT_TYPE = "python agent"  # Added: Define the type of this client

# Mistral AI configuration
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    logger.warning(
        "MISTRAL_API_KEY not found in environment variables. Chat responses will be disabled."
    )
    MISTRAL_CLIENT = None
else:
    logger.info(
        "MISTRAL_API_KEY found in environment variables. Chat responses will be enabled."
    )
    MISTRAL_CLIENT = Mistral(api_key=MISTRAL_API_KEY)

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


async def send_registration_message(websocket):
    """Sends the initial registration message to the server."""
    registration_payload = {
        "type": "register",
        "client_id": CLIENT_ID,
        "status": {
            "client_role": CLIENT_ROLE,
            "client_type": CLIENT_TYPE,
            "client_name": CLIENT_NAME,
            "client_state": "initializing",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }
    await websocket.send(json.dumps(registration_payload))
    logger.info(f"Sent registration message: {registration_payload}")


async def send_status_message(websocket, status_attributes: dict):
    # Allow sending disconnect acknowledgment even if manual_disconnect_initiated is true
    is_disconnect_ack = status_attributes.get("acknowledged_command") == "disconnect"
    if manual_disconnect_initiated and not is_disconnect_ack:
        return
    message = {
        "type": "status_update",
        "client_id": CLIENT_ID,
        "status": status_attributes,
    }
    await websocket.send(json.dumps(message))

    # Log status updates with minimal info
    if status_attributes.get("acknowledged_command"):
        # Log acknowledgment messages
        logger.info(
            f"Sent command acknowledgment: {status_attributes.get('acknowledged_command')}"
        )
    elif "client_state" in status_attributes:
        # Log state change messages
        logger.info(
            f"Sent status update: state={status_attributes.get('client_state')}"
        )
    else:
        # Log periodic status updates with minimal info
        logger.info("Sent status update: periodic")


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
                    or message.get("type") == "message_processed"
                ):
                    # This is just a server acknowledgment, not a command
                    ack_type = message.get("result") or message.get("type")
                    logger.info(f"Server acknowledgment: {ack_type}")
                    continue

                # Check if this is an error message from the server
                error_content = message.get("error")
                if error_content is not None:
                    logger.error(
                        f"Received error message from server: '{error_content}'. "
                        f"Initiating shutdown."
                    )
                    manual_disconnect_initiated = True
                    return  # Exit listener task, gather will handle shutdown

                # Check if this is an actual command message
                command = message.get("command")
                if command is None:
                    # If no command field but not an acknowledgment, log and ignore
                    if "result" not in message:
                        print(f"Received non-command msg: {message}")
                    continue

                logger.info(f"Received command: {command}")

                if command == "pause":
                    if not is_paused:
                        is_paused = True
                        logger.info(
                            "Pause command received. " "Halting status updates."
                        )
                        ack_status = {
                            "client_state": "paused",
                            "acknowledged_command": "pause",
                        }
                        await send_status_message(websocket, ack_status)
                    else:
                        logger.info("Already paused. " "Pause command ignored.")
                elif command == "resume":
                    if is_paused:
                        is_paused = False
                        logger.info(
                            "Resume command received. " "Resuming status updates."
                        )
                        # Send ack with current full status
                        ack_status = {
                            "client_state": "running",
                            "acknowledged_command": "resume",
                            **get_current_status_payload(),
                        }
                        await send_status_message(websocket, ack_status)
                    else:
                        logger.info("Already running. " "Resume command ignored.")
                elif command == "disconnect":
                    logger.info("Disconnect command received. Initiating shutdown.")
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
                    logger.warning(f"Unknown command received: {command}")

            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON: {message_json}")
            except Exception as e:
                if (
                    not manual_disconnect_initiated
                ):  # Don't log errors if we are trying to shut down
                    logger.error(f"Error processing command: {e}")
                else:
                    logger.error(f"Error during shutdown: {e}")
                # If a critical error occurs, might also want to set manual_disconnect_initiated = True

    except ConnectionClosed as e:
        if manual_disconnect_initiated:
            logger.info("Connection closed during manual disconnect sequence.")
            return  # Expected if server closes after we ack disconnect command

        close_code = e.rcvd.code if e.rcvd else None
        close_reason = e.rcvd.reason if e.rcvd else ""
        logger.info(
            f"Connection closed. " f"Code: {close_code}, Reason: '{close_reason}'"
        )
        if (
            close_code == 1000
        ):  # Server initiated normal disconnect (e.g. after sending command)
            if "Server initiated disconnect" in close_reason:
                logger.info(
                    "Server initiated disconnect (1000). "
                    "Not attempting to reconnect."
                )
                manual_disconnect_initiated = True  # Signal to not reconnect
            else:
                logger.info(
                    "Server closed connection (1000). " "Will attempt to reconnect."
                )
                # Standard delay will apply before reconnecting
        # For other close codes, the outer loop will handle reconnection attempts if flag not set.
        raise  # Re-raise to be caught by connect_and_send_updates to potentially retry
    except Exception as e:
        if not manual_disconnect_initiated:
            logger.error(f"Unexpected error in command listener: {e}")
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
        logger.info("Exiting periodic sender due to disconnect signal.")
    except ConnectionClosed:
        if not manual_disconnect_initiated:
            logger.warning("Connection lost during periodic update.")
        raise  # Re-raise for outer loop to handle (or not if flag is set)
    except Exception as e:
        if not manual_disconnect_initiated:
            logger.error(f"Error in periodic sender: {e}")
            manual_disconnect_initiated = True  # Safety net
        raise


async def connect_and_send_updates():
    """Main function to connect to the server and send periodic updates."""
    global manual_disconnect_initiated, CLIENT_ID, CLIENT_NAME, is_paused
    delay = RECONNECT_DELAY  # Initial delay

    print(
        f"connect_and_send_updates() called. manual_disconnect_initiated={manual_disconnect_initiated}"
    )

    # Initialize chat consumer
    chat_consumer = ChatConsumer(CLIENT_ID)
    chat_consumer_task = None

    while not manual_disconnect_initiated:
        print(
            f"Entering main loop iteration. manual_disconnect_initiated={manual_disconnect_initiated}"
        )
        websocket = None  # Initialize websocket to None
        listener_task = None
        periodic_sender_task = None
        try:
            print(f"About to call websockets.connect({SERVER_URL})")
            logger.info(f"Attempting to connect to {SERVER_URL}...")
            websocket = await websockets.connect(SERVER_URL)
            print("websockets.connect() succeeded")
            logger.info("Connected to server.")
            delay = RECONNECT_DELAY  # Reset delay on successful connection

            # Send initial registration message
            await send_registration_message(websocket)

            # Initial full status update upon connection (after registration)
            initial_status = {
                "client_name": CLIENT_NAME,
                "client_state": "running",
                "client_role": CLIENT_ROLE,
                "client_type": CLIENT_TYPE,
                **get_current_status_payload(),
            }
            await send_status_message(websocket, initial_status)

            # Setup chat consumer (only once per client session)
            if chat_consumer_task is None:
                if await chat_consumer.connect():
                    if await chat_consumer.setup_consumer_groups():
                        chat_consumer_task = asyncio.create_task(
                            chat_consumer.consume_messages()
                        )
                        logger.info("Chat consumer started")
                    else:
                        logger.warning("Failed to setup chat consumer groups")
                else:
                    logger.warning("Failed to connect to Redis for chat")

            # Create tasks for listening to commands and sending periodic updates
            listener_task = asyncio.create_task(listen_for_commands(websocket))
            periodic_sender_task = asyncio.create_task(
                send_status_update_periodically(websocket)
            )

            # Only gather WebSocket-related tasks (chat consumer runs independently)
            websocket_tasks = [listener_task, periodic_sender_task]

            # Wait for any WebSocket task to complete (or be cancelled)
            task_results = await asyncio.gather(
                *websocket_tasks, return_exceptions=True
            )

            # Handle results/exceptions from WebSocket tasks only
            # Chat consumer continues independently of WebSocket connection status
            for task_result in task_results:
                if isinstance(task_result, ClientInitiatedDisconnect):
                    logger.info(
                        "Client initiated disconnect processed by gather result."
                    )
                    manual_disconnect_initiated = True
                elif isinstance(task_result, ConnectionClosed):
                    # Re-raise ConnectionClosed to be handled by main exception handler
                    # This allows 4008 (duplicate client ID) to trigger ID regeneration
                    logger.info(
                        "ConnectionClosed from WebSocket task, re-raising for main handler."
                    )
                    raise task_result
                elif isinstance(task_result, BaseException):
                    exc = task_result
                    # Handle different types of WebSocket task exceptions
                    if isinstance(
                        exc | (ConnectionClosed, ConnectionRefusedError, TimeoutError)
                    ):
                        # Network-related errors: attempt to reconnect WebSocket
                        logger.warning(
                            f"Network error in WebSocket task: {exc!r}. Will attempt to reconnect."
                        )
                        # Don't set manual_disconnect_initiated, let it reconnect
                    elif (
                        isinstance(exc, asyncio.CancelledError)
                        and exc.__str__() == "Test sleep limit"
                    ):
                        # Special test case handling
                        logger.info(
                            f"WebSocket task cancelled: {exc!r}. Assuming part of controlled shutdown or test sequence."
                        )
                    else:
                        # Logic errors, validation errors, or other serious issues: shut down
                        logger.error(
                            f"Critical error in WebSocket task: {exc!r}. Signaling shutdown."
                        )
                        manual_disconnect_initiated = True

            # If manual_disconnect_initiated is True, break the loop after cleanup
            if manual_disconnect_initiated:
                logger.info("Breaking main loop for shutdown.")
                break

        except InvalidURI:
            logger.error(f"Invalid server URI: {SERVER_URL}. Exiting.")
            manual_disconnect_initiated = True  # Prevent further attempts
            break  # Exit the loop
        except ConnectionRefusedError:
            logger.warning(
                f"Connection refused. " f"Retrying in {delay:.2f} seconds..."
            )
        except TimeoutError:  # Add specific handling for TimeoutError
            logger.warning(
                f"Connection timed out during connect. "
                f"Retrying in {delay:.2f} seconds..."
            )
        except ConnectionClosed as e:
            close_code = e.rcvd.code if e.rcvd else None
            close_reason = e.rcvd.reason if e.rcvd else ""
            logger.info(
                f"Connection closed. " f"Code: {close_code}, Reason: '{close_reason}'"
            )
            if manual_disconnect_initiated:
                logger.info("Connection closed during shutdown.")
                break  # Exit loop if disconnect was intentional

            if close_code == 4008:  # Duplicate client ID
                logger.warning(
                    "Duplicate client ID detected (4008). "
                    "Generating new ID and retrying immediately."
                )
                CLIENT_ID = str(uuid.uuid4())  # Regenerate client ID
                CLIENT_NAME = generate_realistic_name()  # Regenerate name with new ID
                delay = 0  # Retry immediately
                continue  # Skip sleep and retry connection immediately
            elif close_code == 1000:  # Normal closure by server
                if "Server initiated disconnect" in close_reason:
                    logger.info(
                        "Server initiated disconnect (1000). "
                        "Not attempting to reconnect."
                    )
                    manual_disconnect_initiated = True  # Signal to not reconnect
                else:
                    logger.info(
                        "Server closed connection (1000). " "Will attempt to reconnect."
                    )
                    # Standard delay will apply before reconnecting
            # For other close codes, standard delay applies

        except asyncio.CancelledError:
            logger.info("Main connection task cancelled. Exiting.")
            manual_disconnect_initiated = True
            break
        except Exception as e:
            logger.error(
                f"An unexpected error occurred: {e}. Signaling shutdown.",
                exc_info=True,
            )
            manual_disconnect_initiated = True  # Stop retrying on generic errors
        finally:
            # Ensure tasks are cancelled if they were started
            if listener_task and not listener_task.done():
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass  # Expected
            if periodic_sender_task and not periodic_sender_task.done():
                periodic_sender_task.cancel()
                try:
                    await periodic_sender_task
                except asyncio.CancelledError:
                    pass  # Expected

            # Close the websocket if it was opened and is not already closed
            if websocket and websocket.state != WebSocketState.CLOSED:
                print("Closing WebSocket connection in finally block.")
                await websocket.close()
                websocket = None  # Reset websocket

        if not manual_disconnect_initiated:
            if delay > 0:  # Only sleep if delay is positive
                await asyncio.sleep(delay)
            delay = min(
                delay * 2 if delay > 0 else RECONNECT_DELAY, 60
            )  # Exponential backoff, max 60 seconds
        else:
            break  # Ensure exit if disconnect was initiated

    print("Exited main connection loop.")

    # Cleanup chat consumer
    if chat_consumer_task and not chat_consumer_task.done():
        chat_consumer_task.cancel()
        try:
            await chat_consumer_task
        except asyncio.CancelledError:
            pass  # Expected

    # Stop chat consumer with exception handling
    try:
        await chat_consumer.stop()
    except (Exception, SystemExit) as e:
        # Ignore exceptions during cleanup, especially ClientInitiatedDisconnect (which inherits from SystemExit)
        logger.debug(f"Exception during chat consumer cleanup: {e}")
        pass

    print("Shutdown complete.")


class ChatConsumer:
    """Redis Streams consumer for chat messages."""

    def __init__(
        self, client_id: str, redis_host: str = REDIS_HOST, redis_port: int = REDIS_PORT
    ):
        self.client_id = client_id
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_conn: redis_async.Redis | None = None
        self.is_running = False

        # Stream keys
        self.personal_stream = f"chat_stream:{client_id}"
        self.global_stream = "chat_global"
        self.consumer_group = "workers"
        self.consumer_name = f"worker-{client_id}"

    async def connect(self) -> bool:
        """Connect to Redis."""
        try:
            self.redis_conn = redis_async.Redis(
                host=self.redis_host, port=self.redis_port, db=0
            )
            await self.redis_conn.ping()
            logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return False

    async def setup_consumer_groups(self) -> bool:
        """Set up consumer groups for streams."""
        try:
            # Create consumer group for personal stream (starting from newest messages)
            try:
                await self.redis_conn.xgroup_create(
                    self.personal_stream, self.consumer_group, "$", mkstream=True
                )
                logger.info(
                    f"Created consumer group for personal stream: {self.personal_stream}"
                )
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    logger.debug(
                        f"Consumer group already exists for personal stream: {self.personal_stream}"
                    )
                else:
                    logger.warning(
                        f"Failed to create consumer group for personal stream: {e}"
                    )

            # Create consumer group for global stream (starting from newest messages)
            try:
                await self.redis_conn.xgroup_create(
                    self.global_stream, self.consumer_group, "$", mkstream=True
                )
                logger.info(
                    f"Created consumer group for global stream: {self.global_stream}"
                )
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    logger.debug(
                        f"Consumer group already exists for global stream: {self.global_stream}"
                    )
                else:
                    logger.warning(
                        f"Failed to create consumer group for global stream: {e}"
                    )

            return True
        except Exception as e:
            logger.error(f"Failed to setup consumer groups: {e}")
            return False

    async def publish_response(
        self, original_message_id: str, response: str, original_sender_id: str
    ) -> bool:
        """Publish a response message to the global Redis stream using unified schema."""
        try:
            # Generate unique message ID for this response
            response_message_id = (
                f"resp-{self.client_id}-{datetime.now(UTC).timestamp()}"
            )

            message_data = {
                "type": "chat",
                "message_id": response_message_id,
                "client_id": self.client_id,
                "message": response,
                "target_id": "",  # Empty for broadcast response
                "in_response_to_message_id": original_message_id,
                "sender_role": "worker",
                "timestamp": datetime.now(UTC).isoformat(),
            }

            message_id = await self.redis_conn.xadd(self.global_stream, message_data)
            logger.info(
                f"Published response to global stream with ID: {message_id.decode('utf-8')}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to publish response: {e}")
            return False

    async def generate_ai_response(self, message: str, sender_id: str) -> str | None:
        """Generate an AI response using Mistral."""
        if not MISTRAL_CLIENT:
            logger.debug("Mistral client not available, skipping AI response")
            return None

        try:
            # Create a context-aware prompt
            system_prompt = (
                f"You are a helpful AI assistant in a distributed system chat. "
                f"Your client ID is {self.client_id}. "
                f"You are responding to a message from client {sender_id}. "
                f"Keep your responses concise, helpful, and conversational. "
                f"If the message is a question, try to provide a useful answer. "
                f"If it's a statement, acknowledge it appropriately."
            )

            # Generate response using Mistral
            response = await asyncio.to_thread(
                MISTRAL_CLIENT.chat.complete,
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                max_tokens=150,
                temperature=0.7,
            )

            ai_response = response.choices[0].message.content
            logger.info(f"Response: {ai_response}")
            return ai_response

        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            return None

    async def consume_messages(self) -> None:
        """Main message consumption loop."""
        logger.info("Starting chat message consumption")
        self.is_running = True

        while self.is_running and not manual_disconnect_initiated:
            try:
                # Read from both personal and global streams
                streams = {
                    self.personal_stream: ">",  # Get new messages
                    self.global_stream: ">",  # Get new messages
                }

                # Use XREADGROUP to consume messages (one at a time for sequential processing)
                messages = await self.redis_conn.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    streams,
                    count=1,  # Read only 1 message at a time to ensure sequential processing
                    block=1000,  # Block for 1 second if no messages
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        stream_name = stream_name.decode("utf-8")
                        for message_id, fields in stream_messages:
                            message_id = message_id.decode("utf-8")
                            await self.process_message(stream_name, message_id, fields)

                            # Acknowledge the message
                            await self.redis_conn.xack(
                                stream_name, self.consumer_group, message_id
                            )

            except Exception as e:
                if not manual_disconnect_initiated:
                    logger.error(f"Error in chat message consumption loop: {e}")
                    await asyncio.sleep(5)  # Wait before retrying
                else:
                    break

    async def process_message(
        self, stream_name: str, message_id: str, fields: dict[bytes, bytes]
    ) -> None:
        """Process a received chat message and generate AI responses."""
        try:
            # Decode fields from bytes to strings
            decoded_fields = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in fields.items()
            }

            # Extract basic message data using unified schema
            sender_id = decoded_fields.get("client_id", "unknown")
            message_text = decoded_fields.get("message", "")
            target_id = decoded_fields.get("target_id", "")
            in_response_to_message_id = decoded_fields.get(
                "in_response_to_message_id", ""
            )

            msg_message_id = decoded_fields.get(
                "message_id", message_id
            )  # Use Redis stream ID as fallback

            # CRITICAL: Skip processing messages from this client to avoid infinite loops
            if sender_id == self.client_id:
                logger.debug("Skipping own message to avoid infinite loop")
                return

            # Note: We allow worker responses to be seen by other workers
            # This enables workers to see each other's responses in the chat
            # Individual workers will decide whether to respond based on should_respond logic later

            # Determine message type
            is_direct = bool(target_id and target_id != "")
            is_personal = stream_name == self.personal_stream
            message_type = "📩 Direct" if is_direct else "📢 Broadcast"
            stream_type = "Personal" if is_personal else "Global"

            # Log the received message
            logger.info(
                f"Received {message_type} message: {message_text} | Stream: {stream_type} | Message ID: {msg_message_id}"
            )

            # Generate AI response if message is not empty and client has Mistral configured
            if message_text.strip() and MISTRAL_CLIENT:
                # Respond to broadcast messages or direct messages targeted at this client
                # Can respond to responses from OTHER workers (enables worker-to-worker conversation)
                should_respond = (
                    not is_direct  # Respond to broadcast messages (including responses from other workers)
                    or (
                        is_direct and target_id == self.client_id
                    )  # Respond to direct messages for this client
                )

                if should_respond:
                    ai_response = await self.generate_ai_response(
                        message_text, sender_id
                    )

                    if ai_response:
                        # Publish the response to the global stream using the message ID
                        success = await self.publish_response(
                            msg_message_id, ai_response, sender_id
                        )
                        if not success:
                            logger.warning("Failed to publish AI response")
                    else:
                        logger.info("No AI response generated")
                else:
                    if in_response_to_message_id:
                        logger.debug(
                            "Skipping response - message is already a response"
                        )
                    else:
                        logger.debug(
                            "Skipping response - direct message not targeted at this client"
                        )
            else:
                if not message_text.strip():
                    logger.debug("Skipping empty message")
                elif not MISTRAL_CLIENT:
                    logger.debug("Mistral not configured, skipping response")

            logger.debug(f"Finished processing message {message_id}")

        except Exception as e:
            logger.error(f"Error processing message {message_id}: {e}")

    async def stop(self) -> None:
        """Stop the consumer."""
        logger.info("Stopping chat message consumption")
        self.is_running = False
        if self.redis_conn:
            try:
                await self.redis_conn.aclose()  # Use aclose() instead of deprecated close()
                logger.info("Redis connection closed")
            except (Exception, SystemExit) as e:
                # Ignore exceptions during cleanup, especially ClientInitiatedDisconnect (which inherits from SystemExit)
                logger.debug(f"Exception during Redis cleanup: {e}")
                pass


if __name__ == "__main__":
    logger.info(f"Starting client with ID: {CLIENT_ID}, Name: {CLIENT_NAME}")
    try:
        asyncio.run(connect_and_send_updates())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
    finally:
        # This ensures the flag is set if shutdown initiated by Ctrl+C
        manual_disconnect_initiated = True
        logger.info("Final cleanup.")
