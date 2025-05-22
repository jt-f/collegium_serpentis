import json
import asyncio
import random
from datetime import datetime, UTC
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.shared.utils.logging import get_logger
from src.shared.utils.config import REDIS_CONFIG


async def cleanup_disconnected_clients():
    cleanup_interval = 60  # Check every 60 seconds
    max_disconnect_duration = 60  # 1 minute in seconds
    logger.info("Starting background task: cleanup_disconnected_clients")
    while True:
        await asyncio.sleep(cleanup_interval)
        logger.debug("Running cleanup for disconnected clients...")
        current_time = datetime.now(UTC)
        clients_to_delete = []

        # Check Redis first if available
        if status_store["redis"] == "connected":
            try:
                async for key_b in redis_client.scan_iter("client:*:status"):
                    key = key_b.decode()
                    client_id = key.split(":")[1]
                    status_data_raw = await redis_client.hgetall(key)
                    
                    # Decode status_data from bytes to str
                    status_data = {k.decode(): v.decode() for k, v in status_data_raw.items()}

                    if status_data.get("connected") == "false":
                        disconnect_time_str = status_data.get("disconnect_time")
                        if disconnect_time_str:
                            try:
                                disconnect_time = datetime.fromisoformat(disconnect_time_str.replace("Z", "+00:00"))
                                if (current_time - disconnect_time).total_seconds() > max_disconnect_duration:
                                    clients_to_delete.append(client_id)
                            except ValueError as e:
                                logger.warning(f"Invalid disconnect_time format for client {client_id}: {disconnect_time_str}, error: {e}")
                
                for client_id in clients_to_delete:
                    logger.info(f"Deleting data for disconnected client {client_id} from Redis.")
                    await redis_client.delete(f"client:{client_id}:status")
                    # Also remove from in-memory cache if it exists there, for consistency
                    if client_id in client_cache:
                        del client_cache[client_id]
                if clients_to_delete:
                    logger.debug(f"Finished Redis cleanup, deleted {len(clients_to_delete)} clients.")

            except redis.RedisError as e:
                logger.error(f"Redis error during client cleanup: {e}")
                status_store["redis"] = "unavailable" # Mark Redis as unavailable
            except Exception as e:
                logger.error(f"Unexpected error during Redis client cleanup: {e}")

        # Fallback or primary: Check in-memory cache
        cached_clients_to_delete = [] 
        for client_id, status_data in list(client_cache.items()):
            if client_id in clients_to_delete and status_store["redis"] == "connected": 
                continue

            if status_data.get("connected") == "false":
                disconnect_time_str = status_data.get("disconnect_time")
                if disconnect_time_str:
                    try:
                        disconnect_time = datetime.fromisoformat(disconnect_time_str.replace("Z", "+00:00"))
                        if (current_time - disconnect_time).total_seconds() > max_disconnect_duration:
                            cached_clients_to_delete.append(client_id)
                    except ValueError as e:
                        logger.warning(f"Invalid disconnect_time format in cache for client {client_id}: {disconnect_time_str}, error: {e}")
            
        for client_id in cached_clients_to_delete:
            if client_id in client_cache: 
                logger.info(f"Deleting data for disconnected client {client_id} from in-memory cache.")
                del client_cache[client_id]
        if cached_clients_to_delete:
            logger.debug(f"Finished cache cleanup, deleted {len(cached_clients_to_delete)} clients from cache.")
        
        logger.debug("Client cleanup cycle finished.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting server...",
        redis_host=REDIS_CONFIG["host"],
        redis_port=REDIS_CONFIG["port"],
    )
    try:
        await redis_client.ping()
        status_store["redis"] = "connected"
        logger.info("Successfully connected to Redis")
    except redis.ConnectionError as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except redis.RedisError as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except Exception as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis (UnknownError)",
            error=str(e),
        )
    # Start background tasks for Redis monitoring and reconnection
    asyncio.create_task(redis_health_check())
    asyncio.create_task(redis_reconnector())
    asyncio.create_task(cleanup_disconnected_clients())
    try:
        yield
    finally:
        logger.info("Shutting down server...")
        try:
            await redis_client.close()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.warning("Error while closing Redis connection", error=str(e))


# Initialize logger
logger = get_logger(__name__)

# Initialize Redis client
redis_client = redis.asyncio.Redis(**REDIS_CONFIG)

# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Mount static files directory
app.mount("/static", StaticFiles(directory="src/server/static"), name="static")

# Dictionary to keep track of active WebSocket connections
active_connections: Dict[str, WebSocket] = {}

# Status store to track service health
status_store: Dict[str, str] = {"redis": "unknown"}

# In-memory cache as fallback when Redis is unavailable
client_cache: Dict[str, Dict[str, Any]] = {}


