import asyncio
import json
import os
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional 

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

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
from src.shared.models.commands import FrontendCommand, CommandName 
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Define frontend directory path
FRONTEND_BUILD_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend-command-centre", "dist"
    )
)
if not os.path.exists(FRONTEND_BUILD_DIR):
    logger.warning(
        f"Frontend build directory not found: {FRONTEND_BUILD_DIR}. "
        "The frontend will not be served until it is built and "
        "placed in the correct location."
    )
elif not os.path.exists(os.path.join(FRONTEND_BUILD_DIR, "index.html")):
    logger.warning(
        f"index.html not found in frontend build directory: {FRONTEND_BUILD_DIR}. "
        "The frontend may not be served correctly."
    )

# Store active connections, distinguishing between roles
worker_connections: Dict[str, WebSocket] = {}
frontend_connections: Dict[str, WebSocket] = {}


async def broadcast_to_frontends(message: dict):
    """Sends a message to all connected frontend clients."""
    disconnected_frontends = []
    for client_id_iter, websocket_iter in list(frontend_connections.items()): 
        try:
            await websocket_iter.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                f"Frontend client {client_id_iter} disconnected or error during broadcast: {e}"
            )
            disconnected_frontends.append(client_id_iter)
        except Exception as e:
            logger.error(f"Unexpected error broadcasting to frontend {client_id_iter}: {e}")
            disconnected_frontends.append(client_id_iter)

    for client_id_to_remove in disconnected_frontends:
        if client_id_to_remove in frontend_connections:
            del frontend_connections[client_id_to_remove]
            logger.info(
                f"Removed disconnected/erroring frontend {client_id_to_remove} from broadcast list."
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
        all_close_tasks: List[Coroutine[Any, Any, None]] = []
        logger.info(
            f"Closing {len(worker_connections)} worker and {len(frontend_connections)} frontend connections."
        )
        for ws in list(worker_connections.values()):
            all_close_tasks.append(ws.close(code=1001, reason="Server shutting down"))
        for ws in list(frontend_connections.values()):
            all_close_tasks.append(ws.close(code=1001, reason="Server shutting down"))

        if all_close_tasks:
            results = await asyncio.gather(*all_close_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id: Optional[str] = None
    client_role: Optional[str] = None
    raw_data_for_logging: Optional[str] = None 

    try:
        await websocket.accept()
        logger.info("New WebSocket connection established, awaiting registration")

        initial_data = await websocket.receive_text()
        raw_data_for_logging = initial_data 
        message_json = json.loads(initial_data) 
        client_id = message_json.get("client_id")
        status_attributes = message_json.get("status", {})
        client_role = status_attributes.get("client_role")

        if not client_id or not client_role:
            logger.warning(
                "Client registration failed: client_id or client_role missing",
                extra={"data": initial_data},
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "error": "client_id and client_role in status are required for registration"
                    }
                )
            )
            await websocket.close(code=1008)
            return

        logger.info(
            f"Client registered: {{client_id='{client_id}', role='{client_role}'}}"
        )

        current_time_iso = datetime.now(UTC).isoformat()
        base_status = {
            "connected": "true",
            "connect_time": current_time_iso,
            "last_seen": current_time_iso,
        }
        final_status_attributes = {
            **base_status,
            **status_attributes,
            "client_role": client_role,
        }
        final_status_attributes.pop("disconnect_time", None)

        if client_role == "frontend":
            if client_id in frontend_connections: # Check for existing connection
                logger.warning(f"Frontend client {client_id} re-registering. Closing old connection.")
                old_ws = frontend_connections[client_id]
                await old_ws.close(code=1001, reason="Client re-registered")
            frontend_connections[client_id] = websocket
        else: # worker
            if client_id in worker_connections:
                logger.warning(f"Worker client {client_id} re-registering. Closing old connection.")
                old_ws = worker_connections[client_id]
                await old_ws.close(code=1001, reason="Client re-registered")
            worker_connections[client_id] = websocket


        success = await update_client_status(client_id, final_status_attributes)
        stored_status_after_conn = await get_client_info(client_id)

        if success and stored_status_after_conn:
            logger.info(
                f"Successfully updated and fetched status for client {client_id} post-registration."
            )
            await broadcast_to_frontends(
                {
                    "type": "client_status_update",
                    "client_id": client_id,
                    "status": stored_status_after_conn,
                }
            )
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

        await websocket.send_text(
            json.dumps(
                {
                    "result": "registration_complete",
                    "client_id": client_id,
                    "redis_status": status_store["redis"],
                }
            )
        )

        while True:
            data = await websocket.receive_text()
            raw_data_for_logging = data 
            message_data = json.loads(data) 

            if client_role == "frontend" and message_data.get("type") == "command":
                target_client_id_for_command: Optional[str] = None 
                message_id_for_command: Optional[str] = None
                try:
                    validated_command = FrontendCommand.model_validate(message_data)
                    command_payload = validated_command.payload
                    target_client_id_for_command = command_payload.client_id
                    command_name_enum = command_payload.command_name
                    command_name_str = command_name_enum.value.lower() 
                    message_id_for_command = command_payload.message_id

                    logger.info(f"Frontend '{client_id}' sent command '{command_name_str}' for client '{target_client_id_for_command}' (MsgID: {message_id_for_command})")

                    worker_websocket = worker_connections.get(target_client_id_for_command)

                    if worker_websocket:
                        await worker_websocket.send_text(json.dumps({"command": command_name_str}))
                        await websocket.send_text(json.dumps({"result": "command_sent", "message_id": message_id_for_command, "detail": f"Command '{command_name_str}' acknowledged for client '{target_client_id_for_command}'"}))

                        status_update = {}
                        if command_name_enum == CommandName.PAUSE:
                            status_update = {"client_state": "paused"}
                        elif command_name_enum == CommandName.RESUME:
                            status_update = {"client_state": "running"}
                        elif command_name_enum == CommandName.DISCONNECT:
                            status_update = {
                                "connected": "false",
                                "status_detail": f"Disconnected by frontend command from {client_id}",
                                "disconnect_time": datetime.now(UTC).isoformat(),
                                "client_state": "offline",
                            }
                        
                        if status_update: 
                            if await update_client_status(target_client_id_for_command, status_update):
                                await broadcast_client_state_change(target_client_id_for_command, status_update)
                    
                    else: 
                        logger.warning(f"Frontend '{client_id}' (MsgID: {message_id_for_command}) tried command '{command_name_str}' for DISCONNECTED client '{target_client_id_for_command}'")
                        await websocket.send_text(json.dumps({"error": "client_not_connected", "message_id": message_id_for_command, "client_id": target_client_id_for_command, "detail": f"Client '{target_client_id_for_command}' not connected."}))
                        if command_name_enum == CommandName.DISCONNECT: 
                            status_update = {
                                "connected": "false",
                                "status_detail": f"Disconnected by frontend command (while offline) from {client_id}",
                                "disconnect_time": datetime.now(UTC).isoformat(),
                                "client_state": "offline",
                            }
                            if await update_client_status(target_client_id_for_command, status_update):
                                 await broadcast_client_state_change(target_client_id_for_command, status_update)

                except ValidationError as ve:
                    msg_id = message_data.get("payload", {}).get("message_id") 
                    logger.warning(f"Command validation failed for frontend {client_id} (MsgID: {msg_id}): {ve.errors()}", exc_info=True)
                    await websocket.send_text(json.dumps({"error": "command_validation_failed", "message_id": msg_id, "detail": ve.errors()}))
                except Exception as e:
                    msg_id = message_data.get("payload", {}).get("message_id", "N/A")
                    target_client_id_ref = target_client_id_for_command if 'target_client_id_for_command' in locals() and target_client_id_for_command is not None else 'unknown'
                    logger.error(f"Error processing command from frontend {client_id} (MsgID: {msg_id}) for client '{target_client_id_ref}': {e}", exc_info=True)
                    await websocket.send_text(json.dumps({"error": "command_processing_error", "message_id": msg_id, "detail": "An internal error occurred while processing the command."}))

            else: 
                status_attributes_update = message_data.get("status", {})
                if not status_attributes_update: # Check if client_id (sender) is not None
                    if client_id:
                        logger.debug(
                            "Received message without status attributes from client",
                            extra={"client_id": client_id},
                        )
                        await websocket.send_text(
                            json.dumps(
                                {"result": "empty_status_ignored", "client_id": client_id}
                            )
                        )
                    else: # Should not happen if registration is enforced
                         logger.warning("Received message without status from unregistered client.")
                    continue

                status_attributes_update["last_seen"] = datetime.now(UTC).isoformat()
                sender_client_id = client_id 
                if not sender_client_id: # Should not happen if registration is enforced
                    logger.error("sender_client_id is None, cannot update status.")
                    continue

                update_success = await update_client_status(
                    sender_client_id, status_attributes_update 
                )
                response_to_client = {
                    "result": "message_processed",
                    "client_id": sender_client_id,
                    "status_updated": list(status_attributes_update.keys()),
                    "redis_status": status_store["redis"],
                }
                if not update_success:
                    response_to_client["warning"] = (
                        "Status update failed to store in Redis"
                    )
                    logger.warning(
                        f"Failed to store status update in Redis for {sender_client_id}."
                    )
                else:
                    full_client_status_after_update = await get_client_info(sender_client_id)
                    if full_client_status_after_update:
                        await broadcast_to_frontends(
                            {
                                "type": "client_status_update",
                                "client_id": sender_client_id,
                                "status": full_client_status_after_update,
                            }
                        )
                    else:
                        logger.warning(
                            f"Could not retrieve full status for {sender_client_id} after update for broadcast."
                        )
                await websocket.send_text(json.dumps(response_to_client))

    except WebSocketDisconnect:
        logger.info(
            f"Client disconnected (WebSocketDisconnect caught): {{client_id='{client_id}', role='{client_role}'}}"
        )
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON during message processing", error=str(e), extra={"client_id": client_id, "raw_data": raw_data_for_logging})
        if websocket.client_state != WebSocketDisconnect: 
            try:
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
                await websocket.close(code=1002)
            except RuntimeError: 
                pass
    except Exception as e:
        logger.error(
            f"Unexpected WebSocket error for client: {{client_id='{client_id}', role='{client_role}'}}",
            error=repr(e),
            exc_info=True,
        )
        if websocket.client_state != WebSocketDisconnect: 
            try:
                await websocket.close(code=1011)
            except RuntimeError: 
                pass
    finally:
        if client_id: 
            await handle_disconnect(client_id, websocket, "Disconnected by client or error")
        else: # Connection closed before client_id was established
            logger.info( "WebSocket connection closed before client_id was established.")
            # Ensure the websocket is closed if it's still in a list (e.g. if it was added then registration failed)
            # This is a safeguard, as it shouldn't be in these lists if client_id is None.
            for conn_dict in [worker_connections, frontend_connections]:
                for cid, ws_in_dict in list(conn_dict.items()): # Iterate over a copy
                    if ws_in_dict == websocket:
                        del conn_dict[cid]
                        logger.info(f"Removed websocket from connection list (no client_id) for {cid}")
                        break
            if websocket.client_state != WebSocketDisconnect: # type: ignore
                 try: await websocket.close(code=1001)
                 except RuntimeError: pass


