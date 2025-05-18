import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from src.server import app as fastapi_app


@pytest.fixture(scope="module")
def test_app():
    """Create a test FastAPI application."""
    return fastapi_app


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI application."""
    return TestClient(test_app)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock Redis client."""
    with patch("src.server.redis_client", new_callable=AsyncMock) as mock:
        mock.hset = AsyncMock()
        mock.scan_iter = AsyncMock()
        mock.hgetall = AsyncMock()
        yield mock


@pytest.fixture
def websocket_client(test_client: TestClient) -> TestClient:
    """Create a WebSocket test client."""
    return test_client
