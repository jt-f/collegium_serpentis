from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch  # Added MagicMock

import pytest

from fastapi import (  # isort: skip
    FastAPI,  # isort: skip
    WebSocketDisconnect,  # isort: skip
)
from fastapi.testclient import TestClient  # isort: skip


@pytest.fixture(scope="function")
def test_app():
    """Create a test FastAPI application."""
    # Import app inside the fixture to ensure it's reloaded for each test
    # This helps in cases where config changes might not be picked up otherwise
    from src.server.server import app as fastapi_app_reloaded

    return fastapi_app_reloaded


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(test_app)


@pytest.fixture
def mock_redis() -> Generator[AsyncMock]:
    """Create a mock Redis client."""
    # Patch initialize_redis to prevent actual Redis connection setup during tests
    # and to ensure our mock_rc is used as redis_manager.redis_client.
    with patch(
        "src.server.redis_manager.initialize_redis", AsyncMock(return_value=None)
    ), patch(
        "src.server.redis_manager.redis_client", new_callable=AsyncMock
    ) as mock_rc:
        mock_rc.hset = AsyncMock(return_value=1)
        mock_rc.scan_iter = MagicMock()
        mock_rc.hgetall = AsyncMock(return_value={})
        mock_rc.delete = AsyncMock(return_value=1)
        mock_rc.ping = AsyncMock(return_value=True)
        # Ensure that the redis_client is indeed our mock_rc if initialize_redis was not called
        # or if it was called but did nothing (due to being mocked by mock_init).
        # The primary patch on redis_client should ensure it IS mock_rc.
        yield mock_rc  # Yield the mock_rc that replaced redis_client


@pytest.fixture
def websocket_client(test_client: TestClient) -> TestClient:
    """Create a WebSocket test client to the test FastAPI application."""
    return test_client


@pytest.fixture
def mock_sleep() -> Generator[AsyncMock]:
    """Create a mock for asyncio.sleep."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


# Helper classes start here
class AsyncIteratorWrapper:
    def __init__(self, items: list[Any]):  # Used Any for items
        self.items = items
        self.iter_items = iter(self.items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.iter_items)
        except StopIteration:
            raise StopAsyncIteration from None


class ErringAsyncIterator:
    def __init__(self, error_to_raise: Exception):  # Used Exception for error_to_raise
        self.error_to_raise = error_to_raise
        self.called = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.called:
            self.called = True
            # Ensure the error is an instance if it's a type
            if isinstance(self.error_to_raise, type) and issubclass(
                self.error_to_raise, Exception
            ):
                raise self.error_to_raise()
            raise self.error_to_raise
        raise StopAsyncIteration


@pytest.fixture
def setup_websocket_with_error():
    """Factory fixture to create a mock WebSocket that errors on send_text."""

    def _setup_websocket_with_error(error_type: str, message: str):
        mock_ws = AsyncMock()

        if error_type == "disconnect":
            exc = WebSocketDisconnect(code=1006, reason=message)
            mock_ws.send_text = AsyncMock(side_effect=exc)
        elif error_type == "runtime":
            exc = RuntimeError(message)
            mock_ws.send_text = AsyncMock(side_effect=exc)
        elif error_type == "value":  # For generic unexpected errors
            exc = ValueError(message)
            mock_ws.send_text = AsyncMock(side_effect=exc)
        else:
            raise ValueError(f"Unsupported error_type for mock WebSocket: {error_type}")

        # Mock other necessary attributes if tests require them, e.g., client_state
        mock_ws.client_state = MagicMock()
        # mock_ws.application_state = MagicMock() # If needed
        # mock_ws.client = ('127.0.0.1', 12345) # If needed

        return mock_ws

    return _setup_websocket_with_error
