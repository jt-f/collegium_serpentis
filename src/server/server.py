import json
import asyncio
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
            exc_info=True,
        )
    except redis.RedisError as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
            exc_info=True,
        )
    except Exception as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis (UnknownError)",
            error=str(e),
            exc_info=True,
        )
    # Start background task to attempt Redis reconnection
    asyncio.create_task(redis_reconnector())
    try:
        yield
    finally:
        logger.info("Shutting down server...")
        await redis_client.close()
        logger.info("Redis connection closed")


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


async def redis_reconnector() -> None:
    """Background task that attempts to reconnect to Redis if it's unavailable."""
    while True:
        await asyncio.sleep(30)
        if status_store["redis"] != "connected":
            try:
                await redis_client.ping()
                status_store["redis"] = "connected"
                logger.info("Reconnected to Redis")
            except redis.ConnectionError as e:
                status_store["redis"] = "unavailable"
                logger.error(
                    "Redis still unavailable (ConnectionRefusedError)",
                    error=str(e),
                    exc_info=True,
                )
            except redis.RedisError as e:
                status_store["redis"] = "unavailable"
                logger.debug("Redis still unavailable (RedisError)", error=str(e))
            except Exception as e:
                status_store["redis"] = "unavailable"
                logger.error(
                    "Redis still unavailable (UnknownError)",
                    error=str(e),
                    exc_info=True,
                )


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
                    await websocket.close() # Added
                    break

                if client_id not in active_connections:
                    active_connections[client_id] = websocket
                    logger.info("Client registered", client_id=client_id)

                if status_attributes:
                    # Update client status in Redis
                    redis_key = f"client:{client_id}:status"
                    await redis_client.hset(redis_key, mapping=status_attributes)
                    logger.debug(
                        "Updated client status in Redis",
                        client_id=client_id,
                        attributes=status_attributes.keys(),
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
                    exc_info=True,
                )
                await websocket.send_text(
                    json.dumps({"error": "Internal server error"})
                )

    except WebSocketDisconnect:
        if client_id and client_id in active_connections:
            del active_connections[client_id]
            await redis_client.hset(
                f"client:{client_id}:status",
                mapping={
                    "connected": "false",
                    "disconnect_time": str(datetime.utcnow()),
                },
            )
            logger.info("Client disconnected", client_id=client_id)
        else:
            logger.info("Anonymous client disconnected")
    except Exception as e:
        logger.error(
            "Unexpected WebSocket error",
            client_id=client_id,
            error=str(e),
            exc_info=True,
        )
        if client_id and client_id in active_connections:
            del active_connections[client_id]
            logger.warning("Client removed due to error", client_id=client_id)


@app.get("/statuses")
async def get_all_statuses() -> Dict[str, Any]:
    """
    Retrieve the latest status for all registered clients from Redis.

    Returns:
        Dict containing system status and client statuses
    """
    logger.debug("Fetching all client statuses")
    statuses = {}

    # If Redis is unavailable, return empty client list with status
    if status_store["redis"] != "connected":
        return {"redis_status": status_store["redis"], "clients": statuses}

    try:
        async for key in redis_client.scan_iter("client:*:status"):
            client_id = key.decode().split(":")[1]
            status_data = await redis_client.hgetall(key)
            statuses[client_id] = {
                k.decode(): v.decode() for k, v in status_data.items()
            }
        logger.debug("Successfully fetched client statuses", count=len(statuses))
        return {"redis_status": status_store["redis"], "clients": statuses}
    except Exception as e:
        logger.error("Failed to fetch client statuses", error=str(e), exc_info=True)
        status_store["redis"] = (
            "unavailable"  # Update status if we encounter Redis errors
        )
        return {
            "redis_status": status_store["redis"],
            "clients": statuses,
            "error": str(e),
        }


@app.get("/")
async def get_status_page():
    """Serves the status display HTML page."""
    return FileResponse("src/server/static/status_display.html")
