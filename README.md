# Collegium Serpentis

A real-time client registry and status management system built with FastAPI and WebSockets.

## Features

- Real-time client registration and status updates
- WebSocket-based communication
- In-memory Redis storage for client status
- Asynchronous Python backend

## Prerequisites

- Python 3.13.2
- Poetry (for dependency management)
- Redis server (for production)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/collegium_serpentis.git
   cd collegium_serpentis
   ```

2. Install dependencies:
   ```bash
   poetry install --no-root
   ```

3. (Optional) Install development dependencies:
   ```bash
   poetry install --with dev --no-root
   ```

4. (Optional) Configure Poetry to use a virtual environment in the project directory:
```bash
poetry config virtualenvs.in-project true
```

5. Install pre-commit hooks:
   ```bash
   poetry run pre-commit install
   ```

6. Install redis tools
```bash
sudo apt install redis-tools
```

## Usage

### Starting the Server

```bash
poetry run uvicorn src.server.server:app --reload
```

This will start the server at `http://localhost:8000` with auto-reload enabled for development.

### starting redis server
```bash
docker run --rm --name redis-server -p 6379:6379 redis
```
test the server status
```bash
redis-cli ping
```
expected result
```bash
PONG
```

#### stopping redis container
```bash
docker stop redis-server
```

#### removing redis container
```bash
docker rm redis-server
```

### Using Docker Compose

To start both the FastAPI server and Redis together:

```bash
docker compose up --build
```

- The server will be available at `http://localhost:8000`
- Redis will be available at `localhost:6379`

To stop the services:

```bash
docker compose down
```

### API Documentation

Once the server is running, you can access:

- Interactive API docs (Swagger UI): `http://localhost:8000/docs`
- Alternative API docs (ReDoc): `http://localhost:8000/redoc`

### WebSocket Endpoint

Connect to the WebSocket at:
```
ws://localhost:8000/ws
```

## Testing


```bash
poetry run pytest -s -vvv --cov-report term-missing --cov=src tests/
```
running a single test
```bash
poetry run pytest -s -vvv --cov-report term-missing tests/server/test_server.py::TestWebSocketServer::test_websocket_connection
```

## Development

For development, we use:
- `pytest` for testing
- `black` for code formatting
- `flake8` for linting

## Pre-commit

Pre-commit hooks are configured in the `.pre-commit-config.yaml` file. To install them, run:
To run pre-commit hooks manually, run:

```bash
poetry run pre-commit run --all-files
```

## License

MIT License

## Client Status Dashboard

A web interface is available to view the status of connected clients and the Redis service.

Once the server is running (e.g., via `docker-compose up`), you can access the dashboard by navigating to:

[http://localhost:8000/](http://localhost:8000/)

The dashboard displays:
- The connection status of the Redis server.
- A list of clients, including their ID, connection status (Connected/Disconnected), last seen time (for connected clients), or disconnect time (for disconnected clients).
- Other status details provided by the clients.
- The information on the dashboard automatically refreshes every 5 seconds.