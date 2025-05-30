import asyncio
import json
import os
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from datetime import timezone, datetime
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

worker_connections: dict[str, WebSocket] = {}
frontend_connections: dict[str, WebSocket] = {}


async def broadcast_to_frontends(message: dict):
    """Sends a message to all connected frontend clients."""
    disconnected_frontends = []
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

    asyncio.create_task(redis_health_check())
    asyncio.create_task(redis_reconnector())
    asyncio.create_task(cleanup_disconnected_clients())
    try:
        yield
    finally:
        logger.info("Shutting down server...")
        all_close_tasks: list[Coroutine[Any, Any, None]] = []
        logger.info(
            f"Closing {len(worker_connections)} worker and {len(frontend_connections)} frontend connections."
        )
        for ws in list(worker_connections.values()):
            all_close_tasks.append(
                ws.close(
                    code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                    reason=config.REASON_SERVER_SHUTDOWN,
                )
            )
        for ws in list(frontend_connections.values()):
            all_close_tasks.append(
                ws.close(
                    code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                    reason=config.REASON_SERVER_SHUTDOWN,
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


app = FastAPI(lifespan=lifespan)

# This allows the frontend (e.g., from http://localhost:5173) to make requests
# to this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all standard HTTP methods
    allow_headers=["*"],  # Allows all headers
)


# REST API Endpoints (must be before static file mounting)
@app.get(config.STATUSES_ENDPOINT_PATH)
async def get_all_statuses():
    """Get all client statuses via HTTP (for frontend polling fallback)."""
    try:
        clients_data, redis_status_str, error_msg = await get_all_client_statuses()

        response = {
            config.PAYLOAD_KEY_CLIENTS: clients_data if clients_data is not None else {},
            config.STATUS_KEY_REDIS_STATUS: redis_status_str or config.STATUS_VALUE_REDIS_UNKNOWN,
        }

        if error_msg:
            response[config.PAYLOAD_KEY_ERROR_REDIS] = error_msg

        return response
    except Exception as e:
        logger.error(
            f"Error in GET {config.STATUSES_ENDPOINT_PATH}: {e}", exc_info=True
        )
        return {
            config.PAYLOAD_KEY_CLIENTS: {},
            config.STATUS_KEY_REDIS_STATUS: config.STATUS_VALUE_REDIS_UNKNOWN,
            config.MSG_TYPE_ERROR: "Failed to retrieve client statuses",
        }


@app.get(config.HEALTH_ENDPOINT_PATH)
async def health_check():
    """Health check endpoint."""
    return {
        config.PAYLOAD_KEY_STATUS: config.HEALTH_STATUS_HEALTHY,
        config.STATUS_KEY_REDIS_STATUS: status_store.get(config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNKNOWN),
        "active_workers": len(worker_connections),
        "active_frontends": len(frontend_connections),
    }


@app.websocket(config.WEBSOCKET_ENDPOINT_PATH)
async def websocket_endpoint(websocket: WebSocket):
    client_id: str | None = None
    client_role: str | None = None

    try:
        await websocket.accept()
        logger.info("New WebSocket connection established, awaiting registration")

        # Registration phase (first message determines role and ID)
        try:
            initial_data = await websocket.receive_text()
            message = json.loads(initial_data)
        except json.JSONDecodeError:
            error_msg = config.ERROR_MSG_INVALID_JSON_REGISTRATION
            logger.warning(error_msg)
            await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
            await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD) # Ensure this constant exists
            return

        client_id = message.get(config.PAYLOAD_KEY_CLIENT_ID)
        status_attributes = message.get(config.PAYLOAD_KEY_STATUS, {})
        client_role = status_attributes.get(config.STATUS_KEY_CLIENT_ROLE)  # Expect role in status

        if not client_id or not client_role:
            error_msg = config.ERROR_MSG_REGISTRATION_MISSING_IDS
            logger.warning(
                f"Client registration failed: {error_msg}", data=initial_data
            )
            await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
            await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION) # Ensure this constant exists
            return

        logger.info(
            f"Client registered: {{client_id='{client_id}', role='{client_role}'}}"
        )

        current_time_iso = datetime.now(timezone.utc).isoformat()
        # Base status attributes for any new or re-registering client
        base_status = {
            config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED,
            config.STATUS_KEY_CONNECT_TIME: current_time_iso,
            config.STATUS_KEY_LAST_SEEN: current_time_iso,  # Initialize last_seen
        }
        # Ensure client_role from registration is part of the status to be stored
        # and overwrite any client_role that might have been in the initial status payload from client.
        final_status_attributes = {
            **base_status,
            **status_attributes,
            config.STATUS_KEY_CLIENT_ROLE: client_role,
        }
        final_status_attributes.pop(
            config.STATUS_KEY_DISCONNECT_TIME, None
        )  # Remove if present from a previous state

        if client_role == config.CLIENT_ROLE_FRONTEND:
            frontend_connections[client_id] = websocket
        else:  # Assume worker or other non-frontend roles
            worker_connections[client_id] = websocket

        success = await update_client_status(
            client_id, final_status_attributes, broadcast=False
        )
        # Fetch the definitive status from Redis/cache after update for broadcasting
        stored_status_after_conn = await get_client_info(client_id)

        # If Redis is not available, use the status we just tried to set
        # Also, ensure the client_role from registration is part of this fallback status
        if stored_status_after_conn is None:
            stored_status_after_conn = final_status_attributes.copy() # Use a copy
            stored_status_after_conn[config.STATUS_KEY_REDIS_STATUS] = config.STATUS_VALUE_REDIS_UNAVAILABLE
            # Ensure client_role is present from the registration step
            if config.STATUS_KEY_CLIENT_ROLE not in stored_status_after_conn and client_role:
                 stored_status_after_conn[config.STATUS_KEY_CLIENT_ROLE] = client_role
        else:
            # Make sure we have a fresh copy of the status from Redis
            # and ensure client_role is present
            retrieved_status = await get_client_info(client_id)
            if retrieved_status:
                stored_status_after_conn = retrieved_status
            else: # Fallback if get_client_info returns None (e.g. Redis error during fetch)
                stored_status_after_conn = final_status_attributes.copy()

            stored_status_after_conn[config.STATUS_KEY_REDIS_STATUS] = status_store.get(
                config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            # Ensure client_role from registration is authoritative if not present or different
            if client_role and stored_status_after_conn.get(config.STATUS_KEY_CLIENT_ROLE) != client_role:
                stored_status_after_conn[config.STATUS_KEY_CLIENT_ROLE] = client_role


        # Handle frontend-specific registration flow
        if client_role == config.CLIENT_ROLE_FRONTEND:
            # Send all clients update first for frontend clients
            all_clients_data, redis_status_val, error = await get_all_client_statuses() # Renamed redis_status to avoid conflict
            if all_clients_data is not None:
                await websocket.send_text(
                    json.dumps(
                        {
                            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_ALL_CLIENTS_UPDATE,
                            config.PAYLOAD_KEY_DATA: {
                                config.PAYLOAD_KEY_CLIENTS: all_clients_data,
                                config.STATUS_KEY_REDIS_STATUS: redis_status_val, # Use renamed variable
                            },
                        }
                    )
                )

            # Broadcast this frontend's registration to other frontends
            # Ensure stored_status_after_conn is not None before using it
            if success and stored_status_after_conn:
                await broadcast_to_frontends(
                    {
                        config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
                        config.PAYLOAD_KEY_CLIENT_ID: client_id,
                        config.PAYLOAD_KEY_STATUS: stored_status_after_conn,
                    }
                )
        else: # Worker or other client roles
            # For worker clients, just broadcast their registration
            # Ensure stored_status_after_conn is not None before using it
            if success and stored_status_after_conn:
                await broadcast_to_frontends(
                    {
                        config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
                        config.PAYLOAD_KEY_CLIENT_ID: client_id,
                        config.PAYLOAD_KEY_STATUS: stored_status_after_conn,
                    }
                )

        # Send registration complete acknowledgment to the client
        await websocket.send_text(
            json.dumps(
                {
                    config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_REGISTRATION_COMPLETE, # Changed from RESULT to TYPE for consistency
                    config.PAYLOAD_KEY_CLIENT_ID: client_id,
                    config.STATUS_KEY_REDIS_STATUS: status_store.get(config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNKNOWN), # Safer get
                }
            )
        )

        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            message_type = message.get(config.PAYLOAD_KEY_TYPE)

            if message_type == config.MSG_TYPE_CONTROL:
                # This message is a control command from a frontend/control client
                action = message.get(config.PAYLOAD_KEY_ACTION)
                target_client_id = message.get(config.PAYLOAD_KEY_TARGET_CLIENT_ID)
                control_message_id = message.get(config.PAYLOAD_KEY_MESSAGE_ID)  # Optional, for tracking

                if not client_role == config.CLIENT_ROLE_FRONTEND:
                    logger.warning(
                        f"Non-frontend client {client_id} attempted control action {action}. Denying."
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CONTROL_RESPONSE,
                                config.PAYLOAD_KEY_ACTION: action,
                                config.PAYLOAD_KEY_TARGET_CLIENT_ID: target_client_id,
                                config.PAYLOAD_KEY_MESSAGE_ID: control_message_id,
                                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                                config.PAYLOAD_KEY_MESSAGE: config.ERROR_MSG_CONTROL_PERMISSION_DENIED,
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
                                config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CONTROL_RESPONSE,
                                config.PAYLOAD_KEY_ACTION: action,
                                config.PAYLOAD_KEY_TARGET_CLIENT_ID: target_client_id,
                                config.PAYLOAD_KEY_MESSAGE_ID: control_message_id,
                                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                                config.PAYLOAD_KEY_MESSAGE: config.ERROR_MSG_CONTROL_INVALID_PAYLOAD,
                            }
                        )
                    )
                    continue

                logger.info(
                    f"Frontend client {client_id} sending control: {action} to {target_client_id}"
                )
                response_payload = {}
                if action == config.CONTROL_ACTION_PAUSE:
                    response_payload = await pause_client(target_client_id, client_id) # Pass originating client_id
                elif action == config.CONTROL_ACTION_RESUME:
                    response_payload = await resume_client(target_client_id, client_id) # Pass originating client_id
                elif action == config.CONTROL_ACTION_DISCONNECT:
                    response_payload = await disconnect_client(target_client_id, client_id) # Pass originating client_id
                else:
                    response_payload = {
                        config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                        config.PAYLOAD_KEY_MESSAGE: f"Unknown control action: {action}", # Keep as f-string for specific error
                    }

                await websocket.send_text(
                    json.dumps(
                        {
                            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CONTROL_RESPONSE,
                            config.PAYLOAD_KEY_ACTION: action,
                            config.PAYLOAD_KEY_TARGET_CLIENT_ID: target_client_id,
                            config.PAYLOAD_KEY_MESSAGE_ID: control_message_id,
                            **response_payload,
                        }
                    )
                )

            # elif message_type == "status_update": # Or assume if not 'control', it's a status update from worker
            # For now, assume any message not of type 'control' from a non-frontend client is a status update.
            elif client_role != config.CLIENT_ROLE_FRONTEND:
                # This is a status update from a worker client
                logger.info(
                    f"[DEBUG] START: Handling status update from worker {client_id}: {message}"
                )
                message_client_id_from_payload = message.get(config.PAYLOAD_KEY_CLIENT_ID)
                if (
                    message_client_id_from_payload
                    and message_client_id_from_payload != client_id
                ):
                    logger.warning(
                        f"Worker {client_id} sent status with conflicting client_id '{message_client_id_from_payload}'. Using connection's client_id."
                    )
                    # Decide if to reject or just log and proceed with the connection's client_id

                status_attributes = message.get(
                    config.PAYLOAD_KEY_STATUS, message
                )  # Handle both formats
                if not isinstance(status_attributes, dict):
                    logger.warning(
                        f"Received non-dict status from worker {client_id}: {status_attributes}. Wrapping in '{config.STATUS_KEY_RAW_PAYLOAD}'."
                    )
                    status_attributes = {config.STATUS_KEY_RAW_PAYLOAD: status_attributes}

                current_time_iso = datetime.now(timezone.utc).isoformat()
                status_attributes[config.STATUS_KEY_LAST_SEEN] = current_time_iso

                # Add Redis status to the status attributes before updating
                status_attributes[config.STATUS_KEY_REDIS_STATUS] = status_store.get(
                    config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
                )

                logger.debug(f"[DEBUG] Calling update_client_status for {client_id}")
                success = await update_client_status(client_id, status_attributes)
                logger.debug(
                    f"[DEBUG] update_client_status completed with success={success}"
                )

                # Ensure redis_status is included in the response
                response_status = {**status_attributes}
                if config.STATUS_KEY_REDIS_STATUS not in response_status:
                    response_status[config.STATUS_KEY_REDIS_STATUS] = status_store.get(
                        config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
                    )

                logger.info(
                    f"[DEBUG] END: Handling status update from worker {client_id}: {response_status}"
                )

                # Send acknowledgment for status update
                await websocket.send_text(
                    json.dumps(
                        {
                            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_MESSAGE_PROCESSED, # Changed from RESULT to TYPE
                            config.PAYLOAD_KEY_CLIENT_ID: client_id,
                            config.PAYLOAD_KEY_STATUS_UPDATED: response_status, # Ensure this constant exists
                            config.STATUS_KEY_REDIS_STATUS: response_status.get(
                                config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
                            ),
                        }
                    )
                )
            else:  # Message from a frontend client that is not 'control'
                logger.debug(
                    f"Received unhandled message type '{message_type}' from frontend {client_id}: {message}"
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_MESSAGE_RECEIPT_UNKNOWN,
                            config.PAYLOAD_KEY_ORIGINAL_MESSAGE: message, # Ensure this constant exists
                            config.PAYLOAD_KEY_INFO: config.INFO_MSG_UNHANDLED_FRONTEND_MSG,
                        }
                    )
                )

    except WebSocketDisconnect:
        logger.info(
            f"Client disconnected (WebSocketDisconnect caught): {{client_id='{client_id}', role='{client_role}'}}"
        )
        # No specific status update here as `finally` block will handle it.

    except json.JSONDecodeError as e:
        error_msg = config.ERROR_MSG_INVALID_JSON
        logger.error(
            f"{error_msg} from {client_id or 'unregistered client'}: {e}",
            exc_info=True, # Log full traceback for JSON errors
            client_id=client_id, # Ensure client_id is logged if available
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
                await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR)
            except RuntimeError: # pragma: no cover
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
            await handle_disconnect(client_id, websocket, config.REASON_DISCONNECTED_BY_CLIENT)
        else:
            # This case handles connections that never completed registration to get a client_id.
            # No specific client status to update, but log the event.
            logger.info(
                "WebSocket connection closed before client_id was established (registration incomplete or failed)."
            )


