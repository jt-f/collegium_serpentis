import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from src.server.server import app as fastapi_app
from typing import Generator, Any # Added Any
import redis # Added redis


@pytest.fixture(scope="module")
def test_app():
    """Create a test FastAPI application."""
    return fastapi_app


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(test_app)


@pytest.fixture
def mock_redis() -> Generator[AsyncMock, None, None]:
    """Create a mock Redis client."""
    with patch("src.server.server.redis_client", new_callable=AsyncMock) as mock:
        mock.hset = AsyncMock()
        mock.scan_iter = AsyncMock()
        mock.hgetall = AsyncMock()
        yield mock


@pytest.fixture
def websocket_client(test_client: TestClient) -> TestClient:
    """Create a WebSocket test client to the test FastAPI application."""
    return test_client

# Helper classes start here
class AsyncIteratorWrapper:
    def __init__(self, items: list[Any]): # Used Any for items
        self.items = items
        self.iter_items = iter(self.items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.iter_items)
        except StopIteration:
            raise StopAsyncIteration

class ErringAsyncIterator:
    def __init__(self, error_to_raise: Exception): # Used Exception for error_to_raise
        self.error_to_raise = error_to_raise
        self.called = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.called:
            self.called = True
            # Ensure the error is an instance if it's a type
            if isinstance(self.error_to_raise, type) and issubclass(self.error_to_raise, Exception):
                raise self.error_to_raise()
            raise self.error_to_raise
        raise StopAsyncIteration
