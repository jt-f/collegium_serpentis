import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from typing import Generator, List, Dict
from src.server.server import create_app


class AsyncIteratorMock:
    """Mock class that supports async iteration."""

    def __init__(self, items):
        self.items = items.copy()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class EnhancedRedisMock(AsyncMock):
    """Enhanced Redis mock that handles async iteration properly."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scan_data: List[bytes] = []
        self._hash_data: Dict[bytes, Dict[bytes, bytes]] = {}

        # Add call counters to track method usage
        self.scan_iter_called = 0
        self.hgetall_called = 0

        # Important: Override the scan_iter method to NOT be a coroutine
        # but instead return our AsyncIteratorMock directly
        self.scan_iter = self._scan_iter

    def setup_scan_data(self, data: List[bytes]):
        """Set up data to be returned by scan_iter."""
        self._scan_data = data.copy()

    def setup_hash_data(self, key: bytes, data: Dict[bytes, bytes]):
        """Set up data to be returned by hgetall for a specific key."""
        self._hash_data[key] = data.copy()

    def _scan_iter(self, match=None):
        """
        Non-async method that returns an async iterator.
        This matches Redis's actual behavior.
        """
        self.scan_iter_called += 1
        if isinstance(match, str):
            match = match.encode()

        filtered = [k for k in self._scan_data if match is None or match in k]
        return AsyncIteratorMock(filtered)


@pytest.fixture
def mock_redis() -> Generator[EnhancedRedisMock, None, None]:
    """Create an enhanced mock Redis client."""
    mock = EnhancedRedisMock()

    # Keep hgetall as an async method
    async def mock_hgetall(key):
        mock.hgetall_called += 1
        return mock._hash_data.get(key, {})

    mock.hgetall.side_effect = mock_hgetall

    yield mock


@pytest.fixture
def test_app(mock_redis):
    """Create a test FastAPI application with a mocked Redis client."""
    app = create_app(redis_client_override=mock_redis)
    return app


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(test_app)


@pytest.fixture
def websocket_client(test_client: TestClient) -> TestClient:
    """Create a WebSocket test client to the test FastAPI application."""
    return test_client
