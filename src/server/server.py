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

from src.server import config
from src.server.redis_manager import (
    cleanup_disconnected_clients,
    close_redis,
    get_all_client_statuses,
    get_client_info,
    initialize_redis,
    redis_health_check,
    redis_reconnector,
    status_store,
    update_client_status,
)
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
        initial_data = await websocket.receive_text()
        message = json.loads(initial_data)
        client_id = message.get("client_id")
        status_attributes = message.get("status", {})
        client_role = status_attributes.get("client_role")  # Expect role in status

        if not client_id or not client_role:
            logger.warning(
                "Client registration failed: client_id or client_role missing",
                data=initial_data,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "error": "client_id and client_role in status are required for registration"
                    }
                )
            )
            await websocket.close(
                code=config.WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION
            )  # Policy Violation
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
        success = await update_client_status(client_id, final_status_attributes)
        # Fetch the definitive status from Redis/cache after update for broadcasting
        stored_status_after_conn = await get_client_info(client_id)

        if success and stored_status_after_conn:
            logger.info(
                f"Successfully updated and fetched status for client {client_id} post-registration."
            )
            # Notify all frontends about this new/updated client
            await broadcast_to_frontends(
                {
                    "type": "client_status_update",
                    "client_id": client_id,
                    "status": stored_status_after_conn,  # Send the definitive status
                }
            )

            # If the connecting client is a frontend, send it the full current state
            if client_role == "frontend":
                all_statuses, data_source, _ = await get_all_client_statuses()
                logger.info(
                    f"Sending all_clients_update to new frontend {client_id} from {data_source}"
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "all_clients_update",
                            "data": {
                                "clients": all_statuses,
                                "redis_status": status_store["redis"],
                            },
                        }
                    )
                )
        else:
            logger.error(
                f"Failed to update or retrieve status for {client_id} after registration. Success: {success}"
            )

        # Acknowledge successful registration to the client
        await websocket.send_text(
            json.dumps(
                {
                    "result": "registration_complete",
                    "client_id": client_id,
                    "redis_status": status_store["redis"],
                }
            )
        )

        # Listen for subsequent messages from this client
        while True:
            data = await websocket.receive_text()
            # logger.debug(f"Received message from {client_id}", data_length=len(data)) # Can be noisy
            try:
                message = json.loads(data)
                status_attributes_update = message.get("status", {})

                if not status_attributes_update:
                    logger.debug(
                        "Received message without status attributes from client",
                        client_id=client_id,
                    )
                    await websocket.send_text(
                        json.dumps(
                            {"result": "empty_status_ignored", "client_id": client_id}
                        )
                    )
                    continue

                # Add/update last_seen timestamp for any status update
                status_attributes_update["last_seen"] = datetime.now(UTC).isoformat()

                # Update Redis with new status attributes from the client
                update_success = await update_client_status(
                    client_id, status_attributes_update
                )

                response_to_client = {
                    "result": "message_processed",
                    "client_id": client_id,
                    "status_updated": list(status_attributes_update.keys()),
                    "redis_status": status_store["redis"],
                }

                if not update_success:
                    response_to_client["warning"] = (
                        "Status update failed to store in Redis"
                    )
                    logger.warning(
                        f"Failed to store status update in Redis for {client_id}."
                    )
                else:
                    # If Redis update was successful, broadcast the full updated status to frontends
                    full_client_status_after_update = await get_client_info(client_id)
                    if full_client_status_after_update:
                        # logger.debug(f"Broadcasting update for {client_id} to frontends.") # Can be noisy
                        await broadcast_to_frontends(
                            {
                                "type": "client_status_update",
                                "client_id": client_id,
                                "status": full_client_status_after_update,
                            }
                        )
                    else:
                        logger.warning(
                            f"Could not retrieve full status for {client_id} after update for broadcast."
                        )

                await websocket.send_text(json.dumps(response_to_client))

            except json.JSONDecodeError as e:
                logger.error(
                    "Invalid JSON received",
                    error=str(e),
                    data=data,
                    client_id=client_id,
                )
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
            except WebSocketDisconnect:
                # This specific exception is caught by the outer handler if it occurs during receive_text()
                logger.info(
                    f"Client {client_id} (Role: {client_role}) disconnected during message processing loop."
                )
                raise  # Re-raise to be handled by the main WebSocketDisconnect handler
            except Exception as e:
                logger.error(f"Error processing message from {client_id}", error=str(e))
                try:
                    await websocket.send_text(
                        json.dumps(
                            {"error": "Internal server error processing your message"}
                        )
                    )
                except Exception as send_err:
                    logger.error(
                        f"Failed to send error to client {client_id}",
                        error=str(send_err),
                    )

    except WebSocketDisconnect:
        # This block will be entered when the client disconnects.
        # The main cleanup and status update logic is now in the finally block,
        # calling the unified handle_disconnect function.
        logger.info(
            f"Client disconnected (WebSocketDisconnect caught): {{client_id='{client_id}', role='{client_role}'}}"
        )
        # No direct broadcast or status update here; finally block handles it.

    except (
        json.JSONDecodeError
    ) as e:  # Catch errors during initial registration message parsing
        logger.error("Invalid JSON during registration phase", error=str(e))
        if not websocket.client_state == WebSocketDisconnect:  # type: ignore
            await websocket.send_text(
                json.dumps({"error": "Invalid JSON format for registration"})
            )
            await websocket.close(
                code=config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR
            )  # Protocol error
    except Exception as e:
        logger.error(
            f"Unexpected WebSocket error for client: {{client_id='{client_id}', role='{client_role}'}}",
            error=repr(e),
            exc_info=True,
        )
        if not websocket.client_state == WebSocketDisconnect:  # type: ignore
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