@app.get("/statuses")
async def get_all_statuses() -> dict[str, Any]:
    logger.debug("Fetching all client statuses for HTTP GET /statuses")
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


async def broadcast_client_state_change(
    client_id: str, changed_state_attributes: Optional[dict] = None
):
    full_status = await get_client_info(client_id)
    if full_status:
        if changed_state_attributes:
            full_status.update(changed_state_attributes)
        await broadcast_to_frontends(
            {
                "type": "client_status_update",
                "client_id": client_id,
                "status": full_status,
            }
        )
        logger.info(f"Broadcasted state change for client {client_id}")
    else:
        logger.warning(
            f"Could not get client info for {client_id} to broadcast state change."
        )

@app.get("/health")
async def health_check():
    return {"status": "ok", "redis_status": status_store.get("redis", "unknown")}


app.mount(
    "/",
    StaticFiles(directory=FRONTEND_BUILD_DIR, html=True),
    name="static-frontend",
)


async def handle_disconnect(client_id: str, websocket: WebSocket, reason: str):
    """Handles client disconnection, updates status, and cleans up connections."""
    
    role_from_dicts: Optional[str] = None
    # Check and remove from worker_connections
    if client_id in worker_connections and worker_connections.get(client_id) == websocket:
        role_from_dicts = "worker"
        del worker_connections[client_id]
        logger.info(f"Removed worker client {client_id} from active connections.")
    # Check and remove from frontend_connections
    elif client_id in frontend_connections and frontend_connections.get(client_id) == websocket:
        role_from_dicts = "frontend"
        del frontend_connections[client_id]
        logger.info(f"Removed frontend client {client_id} from active connections.")
    else:
        # This case means the websocket object passed was not the one stored for this client_id,
        # or the client_id was not in any active list (e.g. already removed).
        logger.info(
            f"Client {client_id} for disconnect: WebSocket object mismatch or not found in active connection dicts. "
            "May have been already handled or connection was superseded."
        )

    client_info_from_redis = await get_client_info(client_id)
    
    authoritative_client_role = "unknown"
    if client_info_from_redis and client_info_from_redis.get("client_role"):
        authoritative_client_role = client_info_from_redis["client_role"]
    elif role_from_dicts:
        authoritative_client_role = role_from_dicts

    logger.info(
        f"Processing disconnect for client {client_id} (Role: {authoritative_client_role}). Reason: {reason}."
    )

    status_update = {
        "connected": "false",
        "status_detail": reason,
        "disconnect_time": datetime.now(UTC).isoformat(),
        "client_role": authoritative_client_role 
    }
    
    # Update Redis only if the client wasn't already marked as disconnected or if the new reason/role is important
    if client_info_from_redis and client_info_from_redis.get("connected") == "false" and client_info_from_redis.get("status_detail") == reason:
        logger.info(f"Client {client_id} already marked as disconnected in Redis with the same reason. No Redis update.")
    else:
        update_success = await update_client_status(client_id, status_update)
        if not update_success:
            logger.error(f"Failed to update Redis for disconnected client {client_id}")
            # If Redis update fails, still attempt broadcast for worker clients if it was a worker
            if authoritative_client_role == "worker":
                await broadcast_to_frontends(
                    {
                        "type": "client_status_update",
                        "client_id": client_id,
                        "status": status_update, 
                    }
                )
            # Do not return here, as we might still need to broadcast the latest state from Redis below
        
    # Always fetch the latest status from Redis for broadcast, to ensure consistency
    final_status_after_disconnect = await get_client_info(client_id)
    if not final_status_after_disconnect:
        logger.warning(
            f"Could not retrieve client info for {client_id} after disconnect. Using local status_update for broadcast if worker."
        )
        # If fetching fails, use the status_update dict we constructed
        final_status_after_disconnect = {**status_update, "client_id": client_id}


    # Broadcast worker disconnects
    if authoritative_client_role == "worker":
        await broadcast_to_frontends(
            {
                "type": "client_status_update",
                "client_id": client_id,
                "status": final_status_after_disconnect, # This will now contain the role and latest info
            }
        )
        logger.info(f"Broadcasted disconnect status for worker client {client_id}.")
    elif authoritative_client_role == "frontend":
        logger.info(
            f"Frontend client {client_id} disconnected. No broadcast sent to other frontends."
        )

    # Ensure the WebSocket is closed from the server side if it's somehow still open.
    # This is a safeguard.
    if websocket and websocket.client_state != WebSocketDisconnect: # type: ignore
        try:
            await websocket.close(code=1000) # Normal closure
        except RuntimeError as e: 
            logger.debug(f"Error closing websocket for {client_id} in handle_disconnect (likely already closed by client): {e}")
        except Exception as e: # Catch any other error during close
            logger.warning(f"Unexpected error closing websocket for {client_id} in handle_disconnect: {e}")

