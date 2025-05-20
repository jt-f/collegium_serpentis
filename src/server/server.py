import json
import asyncio
import random
from datetime import datetime
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.shared.utils.logging import get_logger
from src.shared.utils.config import REDIS_CONFIG


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

        # Skip check if Redis is already known to be unavailable
        if status_store["redis"] != "connected":
            continue

        try:
            # Simple ping to check if Redis is still available
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
        except (redis.ConnectionError, asyncio.TimeoutError) as e:
            # Redis just went down
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis connection lost",
                error=str(e),
                previous_status=old_status,
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            # Other Redis errors
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis health check failed",
                error=str(e),
                previous_status=old_status,
            )


async def redis_reconnector() -> None:
    """Background task that attempts to reconnect to Redis with exponential backoff."""
    base_delay = 5  # Start with 5 seconds
    max_delay = 300  # Maximum delay of 5 minutes
    current_delay = base_delay

    while True:
        await asyncio.sleep(current_delay)

        if status_store["redis"] == "connected":
            # Reset delay when connected
            current_delay = base_delay
            continue

        try:
            logger.debug("Attempting to reconnect to Redis", delay=current_delay)
            await redis_client.ping()
            status_store["redis"] = "connected"
            logger.info("Reconnected to Redis")

            # Try to sync any cached data to Redis
            await sync_cache_to_redis()

            # Reset delay
            current_delay = base_delay

        except redis.ConnectionError as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis still unavailable",
                error=str(e),
                next_retry_in=current_delay * 2,
            )
            # Exponential backoff with jitter
            current_delay = min(current_delay * 2, max_delay)
            current_delay = current_delay * (0.8 + 0.4 * random.random())  # Add jitter

        except Exception as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis reconnection error",
                error=str(e),
                next_retry_in=current_delay,
            )
            # Also apply backoff for other errors
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
    """
    Update client status in Redis or fallback to in-memory cache.

    Args:
        client_id: The unique identifier for the client
        status_attributes: Status attributes to update

    Returns:
        bool: True if successfully stored, False otherwise
    """
    try:
        if status_store["redis"] == "connected":
            redis_key = f"client:{client_id}:status"
            try:
                await asyncio.wait_for(
                    redis_client.hset(redis_key, mapping=status_attributes),
                    timeout=0.5,  # Short timeout to quickly detect Redis issues
                )
                logger.debug(
                    "Updated client status in Redis",
                    client_id=client_id,
                    attributes=list(status_attributes.keys()),
                )
                return True
            except (redis.ConnectionError, asyncio.TimeoutError) as e:
                # Immediately mark Redis as unavailable if we can't reach it
                status_store["redis"] = "unavailable"
                logger.error(
                    "Redis connection failed during status update",
                    client_id=client_id,
                    error=str(e),
                )
                # Continue to fallback storage

        # Fallback to in-memory cache
        if client_id not in client_cache:
            client_cache[client_id] = {}
        client_cache[client_id].update(status_attributes)
        logger.debug(
            "Stored client status in memory cache (Redis unavailable)",
            client_id=client_id,
            attributes=list(status_attributes.keys()),
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to update client status",
            client_id=client_id,
            error=str(e),
        )
        # Last resort fallback
        try:
            if client_id not in client_cache:
                client_cache[client_id] = {}
            client_cache[client_id].update(status_attributes)
            logger.debug(
                "Stored client status in memory cache after Redis error",
                client_id=client_id,
            )
            return True
        except Exception as cache_err:
            logger.error(
                "Failed to store client status in memory cache",
                client_id=client_id,
                error=str(cache_err),
            )
            return False


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for clients to connect and send status updates."""
    client_id: Optional[str] = None

    try:
        await websocket.accept()
        logger.info("New WebSocket connection established")

        while True:
            # Receive message from client
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

                if status_attributes:
                    # Update client status using the new helper function
                    success = await update_client_status(client_id, status_attributes)

                    if not success:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "warning": "Status update stored temporarily",
                                    "redis_status": status_store["redis"],
                                }
                            )
                        )
                else:
                    logger.debug(
                        "Received message without status attributes",
                        client_id=client_id,
                    )

                # Send a specific response after successful registration/update
                await websocket.send_text(
                    json.dumps(
                        {
                            "result": "registered",
                            "client_id": client_id,
                            "status": status_attributes,
                            "redis_status": status_store["redis"],
                        }
                    )
                )

            except json.JSONDecodeError as e:
                logger.error("Invalid JSON received", error=str(e), data=data)
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
                await websocket.close()
                break
            except Exception as e:
                logger.error(
                    "Error processing message",
                    client_id=client_id,
                    error=str(e),
                )
                await websocket.send_text(
                    json.dumps({"error": "Internal server error"})
                )

    except WebSocketDisconnect:
        if client_id and client_id in active_connections:
            del active_connections[client_id]

            # Update disconnection status
            disconnect_status = {
                "connected": "false",
                "disconnect_time": str(datetime.utcnow()),
            }

            await update_client_status(client_id, disconnect_status)
            logger.info("Client disconnected", client_id=client_id)
        else:
            logger.info("Anonymous client disconnected")
    except Exception as e:
        logger.error(
            "Unexpected WebSocket error",
            client_id=client_id,
            error=str(e),
        )
        if client_id and client_id in active_connections:
            del active_connections[client_id]
            logger.warning("Client removed due to error", client_id=client_id)


@app.get("/statuses")
async def get_all_statuses() -> Dict[str, Any]:
    """
    Retrieve the latest status for all registered clients from Redis or cache.

    Returns:
        Dict containing system status and client statuses
    """
    logger.debug("Fetching all client statuses")
    statuses = {}

    # First attempt: Try Redis if it's available
    if status_store["redis"] == "connected":
        try:
            async for key in redis_client.scan_iter("client:*:status"):
                client_id = key.decode().split(":")[1]
                status_data = await redis_client.hgetall(key)
                statuses[client_id] = {
                    k.decode(): v.decode() for k, v in status_data.items()
                }
            logger.debug(
                "Successfully fetched client statuses from Redis", count=len(statuses)
            )
        except Exception as e:
            logger.error("Failed to fetch client statuses from Redis", error=str(e))
            status_store["redis"] = "unavailable"
            # Continue to fallback

    # Fallback: Use in-memory cache if Redis failed or is unavailable
    if not statuses and client_cache:
        statuses = client_cache.copy()
        logger.debug(
            "Using in-memory cache for client statuses",
            count=len(statuses),
            redis_status=status_store["redis"],
        )

    return {
        "redis_status": status_store["redis"],
        "clients": statuses,
        "data_source": (
            "redis" if status_store["redis"] == "connected" else "memory_cache"
        ),
    }


@app.get("/")
async def get_status_page():
    """Serves the status display HTML page."""
    return FileResponse("src/server/static/status_display.html")
