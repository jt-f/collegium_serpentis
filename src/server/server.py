import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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

# Define frontend directory path
# Assumes 'frontend-command-centre' is at the root of the workspace,
# and 'server.py' is in 'src/server/'
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

# Dictionary to keep track of active WebSocket connections
active_connections: dict[str, WebSocket] = {}


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
        await close_redis()


# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# CORS middleware
# This allows the frontend (e.g., from http://localhost:5173) to make requests
# to this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite default dev port
        "http://127.0.0.1:5173",
        "http://localhost:3000",  # Common React dev port (e.g. Create React App)
        "http://127.0.0.1:3000",
        # Add your production frontend URL here when you deploy
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allows all standard HTTP methods
    allow_headers=["*"],  # Allows all headers
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id: str | None = None
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
async def get_all_statuses() -> dict[str, Any]:
    logger.debug("Fetching all client statuses")
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


@app.post("/clients/{client_id}/disconnect")
async def disconnect_client(client_id: str):
    logger.info(f"Received request to disconnect client {client_id}")
    websocket = active_connections.get(client_id)

    if not websocket:
        client_info = await get_client_info(client_id)
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
        ) from None
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
        raise HTTPException(status_code=500, detail=detail) from e
    except Exception as e:
        logger.error(f"Error sending pause command to client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send pause command to client {client_id}.",
        ) from e


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
        ) from None
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
        raise HTTPException(status_code=500, detail=detail) from e
    except Exception as e:
        logger.error(f"Error sending resume command to client {client_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send resume command to client {client_id}.",
        ) from e


@app.get("/health")
async def health_check():
    """Returns a simple health check response."""
    return {"status": "ok", "redis_status": status_store.get("redis", "unknown")}


# Mount static files for the React frontend
# This should be the LAST mounted path to act as a catch-all for Single Page Applications.
# It will serve 'index.html' for paths that are not API routes,
# allowing React Router to handle client-side navigation.
# It also serves other static assets (JS, CSS, images) from the FRONTEND_BUILD_DIR.
app.mount(
    "/",
    StaticFiles(directory=FRONTEND_BUILD_DIR, html=True),
    name="static-frontend",
)