# Removed HTTP command endpoints as their functionality is now via WebSockets.
# Kept /statuses and /health.
# Kept helper functions and other setup.Okay, I have overwritten `src/server/server.py` with the new content.
This version includes:
1.  The necessary imports (`FrontendCommand`, `CommandName`, `ValidationError`, etc.).
2.  The modified `websocket_endpoint` with the `if/else` structure to differentiate between command messages from frontends and status updates from workers.
3.  Integrated Pydantic model validation (`FrontendCommand.model_validate(message_data)`) for incoming commands.
4.  Logic to process `PAUSE`, `RESUME`, and `DISCONNECT` commands:
    *   Sending the command to the target worker via its WebSocket.
    *   Sending an acknowledgment back to the originating frontend.
    *   Updating the worker's status in Redis.
    *   Broadcasting the worker's state change to all connected frontends.
    *   Handling cases where the target worker is not connected.
5.  Error handling for `ValidationError` during command parsing and other general exceptions during command processing, with appropriate logging and error messages sent to the frontend.
6.  The existing logic for worker status updates is preserved within the `else` block.
7.  The `handle_disconnect` function was refined to better determine `authoritative_client_role` and ensure appropriate Redis updates and broadcasts occur, especially if a client was already marked disconnected.
8.  Added logging for when a client re-registers to help debug potential stale connection issues.
9.  Ensured client_id is consistently used (sender_client_id) when processing status updates to avoid ambiguity.
10. Added more robust error handling around `websocket.close()` calls.

The HTTP command endpoints (`/clients/{client_id}/pause`, `/resume`, `/disconnect`) are now effectively superseded by the WebSocket command handling.

I will now submit the subtask report.
