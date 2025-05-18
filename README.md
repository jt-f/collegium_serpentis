# Collegium Serpentis

A real-time client registry and status management system built with FastAPI and WebSockets.

## Features

- Real-time client registration and status updates
- WebSocket-based communication
- In-memory Redis storage for client status
- Asynchronous Python backend

## Prerequisites

- Python 3.8+
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
   poetry install
   ```

3. (Optional) Install development dependencies:
   ```bash
   poetry install --with dev
   ```

4. (Optional) Configure Poetry to use a virtual environment in the project directory:
```bash
poetry config virtualenvs.in-project true
```

5. Install pre-commit hooks:
   ```bash
   poetry run pre-commit install
   ```

## Usage

### Starting the Server

```bash
poetry run uvicorn src.server:app --reload
```

This will start the server at `http://localhost:8000` with auto-reload enabled for development.

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

poetry run pytest -s -vvv --cov=src

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