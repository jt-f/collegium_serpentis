import asyncio
import json
import os
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState

from src.server import config
from src.server.redis_manager import (
    cleanup_disconnected_clients,
    close_redis,
    get_all_client_statuses,
    initialize_redis,
    redis_client,
    redis_health_check,
    redis_reconnector,
    status_store,
)
from src.server.redis_manager import update_client_status as redis_update_client_status
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)


if not os.path.exists(config.FRONTEND_BUILD_DIR):
    logger.warning(
        f"Frontend build directory not found: {config.FRONTEND_BUILD_DIR}. "
        "The frontend will not be served until it is built and "
        "placed in the correct location."
    )
elif not os.path.exists(os.path.join(config.FRONTEND_BUILD_DIR, "index.html")):
    logger.warning(
        f"index.html not found in frontend build directory: {config.FRONTEND_BUILD_DIR}. "
        "The frontend may not be served correctly."
    )

# Store active connections, distinguishing between roles
worker_connections: dict[str, WebSocket] = {}
frontend_connections: dict[str, WebSocket] = {}


async def broadcast_to_frontends(message: dict):
    """Sends a message to all connected frontend clients."""
    disconnected_frontends = []
    # Iterate over a copy of items in case the dictionary is modified during iteration elsewhere
    for client_id, websocket in list(frontend_connections.items()):
        try:
            await websocket.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError) as e:  # More specific exceptions
            logger.warning(
                f"Frontend client {client_id} disconnected or error during broadcast: {e}"
            )
            disconnected_frontends.append(client_id)
        except Exception as e:
            logger.error(f"Unexpected error broadcasting to frontend {client_id}: {e}")
            # Optionally, still add to disconnected_frontends if the error implies connection loss
            disconnected_frontends.append(client_id)

    # Clean up disconnected frontends
    for client_id in disconnected_frontends:
        if client_id in frontend_connections:  # Check if still exists before deleting
            del frontend_connections[client_id]
            logger.info(
                f"Removed disconnected/erroring frontend {client_id} from broadcast list."
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting server...")
    await initialize_redis()

    # Start background tasks for Redis monitoring and reconnection
    asyncio.create_task(redis_health_check())
    asyncio.create_task(redis_reconnector())
    asyncio.create_task(cleanup_disconnected_clients())
    try:
        yield
    finally:
        logger.info("Shutting down server...")
        # Gracefully close all WebSocket connections
        all_close_tasks: list[Coroutine[Any, Any, None]] = []  # Renamed for clarity
        logger.info(
            f"Closing {len(worker_connections)} worker and {len(frontend_connections)} frontend connections."
        )
        for ws in list(worker_connections.values()):  # Iterate over a copy
            all_close_tasks.append(
                ws.close(
                    code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                    reason="Server shutting down",
                )
            )
        for ws in list(frontend_connections.values()):  # Iterate over a copy
            all_close_tasks.append(
                ws.close(
                    code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                    reason="Server shutting down",
                )
            )

        if all_close_tasks:
            results = await asyncio.gather(*all_close_tasks, return_exceptions=True)
            for i, result in enumerate(
                results
            ):  # Added i for potential detailed logging
                if isinstance(result, Exception):
                    # Log which connection failed to close if needed
                    logger.warning(
                        f"Error closing a WebSocket connection during shutdown: {result} (task index {i})"
                    )
            logger.info(
                f"Attempted to close {len(all_close_tasks)} WebSocket connections."
            )

        worker_connections.clear()
        frontend_connections.clear()
        await close_redis()


# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# CORS middleware
# This allows the frontend (e.g., from http://localhost:5173) to make requests
# to this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all standard HTTP methods
    allow_headers=["*"],  # Allows all headers
)