async def redis_health_check() -> None:
    """
    Background task that frequently checks if Redis is available.
    This ensures the server notices immediately when Redis goes down.
    """
    check_interval = 3  # Check every 3 seconds

    while True:
        await asyncio.sleep(check_interval)

        if status_store["redis"] != "connected":
            continue

        try:
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
        except (redis.ConnectionError, asyncio.TimeoutError) as e:
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis connection lost",
                error=str(e),
                previous_status=old_status,
                timestamp=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis health check failed",
                error=str(e),
                previous_status=old_status,
            )


async def redis_reconnector() -> None:
    """Background task that attempts to reconnect to Redis with exponential backoff."""
    base_delay = 5
    max_delay = 300
    current_delay = base_delay

    while True:
        await asyncio.sleep(current_delay)

        if status_store["redis"] == "connected":
            current_delay = base_delay
            continue

        try:
            logger.debug("Attempting to reconnect to Redis", delay=current_delay)
            await redis_client.ping()
            status_store["redis"] = "connected"
            logger.info("Reconnected to Redis")
            await sync_cache_to_redis()
            current_delay = base_delay
        except redis.ConnectionError as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis still unavailable",
                error=str(e),
                next_retry_in=current_delay * 2,
            )
            current_delay = min(current_delay * 2, max_delay)
            current_delay = current_delay * (0.8 + 0.4 * random.random())
        except Exception as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis still unavailable (UnknownError)",
                error=str(e),
                next_retry_in=current_delay,
            )
            current_delay = min(current_delay * 2, max_delay)


async def sync_cache_to_redis() -> None:
    """Synchronize in-memory cache to Redis when Redis becomes available."""
    if not client_cache or status_store["redis"] != "connected":
        return

    try:
        count = 0
        for client_id, status in client_cache.items():
            redis_key = f"client:{client_id}:status"
            await redis_client.hset(redis_key, mapping=status)
            count += 1
        if count > 0:
            logger.info(f"Synced {count} clients from cache to Redis")
        client_cache.clear()
    except Exception as e:
        logger.error("Failed to sync cache to Redis", error=str(e))


async def update_client_status(
    client_id: str, status_attributes: Dict[str, Any]
) -> bool:
    if not status_attributes:
        return True

    processed_attributes = {str(k): str(v) for k, v in status_attributes.items()}

    try:
        if status_store["redis"] == "connected":
            redis_key = f"client:{client_id}:status"
            try:
                await redis_client.hset(redis_key, mapping=processed_attributes)
                logger.debug(
                    "Updated client status in Redis",
                    client_id=client_id,
                    attributes=list(processed_attributes.keys()),
                )
                if client_id not in client_cache:
                    client_cache[client_id] = {}
                client_cache[client_id].update(processed_attributes)
                return True
            except (redis.ConnectionError, asyncio.TimeoutError) as e:
                status_store["redis"] = "unavailable"
                logger.error(
                    "Redis connection failed during status update",
                    client_id=client_id,
                    error=str(e),
                )

        if client_id not in client_cache:
            client_cache[client_id] = {}
        client_cache[client_id].update(processed_attributes)
        logger.debug(
            "Stored client status in memory cache (Redis unavailable or failed)",
            client_id=client_id,
            attributes=list(processed_attributes.keys()),
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to update client status",
            client_id=client_id,
            error=str(e),
        )
        try:
            if client_id not in client_cache:
                client_cache[client_id] = {}
            client_cache[client_id].update(processed_attributes)
            logger.debug(
                "Stored client status in memory cache after general error",
                client_id=client_id,
            )
            return True
        except Exception as cache_err:
            logger.error(
                "Failed to store client status in memory cache (last resort)",
                client_id=client_id,
                error=str(cache_err),
            )
            return False


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id: Optional[str] = None
    try:
        await websocket.accept()
        logger.info("New WebSocket connection established")
        while True:
            data = await websocket.receive_text()
            logger.debug("Received message", data_length=len(data))
            try:
                message = json.loads(data)
                client_id = message.get("client_id")
                status_attributes = message.get("status", {})

                if not client_id:
                    logger.warning("Received message without client_id")
                    await websocket.send_text(
                        json.dumps({"error": "client_id is required"})
                    )
                    await websocket.close()
                    break

                if client_id not in active_connections:
                    active_connections[client_id] = websocket
                    logger.info("Client registered", client_id=client_id)
                    status_attributes["connected"] = "true"
                    status_attributes["connect_time"] = datetime.now(UTC).isoformat()
                    status_attributes.pop("disconnect_time", None)
                    status_attributes.pop("status_detail", None)

                if status_attributes:
                    success = await update_client_status(client_id, status_attributes)
                    if not success:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "warning": "Status update failed to store",
                                    "redis_status": status_store["redis"],
                                }
                            )
                        )
                else:
                    logger.debug(
                        "Received message without status attributes",
                        client_id=client_id,
                    )

                status_updated = (
                    list(status_attributes.keys()) if status_attributes else []
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "result": "message_processed",
                            "client_id": client_id,
                            "status_updated": status_updated,
                            "redis_status": status_store["redis"],
                        }
                    )
                )
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON received", error=str(e), data=data)
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
            except WebSocketDisconnect:
                logger.info(
                    "Client disconnected during message processing",
                    client_id=client_id if client_id else "Unknown",
                )
                raise
            except Exception as e:
                logger.error(
                    "Error processing message",
                    client_id=client_id,
                    error=str(e),
                )
                try:
                    await websocket.send_text(
                        json.dumps(
                            {"error": "Internal server error processing message"}
                        )
                    )
                except Exception as send_err:
                    logger.error(
                        "Failed to send error to client",
                        client_id=client_id,
                        error=str(send_err),
                    )
    except WebSocketDisconnect:
        if client_id and client_id in active_connections:
            if active_connections.get(client_id) == websocket:
                del active_connections[client_id]
            disconnect_status = {
                "connected": "false",
                "disconnect_time": datetime.now(UTC).isoformat(),
                "status_detail": "Disconnected by client",
            }
            await update_client_status(client_id, disconnect_status)
            logger.info("Client disconnected", client_id=client_id)
        else:
            logger.info("Anonymous or already removed client disconnected")
    except Exception as e:
        logger.error(
            "Unexpected WebSocket error",
            client_id=client_id,
            error=str(e),
        )
        if client_id and client_id in active_connections:
            if active_connections.get(client_id) == websocket:
                del active_connections[client_id]
            logger.warning(
                "Client removed due to unexpected error", client_id=client_id
            )
        if not websocket.client_state == WebSocketDisconnect:
            try:
                await websocket.close()
            except Exception as close_err:
                logger.error(
                    "Error trying to close WebSocket after unexpected error",
                    client_id=client_id,
                    error=str(close_err),
                )


