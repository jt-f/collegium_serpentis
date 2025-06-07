import asyncio
from datetime import UTC, datetime, timedelta

from src.server import config
from src.server.redis_manager import redis_manager

# Import ConnectionManager if needed for closing WebSocket connections
# from src.server.websocket_manager import manager as ws_manager # Circular import risk?
from src.shared.schemas.websocket import ClientStatus
from src.shared.utils.logging import get_logger

logger = get_logger(__name__)

CLEANUP_INTERVAL_SECONDS = 10  # Run cleanup every 10 seconds (was 60)
FRONTEND_HEARTBEAT_TIMEOUT_SECONDS = 90  # Stale if no heartbeat for 90s
PYTHON_CLIENT_DORMANT_MINUTES = 1  # Mark as dormant after 1 minute (was 5)
PYTHON_CLIENT_DISCONNECT_MINUTES = 3  # Forcibly disconnect after 3 minutes (was 60)


async def periodic_client_cleanup_task(connection_manager_instance) -> None:
    """
    Periodically checks for stale or inactive clients and handles them.
    - Frontend clients: Removed if heartbeat times out.
    - Python clients: Marked dormant, then forcibly disconnected if inactive for too long.
    """
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        logger.info("Running periodic client cleanup task...")
        now = datetime.now(UTC)

        try:
            all_clients_statuses_obj = await redis_manager.get_all_client_statuses()
            if not all_clients_statuses_obj or not all_clients_statuses_obj.clients:
                logger.info("No clients in Redis to check for cleanup.")
                continue

            active_ws_connections = (
                connection_manager_instance.active_connections.copy()
            )

            for client_id, client_status in all_clients_statuses_obj.clients.items():
                if not isinstance(client_status, ClientStatus):
                    logger.warning(
                        f"Skipping invalid status object for client {client_id}"
                    )
                    continue

                if client_status.client_role == config.CLIENT_ROLE_FRONTEND:
                    await _handle_frontend_client_cleanup(
                        client_id,
                        client_status,
                        now,
                        connection_manager_instance,
                        active_ws_connections,
                    )
                elif client_status.client_role == config.CLIENT_ROLE_WORKER:
                    await _handle_worker_client_cleanup(
                        client_id,
                        client_status,
                        now,
                        connection_manager_instance,
                        active_ws_connections,
                    )
        except Exception as e:
            logger.error(f"Error during client cleanup task: {e}", exc_info=True)


async def _handle_frontend_client_cleanup(
    client_id: str,
    client_status: ClientStatus,
    now: datetime,
    ws_manager_instance,  # Pass the ConnectionManager instance
    active_ws_connections: dict,
) -> None:
    """Handle cleanup for a single frontend client."""
    if not client_status.last_heartbeat_timestamp:
        logger.warning(
            f"Frontend client {client_id} missing last_heartbeat_timestamp. Skipping for now."
        )
        # Consider removing if it persists or setting a very old default on creation if this is unexpected
        return

    try:
        last_heartbeat = datetime.fromisoformat(client_status.last_heartbeat_timestamp)
    except ValueError:
        logger.error(
            f"Invalid last_heartbeat_timestamp for frontend {client_id}: {client_status.last_heartbeat_timestamp}"
        )
        # Consider removing the client as its state is corrupted
        return

    if now - last_heartbeat > timedelta(seconds=FRONTEND_HEARTBEAT_TIMEOUT_SECONDS):
        logger.info(
            f"Frontend client {client_id} heartbeat timed out (last_heartbeat: {last_heartbeat}). Disconnecting."
        )
        if client_id in active_ws_connections:
            websocket = active_ws_connections.get(client_id)
            if websocket:
                try:
                    await websocket.close(
                        code=config.WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION,
                        reason="Heartbeat timeout",
                    )
                    logger.info(
                        f"Closed WebSocket for stale frontend client {client_id}."
                    )
                except Exception as e_close:
                    logger.error(
                        f"Error closing WebSocket for stale frontend {client_id}: {e_close}"
                    )
            ws_manager_instance.disconnect(
                client_id
            )  # Remove from active connections dict

        # Update status in Redis to disconnected then remove
        # (or just delete directly - depends on if a final "disconnected" broadcast is desired)
        client_status.connected = config.STATUS_VALUE_DISCONNECTED
        client_status.client_state = config.CLIENT_STATE_OFFLINE
        client_status.disconnect_time = now.isoformat()
        client_status.status_detail = "Disconnected due to heartbeat timeout."

        await redis_manager.update_client_status(
            client_status, broadcast=True
        )  # Broadcast the final state
        # A small delay to allow broadcast to potentially go through if needed
        await asyncio.sleep(0.1)
        await redis_manager.delete_client_status(client_id)  # Then remove
        logger.info(f"Removed stale frontend client {client_id} from Redis.")


