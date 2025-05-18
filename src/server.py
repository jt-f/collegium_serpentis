import json
import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Initialize FastAPI app
app = FastAPI()

# Initialize Redis client
# Assumes Redis is running on localhost:6379
redis_client = redis.asyncio.Redis(host="localhost", port=6379, db=0)

# Dictionary to keep track of active WebSocket connections (optional,
#   but useful for managing connections)
active_connections: dict[str, WebSocket] = {}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for clients to connect and send status updates.
    """
    client_id = None  # Placeholder for client ID

    await websocket.accept()
    print("WebSocket connection accepted.")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            print(f"Received message: {data}")

            try:
                message = json.loads(data)
                client_id = message.get("client_id")
                status_attributes = message.get("status")

                if not client_id:
                    print("Received message without client_id. Closing connection.")
                    await websocket.send_text(
                        json.dumps({"error": "client_id is required"})
                    )
                    break  # Exit the loop to close the connection

                if client_id not in active_connections:
                    # This is the first message from this client, register it
                    active_connections[client_id] = websocket
                    print(f"Client {client_id} registered.")

                if status_attributes:
                    # Update client status in Redis
                    # Use a hash where the key is the client_id and fields are
                    # status attributes
                    redis_key = f"client:{client_id}:status"
                    await redis_client.hset(redis_key, mapping=status_attributes)
                    print(f"Updated status for client {client_id} in Redis.")

                    # Optional: Send acknowledgement back to client
                    # await websocket.send_text(json.dumps({"status": "received",
                    # "client_id": client_id}))
                else:
                    print(
                        """
                        Received message from client {client_id} \
                        without status attributes
                        """
                    )

            except json.JSONDecodeError:
                print(f"Received invalid JSON: {data}")
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
            except Exception as e:
                print(f"Error processing message from {client_id}: {e}")
                # Optionally send error back to client
                # await websocket.send_text(json.dumps({"error": str(e)}))

    except WebSocketDisconnect:
        if client_id and client_id in active_connections:
            del active_connections[client_id]
            await redis_client.hset(f"client:{client_id}:status", "connected", "false")
            print(f"Client {client_id} disconnected.")
        else:
            print("A client disconnected.")
    except Exception as e:
        print(f"An unexpected error occurred with a WebSocket connection: {e}")
        if client_id and client_id in active_connections:
            del active_connections[client_id]
            print(f"Client {client_id} removed due to error.")


# Example endpoint to retrieve all client statuses from Redis
@app.get("/statuses")
async def get_all_statuses():
    """
    Retrieve the latest status for all registered clients from Redis.
    """
    statuses = {}
    # Scan for all keys matching the pattern "client:*:status"
    async for key in redis_client.scan_iter("client:*:status"):
        client_id = key.decode().split(":")[1]  # Extract client ID from key
        status_data = await redis_client.hgetall(key)
        # Decode bytes to strings
        decoded_status_data = {k.decode(): v.decode() for k, v in status_data.items()}
        statuses[client_id] = decoded_status_data
    return statuses


# To run the server:
# uvicorn server:app --reload