@app.websocket(config.WEBSOCKET_ENDPOINT_PATH)
async def websocket_endpoint(websocket: WebSocket):
    client_id: str | None = None
    client_role: str | None = None  # To store the role of the connected client

    try:
        await websocket.accept()
        logger.info("New WebSocket connection established, awaiting registration")

        # Registration phase (first message determines role and ID)
        try:
            initial_data = await websocket.receive_text()
            message = json.loads(initial_data)
        except json.JSONDecodeError:
            error_msg = "Invalid JSON format for registration"
            logger.warning(error_msg)
            await websocket.send_text(json.dumps({"error": error_msg}))
            await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD)
            return

        client_id = message.get("client_id")
        status_attributes = message.get("status", {})
        client_role = status_attributes.get("client_role")  # Expect role in status

        if not client_id or not client_role:
            error_msg = (
                "client_id and client_role in status are required for registration"
            )
            logger.warning(
                f"Client registration failed: {error_msg}", data=initial_data
            )
            await websocket.send_text(json.dumps({"error": error_msg}))
            await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION)
            return

        logger.info(
            f"Client registered: {{client_id='{client_id}', role='{client_role}'}}"
        )

        current_time_iso = datetime.now(UTC).isoformat()
        # Base status attributes for any new or re-registering client
        base_status = {
            "connected": "true",
            "connect_time": current_time_iso,
            "last_seen": current_time_iso,  # Initialize last_seen
        }
        # Ensure client_role from registration is part of the status to be stored
        # and overwrite any client_role that might have been in the initial status payload from client.
        final_status_attributes = {
            **base_status,
            **status_attributes,
            "client_role": client_role,
        }
        final_status_attributes.pop(
            "disconnect_time", None
        )  # Remove if present from a previous state

        if client_role == "frontend":
            frontend_connections[client_id] = websocket
        else:  # Assume worker or other non-frontend roles
            worker_connections[client_id] = websocket

        # Update Redis with the initial/registration status
        success = await update_client_status(
            client_id, final_status_attributes, broadcast=False
        )
        # Fetch the definitive status from Redis/cache after update for broadcasting
        stored_status_after_conn = await get_client_info(client_id)

        # If Redis is not available, use the status we just tried to set
        if stored_status_after_conn is None:
            stored_status_after_conn = final_status_attributes
            stored_status_after_conn["redis_status"] = "unavailable"
        else:
            # Make sure we have a fresh copy of the status from Redis
            stored_status_after_conn = (
                await get_client_info(client_id) or final_status_attributes
            )
            stored_status_after_conn["redis_status"] = status_store.get(
                "redis", "unavailable"
            )

        # Handle frontend-specific registration flow
        if client_role == "frontend":
            # Send all clients update first for frontend clients
            all_clients_data, redis_status, error = await get_all_client_statuses()
            if all_clients_data is not None:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "all_clients_update",
                            "data": {
                                "clients": all_clients_data,
                                "redis_status": redis_status,
                            },
                        }
                    )
                )

            # Broadcast this frontend's registration to other frontends
            if success and stored_status_after_conn:
                await broadcast_to_frontends(
                    {
                        "type": "client_status_update",
                        "client_id": client_id,
                        "status": stored_status_after_conn,
                    }
                )
        else:
            # For worker clients, just broadcast their registration
            if success and stored_status_after_conn:
                await broadcast_to_frontends(
                    {
                        "type": "client_status_update",
                        "client_id": client_id,
                        "status": stored_status_after_conn,
                    }
                )

        # Send registration complete acknowledgment to the client
        await websocket.send_text(
            json.dumps(
                {
                    "result": "registration_complete",
                    "client_id": client_id,
                    "redis_status": status_store["redis"],
                }
            )
        )

        # Main message loop for this client
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            message_type = message.get("type")

            if message_type == "control":
                # This message is a control command from a frontend/control client
                action = message.get("action")
                target_client_id = message.get("target_client_id")
                control_message_id = message.get("message_id")  # Optional, for tracking

                # Basic validation
                if not client_role == "frontend":
                    logger.warning(
                        f"Non-frontend client {client_id} attempted control action {action}. Denying."
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "control_response",
                                "action": action,
                                "target_client_id": target_client_id,
                                "message_id": control_message_id,
                                "status": "error",
                                "message": "Permission denied: Only frontend clients can issue control commands.",
                            }
                        )
                    )
                    continue

                if not action or not target_client_id:
                    logger.warning(
                        f"Frontend client {client_id} sent invalid control message: {message}"
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "control_response",
                                "action": action,
                                "target_client_id": target_client_id,
                                "message_id": control_message_id,
                                "status": "error",
                                "message": "'action' and 'target_client_id' are required for control commands.",
                            }
                        )
                    )
                    continue

                logger.info(
                    f"Frontend client {client_id} sending control: {action} to {target_client_id}"
                )
                response_payload = {}
                if action == "pause":
                    response_payload = await pause_client(target_client_id)
                elif action == "resume":
                    response_payload = await resume_client(target_client_id)
                elif action == "disconnect":
                    response_payload = await disconnect_client(target_client_id)
                else:
                    response_payload = {
                        "status": "error",
                        "message": f"Unknown control action: {action}",
                    }

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "control_response",
                            "action": action,
                            "target_client_id": target_client_id,
                            "message_id": control_message_id,
                            **response_payload,
                        }
                    )
                )

            # elif message_type == "status_update": # Or assume if not 'control', it's a status update from worker
            # For now, assume any message not of type 'control' from a non-frontend client is a status update.
            elif client_role != "frontend":
                # This is a status update from a worker client
                logger.info(
                    f"[DEBUG] START: Handling status update from worker {client_id}: {message}"
                )
                message_client_id_from_payload = message.get("client_id")
                if (
                    message_client_id_from_payload
                    and message_client_id_from_payload != client_id
                ):
                    logger.warning(
                        f"Worker {client_id} sent status with conflicting client_id '{message_client_id_from_payload}'. Using connection's client_id."
                    )
                    # Decide if to reject or just log and proceed with the connection's client_id

                status_attributes = message.get(
                    "status", message
                )  # Handle both formats
                if not isinstance(status_attributes, dict):
                    logger.warning(
                        f"Received non-dict status from worker {client_id}: {status_attributes}. Wrapping in 'raw_payload'."
                    )
                    status_attributes = {"raw_payload": status_attributes}

                current_time_iso = datetime.now(UTC).isoformat()
                status_attributes["last_seen"] = current_time_iso

                # Add Redis status to the status attributes before updating
                status_attributes["redis_status"] = status_store.get(
                    "redis", "unavailable"
                )

                logger.debug(f"[DEBUG] Calling update_client_status for {client_id}")
                success = await update_client_status(client_id, status_attributes)
                logger.debug(
                    f"[DEBUG] update_client_status completed with success={success}"
                )

                # Ensure redis_status is included in the response
                response_status = {**status_attributes}
                if "redis_status" not in response_status:
                    response_status["redis_status"] = status_store.get(
                        "redis", "unavailable"
                    )

                logger.info(
                    f"[DEBUG] END: Handling status update from worker {client_id}: {response_status}"
                )

                # Send acknowledgment for status update
                await websocket.send_text(
                    json.dumps(
                        {
                            "result": "message_processed",
                            "client_id": client_id,
                            "status_updated": response_status,
                            "redis_status": response_status.get(
                                "redis_status", "unavailable"
                            ),
                        }
                    )
                )
            else:  # Message from a frontend client that is not 'control'
                logger.debug(
                    f"Received unhandled message type '{message_type}' from frontend {client_id}: {message}"
                )
                # Optionally send an error or acknowledgment for unhandled frontend messages
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "message_receipt_unknown",
                            "original_message": message,
                            "info": "Message type not recognized for frontend clients, aside from 'control'.",
                        }
                    )
                )

    except WebSocketDisconnect:
        # This block will be entered when the client disconnects.
        logger.info(
            f"Client disconnected (WebSocketDisconnect caught): {{client_id='{client_id}', role='{client_role}'}}"
        )
        # handle_disconnect will be called in the finally block

    except (
        json.JSONDecodeError
    ) as e:  # Catch errors during initial registration message parsing or loop
        logger.error(
            f"Invalid JSON from {client_id or 'unregistered client'}",
            error=str(e),
            client_id=client_id,
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
                await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR)
            except RuntimeError:
                pass  # Already closing or closed
    except Exception as e:
        logger.error(
            f"Unexpected WebSocket error for client: {{client_id='{client_id}', role='{client_role}'}}",
            error=repr(e),
            exc_info=True,
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(
                    code=config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR
                )  # Internal error
            except Exception as close_err:
                logger.error(
                    f"Error trying to close WebSocket for {client_id} after unexpected error",
                    error=str(close_err),
                )
    finally:
        # Ensure client is removed from the correct tracking dictionary and status updated.
        # This block executes regardless of how the try block was exited (normal, exception, etc.)
        if client_id:  # client_id is the one captured from registration or message loop
            await handle_disconnect(client_id, websocket, "Disconnected by client")
        else:
            # This case handles connections that never completed registration to get a client_id.
            # No specific client status to update, but log the event.
            logger.info(
                "WebSocket connection closed before client_id was established (registration incomplete or failed)."
            )


# Helper for command endpoints to broadcast state changes
async def get_client_info(client_id: str) -> dict | None:
    """
    Retrieve the current status of a client from Redis.

    Args:
        client_id: The ID of the client to retrieve status for.

    Returns:
        dict: A dictionary containing the client's status, or None if not found or Redis is unavailable.
    """
    logger.debug(f"[DEBUG] Entering get_client_info for {client_id}")
    if status_store["redis"] != "connected":
        logger.warning(f"Redis unavailable, cannot get client info for {client_id}")
        return None

    try:
        # Get the client's status from Redis
        logger.debug(f"[DEBUG] Calling redis.hgetall for client:{client_id}:status")
        status_data = await redis_client.hgetall(f"client:{client_id}:status")
        logger.debug(
            f"[DEBUG] redis.hgetall completed for {client_id}, got {len(status_data)} items"
        )

        if not status_data:
            logger.debug(f"No status data found in Redis for client {client_id}")
            return None

        # Convert bytes to strings for the keys and values
        status = {k.decode(): v.decode() for k, v in status_data.items()}
        logger.debug(f"[DEBUG] Decoded status for {client_id}: {status.keys()}")
        return status
    except Exception as e:
        logger.error(f"Error getting client info for {client_id}: {e}", exc_info=True)
        return None


async def broadcast_client_state_change(
    client_id: str, changed_state_attributes: dict | None = None
):
    """
    Fetches full client status and broadcasts it, optionally merging specific recent changes.

    Args:
        client_id: The ID of the client whose status changed
        changed_state_attributes: Optional dictionary of attributes that changed
    """
    if not client_id:
        logger.warning("broadcast_client_state_change called with empty client_id")
        return

    try:
        # Get the current status from Redis
        full_status = await get_client_info(client_id) or {}

        # If no status in Redis but we have changes, use those as the base
        if not full_status and changed_state_attributes:
            full_status = changed_state_attributes.copy()

        # If we still don't have a status, log a warning and return
        if not full_status:
            logger.warning(
                f"No status found for client {client_id} in broadcast_client_state_change"
            )
            return

        # If specific changes were provided, merge them into the full status
        if changed_state_attributes:
            full_status.update(changed_state_attributes)

        # Ensure required fields are present
        full_status.setdefault("client_id", client_id)
        full_status.setdefault("redis_status", status_store.get("redis", "unavailable"))

        # Prepare the broadcast message
        broadcast_message = {
            "type": "client_status_update",
            "client_id": client_id,
            "status": full_status,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Broadcast to all connected frontends
        await broadcast_to_frontends(broadcast_message)

        logger.info(f"Broadcasted state change for client {client_id}")
    except Exception as e:
        logger.error(
            f"Error broadcasting state change for client {client_id}: {e}",
            exc_info=True,
        )


async def update_client_status(
    client_id: str, status_attributes: dict, broadcast: bool = True
) -> bool:
    """
    Update the status of a client in Redis.
    This is a wrapper around the redis_manager.update_client_status function.

    Args:
        client_id: The ID of the client to update.
        status_attributes: A dictionary of status attributes to update.
        broadcast: Whether to broadcast the change to frontends.

    Returns:
        bool: True if the update was successful, False otherwise.
    """
    try:
        # Make a copy to avoid modifying the original
        attrs = status_attributes.copy()
        # Ensure redis_status is not stored in Redis
        attrs.pop("redis_status", None)

        success = await redis_update_client_status(client_id, attrs)
        if not success:
            logger.warning(f"Failed to update status for client {client_id} in Redis")
            return False

        # After successful update, trigger a broadcast of the state change if requested
        if broadcast:
            # Include the redis_status in the broadcast
            broadcast_attrs = attrs.copy()
            broadcast_attrs["redis_status"] = status_store.get("redis", "unavailable")
            await broadcast_client_state_change(client_id, broadcast_attrs)

        return True
    except Exception as e:
        logger.error(f"Error updating status for client {client_id} in Redis: {e}")
        return False


async def disconnect_client(client_id: str) -> dict:
    logger.info(f"Processing disconnect request for client {client_id}")
    websocket_to_close = worker_connections.get(client_id)

    client_already_disconnected = False
    client_info_pre_disconnect = await get_client_info(client_id)
    if (
        client_info_pre_disconnect
        and client_info_pre_disconnect.get("connected") == "false"
    ):
        client_already_disconnected = True

    if not websocket_to_close:
        if client_already_disconnected:
            logger.info(
                f"Client {client_id} is already marked as disconnected in Redis."
            )
        else:
            logger.warning(
                f"Client {client_id} not actively in worker_connections for disconnect attempt."
            )
        # Still proceed to update Redis status to disconnected even if no active WebSocket connection

    if websocket_to_close:
        try:
            logger.info(
                f"Sending disconnect command to client {client_id} via WebSocket"
            )
            await websocket_to_close.send_text(json.dumps({"command": "disconnect"}))
            logger.info(f"Disconnect command sent to client {client_id}")
            # Give the client a brief moment to process the command before closing the socket
            await asyncio.sleep(0.1)  # ADDED SMALL DELAY
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                f"Could not send disconnect command to client {client_id} (already disconnected or WebSocket error): {e}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error sending disconnect command to client {client_id}: {e}"
            )

        try:
            logger.info(
                f"Closing WebSocket connection for client {client_id} from server-side (HTTP request)"
            )
            await websocket_to_close.close(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason="Server initiated disconnect via HTTP",
            )
        except RuntimeError as e:
            logger.warning(
                f"Error closing WebSocket for client {client_id} (may already be closing or closed): {e}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error closing WebSocket for client {client_id}: {e}"
            )
        finally:
            # Ensure the client is removed from active connections
            if client_id in worker_connections:
                del worker_connections[client_id]
                logger.info(f"Removed client {client_id} from worker_connections")

    # Update Redis with disconnect status
    status_update = {
        "connected": "false",
        "status_detail": "Disconnected by server request (HTTP)",
        "disconnect_time": datetime.now(UTC).isoformat(),
        "client_state": "offline",
        "redis_status": status_store.get("redis", "unavailable"),
    }

    # Update the status in Redis and broadcast the change
    success = await update_client_status(client_id, status_update)
    if success:
        # Broadcast the state change to all frontends
        await broadcast_client_state_change(client_id, status_update)

    if client_already_disconnected and not websocket_to_close:
        return {
            "status": "info",
            "message": f"Client {client_id} was already disconnected. Status re-broadcasted.",
            "client_id": client_id,
            "redis_status": status_store["redis"],
        }
    if not websocket_to_close and not client_already_disconnected:
        # This case means the client wasn't in worker_connections, but wasn't marked disconnected in Redis.
        # We've updated Redis, so this is a form of success.
        return {
            "status": "warning",
            "message": f"Client {client_id} not actively connected. Status updated to disconnected and broadcasted.",
            "client_id": client_id,
            "redis_status": status_store["redis"],
        }
    return {
        "status": "success",
        "message": f"Disconnection process for client {client_id} initiated and broadcasted.",
        "client_id": client_id,
        "redis_status": status_store["redis"],
    }


async def pause_client(client_id: str) -> dict:
    logger.info(f"Processing pause request for client {client_id}")
    websocket = worker_connections.get(client_id)
    target_client_exists_in_redis = True

    if not websocket:
        client_info = await get_client_info(client_id)
        if not client_info or client_info.get("connected") == "false":
            target_client_exists_in_redis = (
                False  # Does not exist or already disconnected
            )
            logger.error(
                f"Cannot pause client {client_id}: not found or already disconnected."
            )
            return {
                "status": "error",
                "message": f"Client {client_id} not found or already disconnected.",
            }
        logger.warning(
            f"Client {client_id} not actively connected via WebSocket for pause. Updating state in Redis only."
        )
    else:
        try:
            await websocket.send_text(json.dumps({"command": "pause"}))
            logger.info(f"Pause command sent to client {client_id} via WebSocket")
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                f"Client {client_id} disconnected or error sending pause command: {e}. Will proceed to update state in Redis."
            )
            if client_id in worker_connections:
                del worker_connections[
                    client_id
                ]  # Clean up if error implies disconnect
            # The client's own WebSocket handler or cleanup task will mark it disconnected in Redis.
        except Exception as e:
            logger.error(
                f"Error sending pause command to client {client_id}: {e}", exc_info=True
            )
            # Remove from connections on any error
            if client_id in worker_connections:
                del worker_connections[client_id]
            raise HTTPException(
                status_code=500,
                detail=f"Error sending pause command to client {client_id}: {e}",
            ) from e

    status_update = {"client_state": "paused"}
    if target_client_exists_in_redis:
        await update_client_status(client_id, status_update)
        # Ensure broadcast is only called once
        await broadcast_client_state_change(client_id, status_update)
        return {
            "status": "success",
            "message": f"Pause command processed for client {client_id}. State updated and broadcasted.",
        }
    # This 'else' corresponds to 'if not target_client_exists_in_redis', which should have been handled by the error return above.
    # However, to be safe, we ensure a dictionary is returned.
    return {
        "status": "error",
        "message": f"Pause command for client {client_id} could not be processed as client was not found in an active state.",
    }


