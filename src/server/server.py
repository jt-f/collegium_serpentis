import json
import asyncio
from datetime import datetime
from datetime import UTC

from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request

from src.shared.utils.logging import get_logger
from src.shared.utils.config import REDIS_CONFIG


def create_app(redis_client_override=None) -> FastAPI:
    logger = get_logger(__name__)
    redis_client = redis_client_override or redis.asyncio.Redis(**REDIS_CONFIG)
    active_connections: Dict[str, WebSocket] = {}
    status_store: Dict[str, str] = {"redis": "unknown"}

    async def redis_reconnector(app: FastAPI) -> None:
        """Background task that attempts to reconnect to Redis if it's unavailable."""
        while True:
            await asyncio.sleep(30)
            if app.state.status_store["redis"] != "connected":
                try:
                    await app.state.redis_client.ping()
                    app.state.status_store["redis"] = "connected"
                    logger.info("Reconnected to Redis")
                except redis.ConnectionError as e:
                    app.state.status_store["redis"] = "unavailable"
                    logger.error(
                        "Redis still unavailable (ConnectionRefusedError)",
                        error=str(e),
                        exc_info=True,
                    )
                except redis.RedisError as e:
                    app.state.status_store["redis"] = "unavailable"
                    logger.debug("Redis still unavailable (RedisError)", error=str(e))
                except Exception as e:
                    app.state.status_store["redis"] = "unavailable"
                    logger.error(
                        "Redis still unavailable (UnknownError)",
                        error=str(e),
                        exc_info=True,
                    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "Starting server...",
            redis_host=REDIS_CONFIG["host"],
            redis_port=REDIS_CONFIG["port"],
        )
        try:
            await app.state.redis_client.ping()
            app.state.status_store["redis"] = "connected"
            logger.info("Successfully connected to Redis")
        except redis.ConnectionError as e:
            app.state.status_store["redis"] = "unavailable"
            logger.error(
                "Failed to connect to Redis",
                error=str(e),
                exc_info=True,
            )
        except redis.RedisError as e:
            app.state.status_store["redis"] = "unavailable"
            logger.error(
                "Failed to connect to Redis",
                error=str(e),
                exc_info=True,
            )
        except Exception as e:
            app.state.status_store["redis"] = "unavailable"
            logger.error(
                "Failed to connect to Redis (UnknownError)",
                error=str(e),
                exc_info=True,
            )
        # Start background task to attempt Redis reconnection
        asyncio.create_task(redis_reconnector(app))
        try:
            yield
        finally:
            logger.info("Shutting down server...")
            await app.state.redis_client.close()
            logger.info("Redis connection closed")

    app = FastAPI(lifespan=lifespan)
    app.state.redis_client = redis_client
    app.state.active_connections = active_connections
    app.state.status_store = status_store

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        client_id: Optional[str] = None
        try:
            await websocket.accept()
            logger.info("New WebSocket connection established")

            while True:
                data = await websocket.receive_text()
                logger.info("Received message", data_length=len(data))

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

                    if client_id not in app.state.active_connections:
                        # Add a new connection
                        app.state.active_connections[client_id] = websocket
                        logger.info(f"New client registered: {client_id=}")

                    if status_attributes:
                        # Store the status attributes in Redis
                        redis_key = f"client:{client_id}:status"
                        logger.info(
                            f"Updating client status in Redis: \
                                {redis_key=} {status_attributes=}"
                        )
                        await app.state.redis_client.hset(
                            redis_key, mapping=status_attributes
                        )
                        logger.info(
                            f"Updated client status in Redis, {client_id=},\
                                  attributes={list(status_attributes.keys())}"
                        )
                    else:
                        logger.warning(
                            f"Received message without status attributes\
                                  for {client_id=}",
                        )

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
                    await websocket.send_text(
                        json.dumps({"error": "Invalid JSON format"})
                    )
                    await websocket.close()
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
            if client_id and client_id in app.state.active_connections:
                del app.state.active_connections[client_id]
                await app.state.redis_client.hset(
                    f"client:{client_id}:status",
                    mapping={
                        "connected": "false",
                        "disconnect_time": str(datetime.now(UTC)),
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
            if client_id and client_id in app.state.active_connections:
                del app.state.active_connections[client_id]
                logger.warning("Client removed due to error", client_id=client_id)

    @app.get("/statuses")
    async def get_all_statuses(request: Request) -> Dict[str, Any]:
        logger.debug("Fetching all client statuses")
        statuses = {}
        status_store = request.app.state.status_store
        redis_client = request.app.state.redis_client
        logger.info(f"{status_store=}")
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
            status_store["redis"] = "unavailable"
            return {
                "redis_status": status_store["redis"],
                "clients": statuses,
                "error": str(e),
            }

    return app
