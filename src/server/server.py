import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.server import config
from src.server.background_tasks import periodic_client_cleanup_task
from src.server.redis_manager import redis_manager
from src.server.websocket_manager import ConnectionManager
from src.shared.schemas.websocket import ClientStatus
from src.shared.utils.logging import get_logger

# Initialize logger
# setup_logging() # Commented out - ensure logger works with basic config or default
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting server...")
    await redis_manager.initialize()

    # Initialize Redis Streams for chat messages
    streams_initialized = await redis_manager.ensure_chat_consumer_groups()
    if streams_initialized:
        logger.info("Successfully initialized Redis Streams for chat messaging")
    else:
        logger.warning(
            "Failed to initialize Redis Streams - chat messaging may not work properly"
        )

    # Register the broadcast callback from websocket_manager with redis_manager
    if hasattr(ws_manager, "broadcast_client_status_update"):
        redis_manager.register_single_status_broadcast_callback(
            ws_manager.broadcast_client_status_update
        )
        logger.info(
            "Successfully registered single client status broadcast callback with RedisManager."
        )
    else:
        logger.error(
            "Failed to register single status broadcast callback: method not found on ConnectionManager."
        )

    # Start Redis stream monitoring for chat responses
    await ws_manager.start_stream_monitoring()

    asyncio.create_task(redis_manager.health_check())
    asyncio.create_task(redis_manager.reconnector())
    asyncio.create_task(redis_manager.cleanup_disconnected_clients())
    asyncio.create_task(periodic_client_cleanup_task(ws_manager))
    try:
        yield
    finally:
        logger.info("Shutting down server...")

        # Stop Redis stream monitoring
        await ws_manager.stop_stream_monitoring()

        connection_stats = await ws_manager.get_connection_stats()
        logger.info(
            f"Closing {connection_stats['worker_connections']} worker and "
            f"{connection_stats['frontend_connections']} frontend connections."
        )

        for client_id in list(ws_manager.active_connections.keys()):
            await ws_manager.close_connection(
                client_id,
                code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                reason=config.REASON_SERVER_SHUTDOWN,
            )

        await redis_manager.close()


app = FastAPI(lifespan=lifespan)

# app.include_router(health_router) # Commented out
# app.include_router(status_router) # Commented out

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
        clients_data = await redis_manager.get_all_client_statuses()
        redis_status_str = redis_manager.get_redis_status()

        # Convert ClientStatus objects to dicts for JSON response
        if clients_data is not None:
            clients_dict = {
                client_id: client_status.model_dump()
                for client_id, client_status in clients_data.clients.items()
            }
        else:
            clients_dict = {}

        response = {
            config.PAYLOAD_KEY_CLIENTS: clients_dict,
            config.STATUS_KEY_REDIS_STATUS: redis_status_str,
        }

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
    connection_stats = await ws_manager.get_connection_stats()
    return {
        config.PAYLOAD_KEY_STATUS: config.HEALTH_STATUS_HEALTHY,
        config.STATUS_KEY_REDIS_STATUS: redis_manager.get_redis_status(),
        "active_workers": connection_stats["worker_connections"],
        "active_frontends": connection_stats["frontend_connections"],
    }


ws_manager = ConnectionManager()


@app.websocket(config.WEBSOCKET_ENDPOINT_PATH)
async def websocket_endpoint(websocket: WebSocket):
    """Simplified websocket endpoint that delegates to the connection manager."""
    await ws_manager.handle_websocket_session(websocket)


async def get_client_info(client_id: str) -> ClientStatus | None:
    """
    Retrieve the current status of a client from Redis.

    Args:
        client_id: The ID of the client to retrieve status for.

    Returns:
        ClientStatus: A ClientStatus object containing the client's status, or None if not found or Redis is unavailable.
    """
    return await redis_manager.get_client_info(client_id)