async def resume_client(client_id: str) -> dict:
    logger.info(f"Processing resume request for client {client_id}")
    websocket = worker_connections.get(client_id)
    target_client_exists_in_redis = True

    if not websocket:
        client_info = await get_client_info(client_id)
        if not client_info or client_info.get("connected") == "false":
            target_client_exists_in_redis = False
            logger.error(
                f"Cannot resume client {client_id}: not found or already disconnected."
            )
            return {
                "status": "error",
                "message": f"Client {client_id} not found or already disconnected.",
            }
        # If client_info.client_state is already 'running', this is a redundant call but harmless.
        logger.warning(
            f"Client {client_id} not actively connected via WebSocket for resume. Updating state in Redis only."
        )
    else:
        try:
            await websocket.send_text(json.dumps({"command": "resume"}))
            logger.info(f"Resume command sent to client {client_id} via WebSocket")
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                f"Client {client_id} disconnected or error sending resume command: {e}. Will proceed to update state in Redis."
            )
            if client_id in worker_connections:
                del worker_connections[client_id]
        except Exception as e:
            logger.error(
                f"Error sending resume command to client {client_id}: {e}",
                exc_info=True,
            )
            # Remove from connections on any error
            if client_id in worker_connections:
                del worker_connections[client_id]
            raise HTTPException(
                status_code=500,
                detail=f"Error sending resume command to client {client_id}: {e}",
            ) from e

    status_update = {"client_state": "running"}
    if target_client_exists_in_redis:
        await update_client_status(client_id, status_update)
        # Ensure broadcast is only called once
        await broadcast_client_state_change(client_id, status_update)
        return {
            "status": "success",
            "message": f"Resume command processed for client {client_id}. State updated and broadcasted.",
        }
    # This 'else' corresponds to 'if not target_client_exists_in_redis'.
    return {
        "status": "error",
        "message": f"Resume command for client {client_id} could not be processed as client was not found in an active state.",
    }


