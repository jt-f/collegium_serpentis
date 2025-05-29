# Copilot Instructions
This is what I'm trying to build:
Its not hard-and-fast, but the initial direction:

Building a system with a central Python server, real-time client registration, and dynamic status attribute updates requires a robust and efficient technology stack. The core challenge lies in maintaining persistent, low-latency communication and a real-time registry of client states.

Communication Protocol: WebSockets

Central Python Server Framework: FastAPI (with Uvicorn)

Real-time Client Registry & Status Storage: Redis

Python Clients: websockets library (for asyncio)


How the System Components Work Together:
Client Connection & Registration:

A Python client uses the websockets library to initiate a connection to the FastAPI server's WebSocket endpoint.

Upon successful connection, the client can send an initial message to "register" itself, including its unique ID and initial status attributes.

The FastAPI server, upon receiving this, stores the client's ID and status in Redis (e.g., HSET "clients:status" client_id_1 '{attribute_1: value, ...}').

Status Attribute Updates:

Whenever a status attribute changes on a client, the client immediately sends a new message over its persistent WebSocket connection to the FastAPI server, containing the updated attributes.

The FastAPI server receives this update and, without delay, updates the corresponding entry in Redis.

Real-time Registry on Server:

The Redis instance serves as the central, real-time registry. Any part of the FastAPI server (or other connected services) that needs the current status of any client can query Redis directly for the latest information.

This architecture ensures that the server always has the most up-to-date view of all connected clients and their statuses with minimal latency.

Client Heartbeats and Reconnection Logic: While WebSockets include heartbeats, implement robust client-side logic to detect disconnections and automatically attempt to reconnect with exponential backoff.

Error Handling and Logging: Comprehensive error handling on both client and server, along with structured logging, is crucial for debugging and monitoring.

# Guidelines

- Be sure to write code that will pass black and ruff tests