async def broadcast_client_state_change(
    client_id: str,
    changed_state_attributes: dict | None = None,
    originating_client_id: str | None = None,
):
    """
    Fetches full client status and broadcasts it, optionally merging specific recent changes.

    Args:
        client_id: The ID of the client whose status changed.
        changed_state_attributes: Optional dictionary of attributes that recently changed.
        originating_client_id: Optional ID of the client that triggered this broadcast (e.g., a frontend).
    """
    if not client_id:  # pragma: no cover
        logger.warning("broadcast_client_state_change called with empty client_id")
        return

    try:
        full_status_obj = await get_client_info(client_id)

        if full_status_obj is None:  # Client not found in Redis
            if changed_state_attributes:  # If we have some attributes, use them
                full_status = changed_state_attributes.copy()
                # Ensure client_id is in the status if it came from changed_state_attributes
                full_status.setdefault(config.PAYLOAD_KEY_CLIENT_ID, client_id)
            else:  # No info at all, cannot proceed
                logger.warning(
                    f"No status found for client {client_id} in Redis, and no changed_state_attributes provided for broadcast_client_state_change."
                )
                # Potentially broadcast a 'client_removed' or 'unknown_state' message if needed by frontend logic
                return
        else:  # Client found, convert to dict and merge any recent changes
            full_status = full_status_obj.model_dump()
            if changed_state_attributes:
                full_status.update(changed_state_attributes)

        # Always ensure the most up-to-date Redis status indicator is included
        full_status[config.STATUS_KEY_REDIS_STATUS] = redis_manager.get_redis_status()
        # Ensure client_id is in the payload being broadcasted
        full_status[config.PAYLOAD_KEY_CLIENT_ID] = client_id

        # Prepare the broadcast message
        broadcast_message = {
            config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
            config.PAYLOAD_KEY_CLIENT_ID: client_id,  # Redundant here as it's in full_status, but good for clarity
            config.PAYLOAD_KEY_STATUS: full_status,
            config.PAYLOAD_KEY_TIMESTAMP: datetime.now(UTC).isoformat(),
        }
        if originating_client_id:
            broadcast_message["originating_client_id"] = originating_client_id

        await ws_manager.broadcast_to_frontends(broadcast_message)
        logger.info(
            f"Broadcasted state change for client {client_id}, triggered by {originating_client_id or 'server'}"
        )

    except Exception as e:  # pragma: no cover
        logger.error(
            f"Error broadcasting state change for client {client_id}: {e}",
            exc_info=True,
        )


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
    client_role_for_log = config.STATUS_VALUE_REDIS_UNKNOWN  # Default for logging

    # Check connection manager for the client
    client_exists_in_ws = ws_manager.is_connected(client_id)
    if client_exists_in_ws:
        # Get client role from Redis to determine if it's worker or frontend
        client_info = await redis_manager.get_client_info(client_id)
        if client_info:
            client_role_for_log = client_info.client_role or config.CLIENT_ROLE_WORKER
            logger.info(
                f"Removed {client_role_for_log} client {client_id} from active connections."
            )
        else:
            logger.warning(f"Client {client_id} found in connections but not in Redis.")
            client_role_for_log = config.STATUS_VALUE_REDIS_UNKNOWN
    else:
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
        if (
            client_info_if_exists
            and client_info_if_exists.connected == config.STATUS_VALUE_CONNECTED
        ):
            logger.warning(
                f"Client {client_id} was not in active connection dicts but was marked connected in Redis. "
                f"Updating to disconnected."
            )
            # Use the role from Redis if available, otherwise it's a guess for broadcast.
            client_role_for_log = (
                client_info_if_exists.client_role or config.CLIENT_ROLE_WORKER
            )
        elif client_info_if_exists:  # Exists in Redis but already marked disconnected
            client_role_for_log = (
                client_info_if_exists.client_role or config.CLIENT_ROLE_WORKER
            )
            logger.info(
                f"Client {client_id} already marked disconnected in Redis. Role from Redis: {client_role_for_log}"
            )
            # No further action needed if already marked disconnected.
            # However, if for some reason a broadcast is desired, it could be triggered here.
            # For now, we assume if it's not in active connections and already marked disconnected, we're good.
            return
        else:  # Not in active connections and not in Redis (or not found).
            logger.info(
                f"Client {client_id} not found in Redis. No disconnect status update needed."
            )
            return  # Nothing to update or broadcast.

    # Proceed with status update and broadcast only if we processed a disconnect from active connections
    # OR if we found it in Redis and it was marked as connected.

    logger.info(
        f"Client {client_id} (Role determined as: {client_role_for_log}) disconnected. Reason: {reason}."
    )

    try:
        # Get existing client info to preserve important fields like client_name
        existing_client_info = await redis_manager.get_client_info(client_id)

        # Create ClientStatus object for disconnect update, preserving key fields
        disconnect_data = {
            "client_id": client_id,
            "connected": config.STATUS_VALUE_DISCONNECTED,
            "status_detail": reason,
            "disconnect_time": datetime.now(UTC).isoformat(),
            "client_state": config.CLIENT_STATE_OFFLINE,
            "client_role": client_role_for_log,  # Add the required client_role field
        }

        # Preserve important fields from existing client info
        if existing_client_info:
            if existing_client_info.client_name:
                disconnect_data["client_name"] = existing_client_info.client_name
            if existing_client_info.client_type:
                disconnect_data["client_type"] = existing_client_info.client_type
            if existing_client_info.ip_address:
                disconnect_data["ip_address"] = existing_client_info.ip_address
            if existing_client_info.hostname:
                disconnect_data["hostname"] = existing_client_info.hostname
            # Preserve any custom attributes
            if existing_client_info.attributes:
                disconnect_data["attributes"] = existing_client_info.attributes

        try:
            client_status = ClientStatus.model_validate(disconnect_data)
        except Exception as e:
            logger.error(f"Failed to create ClientStatus for disconnect: {e}")
            return

        # Update status in Redis using redis_manager directly
        update_success = await redis_manager.update_client_status(
            client_status,
            broadcast=True,
            originating_client_id=None,
        )

        if not update_success:  # pragma: no cover
            logger.error(
                f"Failed to update Redis for disconnected client {client_id}. Manual broadcast attempt for worker."
            )
            # If Redis update fails, and it was a worker, still try to tell frontends it's gone.
            if client_role_for_log == config.CLIENT_ROLE_WORKER:
                minimal_disconnect_status = {
                    config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_DISCONNECTED,
                    config.STATUS_KEY_STATUS_DETAIL: reason,
                    config.STATUS_KEY_DISCONNECT_TIME: datetime.now(UTC).isoformat(),
                    config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_OFFLINE,
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,  # Add role for broadcast
                    config.STATUS_KEY_REDIS_STATUS: config.STATUS_VALUE_REDIS_UNAVAILABLE,  # Reflect Redis issue
                }
                # Preserve client_name if available from existing info
                if existing_client_info and existing_client_info.client_name:
                    minimal_disconnect_status["client_name"] = (
                        existing_client_info.client_name
                    )
                await ws_manager.broadcast_to_frontends(
                    {
                        config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CLIENT_STATUS_UPDATE,
                        config.PAYLOAD_KEY_CLIENT_ID: client_id,
                        config.PAYLOAD_KEY_STATUS: minimal_disconnect_status,
                    }
                )
        else:
            logger.info(
                f"Successfully updated status to disconnected for {client_id} and broadcasted if applicable."
            )

    except Exception as e:  # pragma: no cover
        logger.error(
            f"Error during final status update or broadcast for client {client_id} disconnect: {e}",
            exc_info=True,
        )

    # Check if client exists in the connection manager
    client_exists_in_ws = ws_manager.is_connected(client_id)
    if client_exists_in_ws:
        logger.info(f"Client {client_id} found in websocket manager")
    else:
        logger.info(f"Client {client_id} not found in websocket manager")