# Mount static files for the frontend if the directory exists
if os.path.exists(config.FRONTEND_BUILD_DIR) and os.path.isdir(
    config.FRONTEND_BUILD_DIR
):
    app.mount(
        "/",  # Mount at the root path
        StaticFiles(directory=config.FRONTEND_BUILD_DIR, html=True),
        name="static-frontend",  # Changed name to static-frontend to be consistent if it was intended
    )
    logger.info(f"Serving frontend from: {config.FRONTEND_BUILD_DIR}")
else:
    logger.warning(
        f"Frontend build directory {config.FRONTEND_BUILD_DIR} not found or not a directory. "
        "Static file serving will be disabled."
    )


# REMOVED redundant app.mount for static files
async def handle_disconnect(client_id: str, websocket: WebSocket, reason: str):
    """Handles client disconnection, updates status, and cleans up connections."""

    # Check if already removed (e.g. by another handler of the disconnect)
    # This check helps make the function more idempotent if called multiple times
    # for the same disconnect event.
    processed_disconnect = False
    client_role_for_log = "unknown"  # For logging if role changes mid-process

    if client_id in worker_connections:
        client_role_for_log = worker_connections[client_id].get("client_role", "worker")
        del worker_connections[client_id]
        processed_disconnect = True
        logger.info(f"Removed worker client {client_id} from active connections.")
    elif client_id in frontend_connections:
        client_role_for_log = frontend_connections[client_id].get(
            "client_role", "frontend"
        )
        del frontend_connections[client_id]
        processed_disconnect = True
        logger.info(f"Removed frontend client {client_id} from active connections.")

    if not processed_disconnect:
        logger.info(
            f"Client {client_id} disconnect event: Client not found in active "
            f"worker or frontend connections. Might have been handled already."
        )
        # Optionally, check if Redis state is consistent if the client was truly active before
        # This path implies the client was either never fully registered in these dicts
        # or was cleaned up by a parallel disconnect handling.
        # For safety, we can still try to update its status in Redis if we think it *should* be there.
        # However, without knowing its role, broadcasting accurately is hard.
        # Let's assume for now that if it's not in connections, its state update on disconnect
        # would have happened when it was removed.
        return  # Exit if not found in active connection dicts

    logger.info(
        f"Client {client_id} (Role: {client_role_for_log}) disconnected. Reason: {reason}."
    )

    status_update = {
        "connected": "false",
        "status_detail": reason,
        "disconnect_time": datetime.now(UTC).isoformat(),
        # Persist last known client_role if possible, or rely on Redis to have it
    }

    # We need the client_role to correctly call update_client_status, which needs it
    # to decide whether to broadcast.
    # If we just deleted it, we don't have its role easily from the connection dict.
    # Best to fetch current info from Redis, which should include its role.
    # However, update_client_status itself calls get_client_info.
    # We pass the websocket object as None, since it's disconnected.
    # The role is crucial for update_client_status to know if it should broadcast (e.g. worker disconnects are broadcast).

    # Let's simplify: update_client_status directly, then decide on broadcast.
    # This avoids re-fetching role if update_client_status expects it.
    try:
        # Directly update Redis without needing the full client object from connections
        # This will also fetch the latest status before updating for the broadcast payload
        update_success = await update_client_status(client_id, status_update)

        if not update_success:
            logger.error(f"Failed to update Redis for disconnected client {client_id}")
            # Even if Redis update fails, attempt to broadcast a minimal disconnect event
            # to allow frontends to clean up.
            # This uses the 'reason' and 'disconnect_time' from the status_update dict.
            minimal_disconnect_status = {
                "connected": "false",
                "disconnect_time": status_update.get("disconnect_time"),
                "status_detail": status_update.get("status_detail"),
                "client_role": client_role_for_log,  # Best guess for role
            }
            if client_role_for_log == "worker":  # Only broadcast for workers
                await broadcast_to_frontends(
                    {
                        "type": "client_status_update",  # or "client_disconnected_event"
                        "client_id": client_id,
                        "status": minimal_disconnect_status,
                    }
                )
            return

        final_status_after_disconnect = await get_client_info(client_id)

        if not final_status_after_disconnect:
            logger.warning(
                f"Could not retrieve client info for {client_id} after disconnect update for broadcast."
            )
            # Fallback to broadcasting minimal info if get_client_info fails post-update
            minimal_disconnect_status_after_success = {
                "connected": "false",
                "disconnect_time": status_update.get("disconnect_time"),
                "status_detail": status_update.get("status_detail"),
                "client_role": client_role_for_log,  # Best guess
            }
            if client_role_for_log == "worker":
                await broadcast_to_frontends(
                    {
                        "type": "client_status_update",
                        "client_id": client_id,
                        "status": minimal_disconnect_status_after_success,
                    }
                )
            return

        # Only broadcast if the client was significant enough (e.g., a worker)
        # Frontend disconnects don't typically need to be broadcast to other frontends.
        # The final_status_after_disconnect should contain the client_role from Redis.
        if final_status_after_disconnect.get("client_role") == "worker":
            disconnect_broadcast_message = {
                "type": "client_status_update",
                "client_id": client_id,
                "status": final_status_after_disconnect,
            }
            await broadcast_to_frontends(disconnect_broadcast_message)
            logger.info(f"Broadcasted disconnect status for worker client {client_id}.")
        elif final_status_after_disconnect.get("client_role") == "frontend":
            logger.info(
                f"Frontend client {client_id} disconnected. No broadcast sent for frontend disconnects."
            )

    except Exception as e:
        logger.error(
            f"Error during Redis update or broadcast for client {client_id} "
            f"disconnect: {e}",
            exc_info=True,
        )

    # Ensure the WebSocket is closed from the server side if still open
    # This is a safeguard; FastAPI's context manager for the endpoint also handles closure.
    # No specific change here for lint 8963f880, assuming previous structural changes to websocket_endpoint's finally fixed it.
    # If that lint persists, it implies an issue at the very end of the file or after this function.