async def get_client_info(client_id: str) -> dict | None:
    """
    Retrieve the current status of a client from Redis.

    Args:
        client_id: The ID of the client to retrieve status for.

    Returns:
        dict: A dictionary containing the client's status, or None if not found or Redis is unavailable.
    """
    logger.debug(f"Entering get_client_info for {client_id}")
    # Use status_store.get for safer access to redis_status
    if status_store.get(config.STATUS_KEY_REDIS_STATUS) != config.STATUS_VALUE_CONNECTED:
        logger.warning(
            f"Redis unavailable (status: {status_store.get(config.STATUS_KEY_REDIS_STATUS)}), cannot get client info for {client_id}"
        )
        return None

    try:
        redis_key = f"{config.REDIS_CLIENT_INFO_KEY_PREFIX}{client_id}"
        logger.debug(f"Calling redis.hgetall for key: {redis_key}")
        status_data = await redis_client.hgetall(redis_key)
        logger.debug(
            f"redis.hgetall completed for {client_id}, got {len(status_data)} items"
        )

        if not status_data:
            logger.debug(f"No status data found in Redis for client {client_id} with key {redis_key}")
            return None

        # Convert bytes to strings for the keys and values
        status = {k.decode("utf-8"): v.decode("utf-8") for k, v in status_data.items()}
        logger.debug(f"Decoded status for {client_id}: {list(status.keys())}") # Log keys for easier debug
        return status
    except Exception as e: # More specific exception handling could be added if needed
        logger.error(f"Error getting client info for {client_id} from Redis: {e}", exc_info=True)
        return None