@app.get("/statuses")
async def get_all_statuses() -> Dict[str, Any]:
    logger.debug("Fetching all client statuses")
    statuses = {}
    error_msg = None
    if status_store["redis"] == "connected":
        try:
            async for key_b in redis_client.scan_iter("client:*:status"):
                key = key_b.decode()
                client_id = key.split(":")[1]
                status_data_raw = await redis_client.hgetall(key)
                statuses[client_id] = {
                    k.decode(): v.decode() for k, v in status_data_raw.items()
                }
            logger.debug(
                "Successfully fetched client statuses from Redis", count=len(statuses)
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to fetch client statuses from Redis", error=error_msg)
            status_store["redis"] = "unavailable"

    if not statuses and client_cache:
        logger.debug(
            "Using in-memory cache for client statuses (Redis empty or unavailable)",
            count=len(client_cache),
            redis_status=status_store["redis"],
        )
        statuses = client_cache.copy()
    elif status_store["redis"] != "connected" and client_cache:
        logger.debug(
            "Using in-memory cache for client statuses (Redis unavailable)",
            count=len(client_cache),
            redis_status=status_store["redis"],
        )
        statuses = client_cache.copy()

    data_source = (
        "redis"
        if status_store["redis"] == "connected" and not error_msg
        else "memory_cache"
    )
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


@app.post("/clients/{client_id}/disconnect")
async def disconnect_client(client_id: str):
    logger.info(f"Received request to disconnect client {client_id}")
    websocket = active_connections.get(client_id)

    if not websocket:
        client_info = client_cache.get(client_id)
        if status_store["redis"] == "connected":
            try:
                redis_key = f"client:{client_id}:status"
                client_info_redis_raw = await redis_client.hgetall(redis_key)
                if client_info_redis_raw:
                    client_info = {
                        k.decode(): v.decode() for k, v in client_info_redis_raw.items()
                    }
            except Exception as e:
                logger.warning(
                    f"Could not verify client {client_id} status in Redis "
                    f"for disconnect: {e}"
                )
        if client_info and client_info.get("connected") == "false":
            raise HTTPException(
                status_code=404, detail=f"Client {client_id} already disconnected."
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=(f"Client {client_id} not found or not actively connected."),
            )

    try:
        logger.info(f"Sending disconnect command to client {client_id}")
        await websocket.send_text(json.dumps({"command": "disconnect"}))
        logger.info(f"Disconnect command sent to client {client_id}")
    except (WebSocketDisconnect, RuntimeError) as e:
        logger.warning(
            f"Could not send disconnect command to client {client_id} "
            f"(already disconnected or connection error): {e}"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error sending disconnect command to client {client_id}: {e}"
        )

    try:
        logger.info(f"Closing WebSocket connection for client {client_id}")
        await websocket.close(code=1000, reason="Server initiated disconnect")
        logger.info(f"WebSocket connection for client {client_id} closed by server.")
    except RuntimeError as e:
        logger.warning(
            f"Error closing WebSocket for client {client_id} "
            f"(may already be closing or closed): {e}"
        )
    except Exception as e:
        logger.error(f"Unexpected error closing WebSocket for client {client_id}: {e}")

    if client_id in active_connections:
        del active_connections[client_id]

    status_update = {
        "connected": "false",
        "status_detail": "Disconnected by server",
        "disconnect_time": datetime.now(UTC).isoformat(),
        "client_state": "offline",
    }
    await update_client_status(client_id, status_update)

    return {"message": f"Client {client_id} disconnected successfully by server."}


@app.post("/clients/{client_id}/pause")
async def pause_client(client_id: str):
    logger.info(f"Received request to pause client {client_id}")
    websocket = active_connections.get(client_id)

    if not websocket:
        raise HTTPException(
            status_code=404, detail=f"Client {client_id} not found or not connected."
        )

    try:
        await websocket.send_text(json.dumps({"command": "pause"}))
        logger.info(f"Pause command sent to client {client_id}")
        status_update = {"client_state": "paused"}
        await update_client_status(client_id, status_update)
        return {"message": f"Pause command sent to client {client_id}."}
    except WebSocketDisconnect:
        logger.warning(
            f"Client {client_id} disconnected before pause command could be "
            f"fully processed."
        )
        if client_id in active_connections:
            del active_connections[client_id]
        disconnect_status = {
            "connected": "false",
            "disconnect_time": datetime.now(UTC).isoformat(),
            "status_detail": "Disconnected during pause attempt",
        }
        await update_client_status(client_id, disconnect_status)
        raise HTTPException(
            status_code=410,
            detail=f"Client {client_id} disconnected during pause attempt.",
        )
    except RuntimeError as e:
        logger.error(
            f"Failed to send pause command to client {client_id} "
            f"(connection state issue): {e}"
        )
        if client_id in active_connections:
            del active_connections[client_id]
        disconnect_status = {
            "connected": "false",
            "disconnect_time": datetime.now(UTC).isoformat(),
            "status_detail": "Connection error during pause attempt",
        }
        await update_client_status(client_id, disconnect_status)
        detail = (
            f"Failed to send pause command to client {client_id} "
            f"due to connection state."
        )
        raise HTTPException(status_code=500, detail=detail)
    except Exception as e:
        logger.error(f"Error sending pause command to client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send pause command to client {client_id}.",
        )


@app.post("/clients/{client_id}/resume")
async def resume_client(client_id: str):
    logger.info(f"Received request to resume client {client_id}")
    websocket = active_connections.get(client_id)

    if not websocket:
        raise HTTPException(
            status_code=404, detail=f"Client {client_id} not found or not connected."
        )

    try:
        await websocket.send_text(json.dumps({"command": "resume"}))
        logger.info(f"Resume command sent to client {client_id}")
        status_update = {"client_state": "running"}
        await update_client_status(client_id, status_update)
        return {"message": f"Resume command sent to client {client_id}."}
    except WebSocketDisconnect:
        logger.warning(
            f"Client {client_id} disconnected before resume command could be "
            f"fully processed."
        )
        if client_id in active_connections:
            del active_connections[client_id]
        disconnect_status = {
            "connected": "false",
            "disconnect_time": datetime.now(UTC).isoformat(),
            "status_detail": "Disconnected during resume attempt",
        }
        await update_client_status(client_id, disconnect_status)
        raise HTTPException(
            status_code=410,
            detail=f"Client {client_id} disconnected during resume attempt.",
        )
    except RuntimeError as e:
        logger.error(
            f"Failed to send resume command to client {client_id} "
            f"(connection state issue): {e}"
        )
        if client_id in active_connections:
            del active_connections[client_id]
        disconnect_status = {
            "connected": "false",
            "disconnect_time": datetime.now(UTC).isoformat(),
            "status_detail": "Connection error during resume attempt",
        }
        await update_client_status(client_id, disconnect_status)
        detail = (
            f"Failed to send resume command to client {client_id} "
            f"due to connection state."
        )
        raise HTTPException(status_code=500, detail=detail)
    except Exception as e:
        logger.error(f"Error sending resume command to client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send resume command to client {client_id}.",
        )


@app.get("/")
async def get_status_page():
    """Serves the status display HTML page."""
    return FileResponse("src/server/static/status_display.html")

@app.get("/health")
async def health_check():
    """Returns a simple health check response."""
    return {"status": "ok", "redis_status": status_store.get("redis", "unknown")}