async def _handle_worker_client_cleanup(
    client_id: str,
    client_status: ClientStatus,
    now: datetime,
    ws_manager_instance,  # Pass the ConnectionManager instance
    active_ws_connections: dict,
) -> None:
    """Handle cleanup for a single worker (Python) client."""
    if not client_status.last_communication_timestamp:
        logger.warning(
            f"Worker client {client_id} missing last_communication_timestamp. Skipping for now."
        )
        return

    try:
        last_comm = datetime.fromisoformat(client_status.last_communication_timestamp)
    except ValueError:
        logger.error(
            f"Invalid last_communication_timestamp for worker {client_id}: {client_status.last_communication_timestamp}"
        )
        return

    needs_redis_update = False
    disconnect_reason = None

    if now - last_comm > timedelta(minutes=PYTHON_CLIENT_DISCONNECT_MINUTES):
        logger.info(
            f"Worker client {client_id} inactive for > {PYTHON_CLIENT_DISCONNECT_MINUTES}m (last_comm: {last_comm}). Forcibly disconnecting."
        )
        client_status.connected = config.STATUS_VALUE_DISCONNECTED
        client_status.client_state = config.CLIENT_STATE_OFFLINE
        client_status.disconnect_time = now.isoformat()
        client_status.status_detail = f"Forcibly disconnected after {PYTHON_CLIENT_DISCONNECT_MINUTES} minutes of inactivity."
        needs_redis_update = True
        disconnect_reason = client_status.status_detail

    elif (
        now - last_comm > timedelta(minutes=PYTHON_CLIENT_DORMANT_MINUTES)
        and client_status.client_state != config.CLIENT_STATE_DORMANT
        and client_status.client_state != config.CLIENT_STATE_OFFLINE
    ):  # Don't mark offline clients as dormant
        logger.info(
            f"Worker client {client_id} inactive for > {PYTHON_CLIENT_DORMANT_MINUTES}m (last_comm: {last_comm}). Marking as dormant."
        )
        client_status.client_state = config.CLIENT_STATE_DORMANT
        client_status.status_detail = (
            f"Client dormant for over {PYTHON_CLIENT_DORMANT_MINUTES} minutes."
        )
        # Keep 'connected' as true, as it's just dormant, not disconnected yet
        needs_redis_update = True

    if needs_redis_update:
        await redis_manager.update_client_status(client_status, broadcast=True)
        logger.info(
            f"Updated status for worker client {client_id} to {client_status.client_state} in Redis."
        )

    if disconnect_reason:
        if client_id in active_ws_connections:
            websocket = active_ws_connections.get(client_id)
            if websocket:
                try:
                    await websocket.close(
                        code=config.WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION,
                        reason=disconnect_reason,
                    )
                    logger.info(
                        f"Closed WebSocket for inactive worker client {client_id}."
                    )
                except Exception as e_close:
                    logger.error(
                        f"Error closing WebSocket for inactive worker {client_id}: {e_close}"
                    )
            ws_manager_instance.disconnect(
                client_id
            )  # Remove from active connections dict

        # A small delay to allow broadcast of updated (disconnected) status to potentially go through
        await asyncio.sleep(0.1)
        await redis_manager.delete_client_status(client_id)
        logger.info(f"Removed inactive worker client {client_id} from Redis.")


# To run this task, you'll need to get the ConnectionManager instance.
# This might involve passing it from server.py or having a global accessor if appropriate.
# Example (conceptual, adapt to your app structure):
#
# In server.py:
# from .background_tasks import periodic_client_cleanup_task
# from .websocket_manager import manager as ws_manager # global instance
#
# @app.on_event("startup")
# async def startup_event():
#     # ... other startup things ...
#     asyncio.create_task(periodic_client_cleanup_task(ws_manager))