async def broadcast_client_state_change(
    client_id: str, changed_state_attributes: dict | None = None, originating_client_id: str | None = None
):
    """
    Fetches full client status and broadcasts it, optionally merging specific recent changes.

    Args:
        client_id: The ID of the client whose status changed.
        changed_state_attributes: Optional dictionary of attributes that recently changed.
        originating_client_id: Optional ID of the client that triggered this broadcast (e.g., a frontend).
    """
    if not client_id: # pragma: no cover
        logger.warning("broadcast_client_state_change called with empty client_id")
        return

    try:
        full_status = await get_client_info(client_id)

        if full_status is None: # Client not found in Redis
            if changed_state_attributes: # If we have some attributes, use them
                full_status = changed_state_attributes.copy()
                # Ensure client_id is in the status if it came from changed_state_attributes
                full_status.setdefault(config.PAYLOAD_KEY_CLIENT_ID, client_id)
            else: # No info at all, cannot proceed
                logger.warning(
                    f"No status found for client {client_id} in Redis, and no changed_state_attributes provided for broadcast_client_state_change."
                )
                # Potentially broadcast a 'client_removed' or 'unknown_state' message if needed by frontend logic
                return
        elif changed_state_attributes: # Client found, merge any recent changes
            full_status.update(changed_state_attributes)

        # Always ensure the most up-to-date Redis status indicator is included
        full_status[config.STATUS_KEY_REDIS_STATUS] = status_store.get(
            config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        # Ensure client_id is in the payload being broadcasted
        full_status[config.PAYLOAD_KEY_CLIENT_ID] = client_id


        # Prepare the broadcast message
        broadcast_message = {
            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
            config.PAYLOAD_KEY_CLIENT_ID: client_id, # Redundant here as it's in full_status, but good for clarity
            config.PAYLOAD_KEY_STATUS: full_status,
            config.PAYLOAD_KEY_TIMESTAMP: datetime.now(timezone.utc).isoformat(),
        }
        if originating_client_id:
            broadcast_message["originating_client_id"] = originating_client_id


        await broadcast_to_frontends(broadcast_message)
        logger.info(f"Broadcasted state change for client {client_id}, triggered by {originating_client_id or 'server'}")

    except Exception as e: # pragma: no cover
        logger.error(
            f"Error broadcasting state change for client {client_id}: {e}",
            exc_info=True,
        )

async def update_client_status(
    client_id: str, status_attributes: dict, broadcast: bool = True, originating_client_id: str | None = None
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
        attrs.pop(config.STATUS_KEY_REDIS_STATUS, None)

        success = await redis_update_client_status(client_id, attrs)
        if not success:
            logger.warning(f"Failed to update status for client {client_id} in Redis")
            return False

        # After successful update, trigger a broadcast of the state change if requested
        if broadcast:
            # Include the redis_status in the broadcast and pass originating_client_id
            broadcast_attrs = attrs.copy()
            broadcast_attrs[config.STATUS_KEY_REDIS_STATUS] = status_store.get(
                config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            await broadcast_client_state_change(client_id, broadcast_attrs, originating_client_id=originating_client_id)

        return True
    except Exception as e: # pragma: no cover
        # Log with originating_client_id if available
        log_extra = {"client_id": client_id}
        if originating_client_id:
            log_extra["originating_client_id"] = originating_client_id
        logger.error(
            f"Error updating status for client {client_id} in Redis",
            exc_info=True,
            **log_extra,
        )
        return False


async def disconnect_client(target_client_id: str, originating_client_id: str | None = None) -> dict:
    logger.info(f"Processing disconnect request for client {target_client_id}, initiated by {originating_client_id or 'server'}")
    websocket_to_close = worker_connections.get(target_client_id)
    frontend_websocket_to_close = frontend_connections.get(target_client_id)

    client_already_disconnected = False
    client_info_pre_disconnect = await get_client_info(target_client_id)
    if (
        client_info_pre_disconnect
        and client_info_pre_disconnect.get(config.STATUS_KEY_CONNECTED) == config.STATUS_VALUE_DISCONNECTED
    ):
        client_already_disconnected = True
        logger.info(f"Client {target_client_id} was already marked as disconnected in Redis.")


    closed_worker = False
    if websocket_to_close:
        try:
            await websocket_to_close.close(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE, # Using a more standard code for server-initiated disconnect
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )
            logger.info(
                f"Closed WebSocket connection for worker client {target_client_id} (requested by {originating_client_id or 'server'})"
            )
            closed_worker = True
        except (WebSocketDisconnect, RuntimeError) as e: # pragma: no cover
            logger.warning(
                f"Worker client {target_client_id} already disconnected or error closing WebSocket: {e}"
            )
        finally:
            # Removal from worker_connections will be handled by the main WebSocket handler's finally block
            pass # worker_connections.pop(target_client_id, None)

    closed_frontend = False
    if frontend_websocket_to_close:
        try:
            await frontend_websocket_to_close.close(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )
            logger.info(
                f"Closed WebSocket connection for frontend client {target_client_id} (requested by {originating_client_id or 'server'})"
            )
            closed_frontend = True
        except (WebSocketDisconnect, RuntimeError) as e: # pragma: no cover
            logger.warning(
                f"Frontend client {target_client_id} already disconnected or error closing WebSocket: {e}"
            )
        finally:
            # Removal from frontend_connections will be handled by the main WebSocket handler's finally block
            pass # frontend_connections.pop(target_client_id, None)


    status_update = {
        config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_DISCONNECTED,
        config.STATUS_KEY_STATUS_DETAIL: config.STATUS_DETAIL_DISCONNECTED_BY_SERVER_HTTP,
        config.STATUS_KEY_DISCONNECT_TIME: datetime.now(timezone.utc).isoformat(),
        config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_OFFLINE,
        # config.STATUS_KEY_REDIS_STATUS is not stored in Redis, but added for broadcast
    }

    # Update status in Redis and broadcast the change
    # Pass originating_client_id to update_client_status so it can be used in broadcast_client_state_change
    success = await update_client_status(target_client_id, status_update.copy(), broadcast=True, originating_client_id=originating_client_id)

    current_redis_status = status_store.get(config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNKNOWN)

    if not (closed_worker or closed_frontend) and not client_already_disconnected:
        # Client wasn't actively connected via WebSocket now, but wasn't marked disconnected in Redis.
        # Status has been updated in Redis.
        message = f"Client {target_client_id} was not actively connected. Status updated to disconnected and broadcasted."
        status_key = config.PAYLOAD_KEY_INFO # Using INFO as it's more of a notice
        logger.info(message)
    elif client_already_disconnected and not (closed_worker or closed_frontend):
        message = f"Client {target_client_id} was already disconnected. Status re-updated and broadcasted."
        status_key = config.PAYLOAD_KEY_INFO
        logger.info(message)
    else: # Successfully closed WebSocket or it was already closed and status updated
        message = f"Disconnection process for client {target_client_id} initiated. WebSocket closed if active, status updated and broadcasted."
        status_key = "success" # This remains a string literal as per original, not a constant
        logger.info(message)

    return {
        config.PAYLOAD_KEY_STATUS: status_key,
        config.PAYLOAD_KEY_MESSAGE: message,
        config.PAYLOAD_KEY_CLIENT_ID: target_client_id,
        config.STATUS_KEY_REDIS_STATUS: current_redis_status,
    }

async def pause_client(target_client_id: str, originating_client_id: str | None = None) -> dict:
    logger.info(f"Processing pause request for client {target_client_id}, initiated by {originating_client_id or 'server'}")
    websocket = worker_connections.get(target_client_id)

    client_info = await get_client_info(target_client_id)
    # Check if client exists and is not already disconnected
    target_client_exists_and_connected = client_info is not None and \
                                       client_info.get(config.STATUS_KEY_CONNECTED) == config.STATUS_VALUE_CONNECTED

    if not target_client_exists_and_connected:
        msg = f"Client {target_client_id} not found or already disconnected. Cannot pause."
        logger.error(msg)
        return {
            config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
            config.PAYLOAD_KEY_MESSAGE: msg,
        }

    if client_info.get(config.STATUS_KEY_CLIENT_STATE) == config.CLIENT_STATE_PAUSED:
        msg = f"Client {target_client_id} is already paused. No action taken."
        logger.info(msg)
        return {
            config.PAYLOAD_KEY_STATUS: config.PAYLOAD_KEY_INFO, # Using INFO as it's a notice
            config.PAYLOAD_KEY_MESSAGE: msg,
        }

    sent_command = False
    if not websocket:
        logger.warning(
            f"Client {target_client_id} not actively connected via WebSocket for pause. Updating state in Redis only."
        )
    else:
        try:
            await websocket.send_text(json.dumps({config.PAYLOAD_KEY_COMMAND: config.CONTROL_ACTION_PAUSE}))
            logger.info(f"Pause command sent to client {target_client_id} via WebSocket")
            sent_command = True
        except (WebSocketDisconnect, RuntimeError) as e: # pragma: no cover
            logger.warning(
                f"Client {target_client_id} disconnected or error sending pause command: {e}. Will proceed to update state in Redis."
            )
            # worker_connections.pop(target_client_id, None) # Let main handler deal with it
        except Exception as e: # pragma: no cover
            logger.error(
                f"Error sending pause command to client {target_client_id}: {e}", exc_info=True
            )
            # worker_connections.pop(target_client_id, None) # Let main handler deal with it
            # Do not re-raise HTTPException here, let the status update proceed if possible
            # and report error in the JSON response.
            return {
                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                config.PAYLOAD_KEY_MESSAGE: f"Error sending pause command to client {target_client_id}: {e}",
            }


    status_update = {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_PAUSED}
    # Pass originating_client_id for broadcast attribution
    await update_client_status(target_client_id, status_update.copy(), broadcast=True, originating_client_id=originating_client_id)

    return {
        config.PAYLOAD_KEY_STATUS: "success", # This remains a string literal
        config.PAYLOAD_KEY_MESSAGE: f"Pause command processed for client {target_client_id}. State updated and broadcasted. WebSocket command {'sent' if sent_command else 'not sent (client not connected)'}.",
    }


async def resume_client(target_client_id: str, originating_client_id: str | None = None) -> dict:
    logger.info(f"Processing resume request for client {target_client_id}, initiated by {originating_client_id or 'server'}")
    websocket = worker_connections.get(target_client_id)

    client_info = await get_client_info(target_client_id)
    # Check if client exists and is not already disconnected
    target_client_exists_and_connected = client_info is not None and \
                                       client_info.get(config.STATUS_KEY_CONNECTED) == config.STATUS_VALUE_CONNECTED

    if not target_client_exists_and_connected:
        msg = f"Client {target_client_id} not found or already disconnected. Cannot resume."
        logger.error(msg)
        return {
            config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
            config.PAYLOAD_KEY_MESSAGE: msg,
        }

    if client_info.get(config.STATUS_KEY_CLIENT_STATE) == config.CLIENT_STATE_RUNNING:
        msg = f"Client {target_client_id} is already running. No action taken."
        logger.info(msg)
        return {
            config.PAYLOAD_KEY_STATUS: config.PAYLOAD_KEY_INFO, # Using INFO as it's a notice
            config.PAYLOAD_KEY_MESSAGE: msg,
        }
    sent_command = False
    if not websocket:
        logger.warning(
            f"Client {target_client_id} not actively connected via WebSocket for resume. Updating state in Redis only."
        )
    else:
        try:
            await websocket.send_text(json.dumps({config.PAYLOAD_KEY_COMMAND: config.CONTROL_ACTION_RESUME}))
            logger.info(f"Resume command sent to client {target_client_id} via WebSocket")
            sent_command = True
        except (WebSocketDisconnect, RuntimeError) as e: # pragma: no cover
            logger.warning(
                f"Client {target_client_id} disconnected or error sending resume command: {e}. Will proceed to update state in Redis."
            )
            # worker_connections.pop(target_client_id, None) # Let main handler deal with it
        except Exception as e: # pragma: no cover
            logger.error(
                f"Error sending resume command to client {target_client_id}: {e}", exc_info=True
            )
            # worker_connections.pop(target_client_id, None) # Let main handler deal with it
            return {
                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                config.PAYLOAD_KEY_MESSAGE: f"Error sending resume command to client {target_client_id}: {e}",
            }

    status_update = {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_RUNNING}
    # Pass originating_client_id for broadcast attribution
    await update_client_status(target_client_id, status_update.copy(), broadcast=True, originating_client_id=originating_client_id)

    return {
        config.PAYLOAD_KEY_STATUS: "success", # This remains a string literal
        config.PAYLOAD_KEY_MESSAGE: f"Resume command processed for client {target_client_id}. State updated and broadcasted. WebSocket command {'sent' if sent_command else 'not sent (client not connected)'}.",
    }


# Mount static files for the frontend if the directory exists
if os.path.exists(config.FRONTEND_BUILD_DIR) and os.path.isdir(
    config.FRONTEND_BUILD_DIR
):
    app.mount(
        "/",
        StaticFiles(directory=config.FRONTEND_BUILD_DIR, html=True),
        name=config.STATIC_FILES_NAME,
    )
    logger.info(f"Serving frontend from: {config.FRONTEND_BUILD_DIR}")
else:
    logger.warning(
        f"Frontend build directory {config.FRONTEND_BUILD_DIR} not found or not a directory. "
        "Static file serving will be disabled."
    )


async def handle_disconnect(client_id: str, websocket: WebSocket, reason: str):
    """Handles client disconnection, updates status, and cleans up connections."""

    # Check if already removed (e.g. by another handler of the disconnect)
    # This check helps make the function more idempotent if called multiple times
    # for the same disconnect event.
    processed_disconnect = False
    client_role_for_log = config.STATUS_VALUE_REDIS_UNKNOWN # Default for logging
    is_worker = False
    is_frontend = False

    if client_id in worker_connections:
        # Try to get role from connection if available, though it might not be stored there.
        # Fallback to CLIENT_ROLE_WORKER.
        # client_role_for_log = worker_connections[client_id].get("client_role", config.CLIENT_ROLE_WORKER) # Assuming client_role might be stored on websocket object
        client_role_for_log = config.CLIENT_ROLE_WORKER # More reliable
        del worker_connections[client_id]
        is_worker = True
        processed_disconnect = True
        logger.info(f"Removed worker client {client_id} from active connections.")
    elif client_id in frontend_connections:
        # client_role_for_log = frontend_connections[client_id].get("client_role", config.CLIENT_ROLE_FRONTEND)
        client_role_for_log = config.CLIENT_ROLE_FRONTEND # More reliable
        del frontend_connections[client_id]
        is_frontend = True
        processed_disconnect = True
        logger.info(f"Removed frontend client {client_id} from active connections.")


    if not processed_disconnect:
        # This means the client_id was not found in either active dictionary.
        # It might have been cleaned up already, or registration never fully completed.
        # Check Redis to see if it was known there.
        logger.info(
            f"Client {client_id} disconnect event: Client not found in active "
            f"worker or frontend connections. Might have been handled already or registration was incomplete."
        )
        # Attempt to fetch current info to see if it needs a final "disconnected" update.
        # This is a best-effort, as we don't know its original role if it wasn't in the dicts.
        client_info_if_exists = await get_client_info(client_id)
        if client_info_if_exists and client_info_if_exists.get(config.STATUS_KEY_CONNECTED) == config.STATUS_VALUE_CONNECTED:
            logger.warning(
                f"Client {client_id} was not in active connection dicts but was marked connected in Redis. "
                f"Updating to disconnected."
            )
            # Use the role from Redis if available, otherwise it's a guess for broadcast.
            client_role_for_log = client_info_if_exists.get(config.STATUS_KEY_CLIENT_ROLE, config.CLIENT_ROLE_WORKER) # Default to worker for broadcast decision
        elif client_info_if_exists: # Exists in Redis but already marked disconnected
             client_role_for_log = client_info_if_exists.get(config.STATUS_KEY_CLIENT_ROLE, config.CLIENT_ROLE_WORKER)
             logger.info(f"Client {client_id} already marked disconnected in Redis. Role from Redis: {client_role_for_log}")
             # No further action needed if already marked disconnected.
             # However, if for some reason a broadcast is desired, it could be triggered here.
             # For now, we assume if it's not in active connections and already marked disconnected, we're good.
             return
        else: # Not in active connections and not in Redis (or not found).
            logger.info(f"Client {client_id} not found in Redis. No disconnect status update needed.")
            return # Nothing to update or broadcast.

    # Proceed with status update and broadcast only if we processed a disconnect from active connections
    # OR if we found it in Redis and it was marked as connected.

    logger.info(
        f"Client {client_id} (Role determined as: {client_role_for_log}) disconnected. Reason: {reason}."
    )

    status_update_payload = {
        config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_DISCONNECTED,
        config.STATUS_KEY_STATUS_DETAIL: reason,
        config.STATUS_KEY_DISCONNECT_TIME: datetime.now(timezone.utc).isoformat(),
        config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_OFFLINE, # Set to offline on disconnect
        # client_role should already be in Redis, so we don't explicitly set it here unless it's missing.
    }

    try:
        # Update status in Redis. Broadcast will be handled by update_client_status.
        # The originating_client_id is None here as this is a server-observed disconnect.
        update_success = await update_client_status(client_id, status_update_payload.copy(), broadcast=True, originating_client_id=None)

        if not update_success: # pragma: no cover
            logger.error(f"Failed to update Redis for disconnected client {client_id}. Manual broadcast attempt for worker.")
            # If Redis update fails, and it was a worker, still try to tell frontends it's gone.
            if client_role_for_log == config.CLIENT_ROLE_WORKER:
                minimal_disconnect_status = {
                    **status_update_payload, # Contains necessary fields
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER, # Add role for broadcast
                    config.STATUS_KEY_REDIS_STATUS: config.STATUS_VALUE_REDIS_UNAVAILABLE, # Reflect Redis issue
                }
                await broadcast_to_frontends(
                    {
                        config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
                        config.PAYLOAD_KEY_CLIENT_ID: client_id,
                        config.PAYLOAD_KEY_STATUS: minimal_disconnect_status,
                    }
                )
        else:
            logger.info(f"Successfully updated status to disconnected for {client_id} and broadcasted if applicable.")
            # The broadcast logic is now within update_client_status -> broadcast_client_state_change
            # which checks the client's role from Redis before deciding to broadcast (typically for workers).

    except Exception as e: # pragma: no cover
        logger.error(
            f"Error during final status update or broadcast for client {client_id} disconnect: {e}",
            exc_info=True,
        )
