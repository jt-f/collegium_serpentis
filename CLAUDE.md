# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Python Backend
- **Install dependencies**: `poetry install --no-root` (add `--with dev` for dev dependencies)
- **Run server**: `./run_server.sh` or `poetry run uvicorn src.server.server:app --reload`
- **Run client**: `./run_client.sh` or `poetry run python src/client/client.py`
- **Run tests**: `./run_tests.sh` or `poetry run pytest -s -vvv --cov-report term-missing --cov=src tests/`
- **Run single test**: `poetry run pytest -s -vvv tests/path/to/test.py::TestClass::test_method`
- **Code formatting**: `./run_black.sh` or `poetry run black src/ tests/`
- **Linting**: `./run_ruff.sh` or `poetry run ruff check src/ tests/`
- **Lint with fixes**: `poetry run ruff check --fix src/ tests/`

### Python Backend pre-commit hooks
- **isort**: `./run_isort.sh`
- **black**: `./run_black.sh`
- **ruff**: `./run_ruff.sh`
You will need to run these manually before committing, and might need to go round in circles a few times, especially with black and ruff. If ruff says it can't fix it, you might need to investigate the code and fix it manually.

### REMEMBER before committing:
Always make sure there is nothing unstaged that needs to be added to the commit!! Double-check. 

### Frontend (React)
- **Navigate to frontend**: `cd frontend-command-centre`
- **Run dev server**: `npm run dev` or `./run_frontend.sh`
- **Build**: `npm run build`
- **Lint**: `npm run lint`
- **Format**: `npm run format`

### Docker
- **Full system**: `docker compose up --build`
- **Redis only**: `docker run --rm --name redis-server -p 6379:6379 redis`

## System Architecture

### Core Components
- **FastAPI Server** (`src/server/`): WebSocket server handling client connections and message routing
- **Python Clients** (`src/client/`): AI-powered worker clients that process messages and respond
- **React Frontend** (`frontend-command-centre/`): Command center dashboard for monitoring and chat
- **Redis**: Message routing via streams and client status storage

### Message Routing System
The system uses **Redis Streams** for scalable message routing between frontend clients and Python workers:

- **Global Stream** (`chat_global`): Broadcast messages to all workers
- **Personal Streams** (`chat_stream:{client_id}`): Direct messages to specific clients  
- **Consumer Groups** (`workers`): Load balancing across Python worker clients
- **WebSocket Manager**: Monitors streams and forwards worker responses to frontends

### Data Flow
1. Frontend sends chat message via WebSocket
2. Server routes to appropriate Redis stream (global or personal)
3. Python workers consume messages using consumer groups
4. Workers generate AI responses and publish to streams
5. Server monitors streams and forwards responses to frontends

### Client Types
- **Frontend Clients**: React dashboard users (monitored, heartbeat timeout: 90s)
- **Worker Clients**: Python AI agents (dormant after 1min, disconnected after 3min)

### Key Files
- `src/server/server.py`: Main FastAPI application and WebSocket endpoints
- `src/server/websocket_manager.py`: WebSocket connection management and message routing
- `src/server/redis_manager.py`: Redis operations and stream management
- `src/client/client.py`: Python worker client with AI chat capabilities
- `src/shared/schemas/websocket.py`: Message schemas for WebSocket communication

### Configuration
- Environment variables loaded from `.env` file
- Server config in `src/server/config.py` and `src/shared/config.py`
- Python 3.13.2 required
- Uses Poetry for Python dependency management

### Testing
- pytest with asyncio support
- Full test coverage expected (`--cov=src --cov-report=term-missing`)
- Tests organized by component in `tests/` directory
- Use `pytest-mock` for mocking Redis and WebSocket connections

### Code Quality
- Black for formatting (line length 88)
- Ruff for linting (Python 3.13 target)
- Pre-commit hooks run black and ruff automatically
- isort for import sorting