@app.get(config.STATUSES_ENDPOINT_PATH)
async def get_all_statuses() -> dict[str, Any]:
    logger.debug(
        "Fetching all client statuses for HTTP GET /statuses"
    )  # Logging clarity
    statuses, data_source, error_msg = await get_all_client_statuses()

    response = {
        "redis_status": status_store["redis"],
        "clients": statuses,
        "data_source": data_source,
    }
    if error_msg and status_store["redis"] != "connected":
        response["error_redis"] = (
            f"Failed to fetch from Redis: {error_msg}. Serving from cache."
        )
    return response


# Helper for command endpoints to broadcast state changes
async def broadcast_client_state_change(
    client_id: str, changed_state_attributes: dict | None = None
):
    """Fetches full client status and broadcasts it, optionally merging specific recent changes."""
    full_status = await get_client_info(client_id)
    if full_status:
        # If specific attributes that changed are provided (e.g. from the direct status_update dict),
        # merge them into the full_status obtained from Redis. This ensures the broadcast
        # reflects the very latest change, even if get_client_info() was somehow micro-delayed.
        if changed_state_attributes:
            full_status.update(changed_state_attributes)

        await broadcast_to_frontends(
            {
                "type": "client_status_update",
                "client_id": client_id,
                "status": full_status,  # Broadcast the potentially merged, most up-to-date status
            }
        )
        logger.info(f"Broadcasted state change for client {client_id}")
    else:
        logger.warning(
            f"Could not get client info for {client_id} to broadcast state change."
        )


@app.post(config.DISCONNECT_CLIENT_ENDPOINT_PATH)
async def disconnect_client(client_id: str):
    logger.info(f"Received HTTP request to disconnect client {client_id}")
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
                f"Client {client_id} not actively in worker_connections for HTTP disconnect attempt."
            )

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

        if client_id in worker_connections:
            del worker_connections[client_id]
            logger.info(f"Removed client {client_id} from active worker_connections.")

    status_update = {
        "connected": "false",
        "status_detail": "Disconnected by server request (HTTP)",
        "disconnect_time": datetime.now(UTC).isoformat(),
        "client_state": "offline",
    }
    await update_client_status(client_id, status_update)
    await broadcast_client_state_change(client_id, status_update)

    if client_already_disconnected and not websocket_to_close:
        return {
            "message": f"Client {client_id} was already disconnected. Status re-broadcasted."
        }
    return {
        "message": f"Disconnection process for client {client_id} initiated and broadcasted."
    }


@app.post(config.PAUSE_CLIENT_ENDPOINT_PATH, tags=["Client Control"])  # Added tag
async def pause_client(client_id: str):
    logger.info(f"Received HTTP request to pause client {client_id}")
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
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found or already disconnected.",
            )
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
            logger.error(f"Error sending pause command to client {client_id}: {e}")
            # Depending on policy, you might still want to update Redis state to paused, or raise error.
            raise HTTPException(
                status_code=500,
                detail=f"Error sending pause command to client {client_id}.",
            ) from e

    status_update = {"client_state": "paused"}
    if target_client_exists_in_redis:
        await update_client_status(client_id, status_update)
        await broadcast_client_state_change(client_id, status_update)
        return {
            "message": f"Pause command processed for client {client_id}. State updated and broadcasted."
        }
    else:
        # Should have been caught by the HTTPException above, but as a safeguard:
        return {
            "message": f"Pause command for client {client_id} could not be fully processed as client was not found in an active state."
        }


@app.post(config.RESUME_CLIENT_ENDPOINT_PATH, tags=["Client Control"])  # Added tag
async def resume_client(client_id: str):
    logger.info(f"Received HTTP request to resume client {client_id}")
    websocket = worker_connections.get(client_id)
    target_client_exists_in_redis = True

    if not websocket:
        client_info = await get_client_info(client_id)
        if not client_info or client_info.get("connected") == "false":
            target_client_exists_in_redis = False
            logger.error(
                f"Cannot resume client {client_id}: not found or already disconnected."
            )
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found or already disconnected (and not active via WS).",
            )
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
            logger.error(f"Error sending resume command to client {client_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error sending resume command to client {client_id}.",
            ) from e

    status_update = {"client_state": "running"}
    if target_client_exists_in_redis:
        await update_client_status(client_id, status_update)
        await broadcast_client_state_change(client_id, status_update)
        return {
            "message": f"Resume command processed for client {client_id}. State updated and broadcasted."
        }
    else:
        return {
            "message": f"Resume command for client {client_id} could not be fully processed as client was not found in an active state."
        }


@app.get(config.HEALTH_ENDPOINT_PATH, tags=["System"])
async def health_check():
    """Returns a simple health check response."""
    return {"status": "ok", "redis_status": status_store["redis"]}


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
