"""
Unit tests for the WebSocket server.

This module tests all the WebSocket connection behavior, client status management,
and REST API functionality, with appropriate mocking for Redis.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from src.server.redis_manager import client_cache, status_store


def reset_global_server_state():
    # server_module.active_connections is now handled by monkeypatch.setattr in
    # manage_server_state. So, no need to clear it here if manage_server_state
    # sets it to {}. If manage_server_state *didn't* setattr, then clear() would
    # be needed. For safety, if active_connections was set by the test, clearing
    # it here is fine.
    status_store["redis"] = "unknown"


@pytest.fixture(autouse=True)
def manage_server_state(monkeypatch):
    # Store original contents for non-monkeypatched globals if needed for manual
    # restoration. For active_connections, monkeypatch.setattr handles restoration
    # of the original object.

    # Clear cache at start of each test
    client_cache.clear()

    # Mock the active_connections dictionary to have a clean slate
    mock_active_connections = {}
    monkeypatch.setattr("src.server.server.active_connections", mock_active_connections)

    reset_global_server_state()

    yield

    # Additional cleanup if necessary (though monkeypatch should auto-restore)
    reset_global_server_state()
    client_cache.clear()


class TestWebSocketServer:
    def test_websocket_connection(self, websocket_client):
        with websocket_client.websocket_connect("/ws") as websocket:
            # Send a minimal valid message
            websocket.send_text(json.dumps({"client_id": "test_client"}))
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["result"] == "message_processed"

    def test_register_client_with_status(self, websocket_client, mock_redis):
        with websocket_client.websocket_connect("/ws") as websocket:
            # Send registration message with status
            message = {
                "client_id": "test_client",
                "status": {"state": "active", "cpu_usage": "25%"},
            }
            websocket.send_text(json.dumps(message))
            response = websocket.receive_text()
            data = json.loads(response)

            assert data["result"] == "message_processed"
            assert data["client_id"] == "test_client"
            assert "state" in data["status_updated"]
            assert "cpu_usage" in data["status_updated"]

    def test_invalid_json_message(self, websocket_client, caplog):
        with websocket_client.websocket_connect("/ws") as websocket:
            # Send invalid JSON
            websocket.send_text("invalid json")
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["error"] == "Invalid JSON format"

    def test_missing_client_id(self, websocket_client, caplog):
        with websocket_client.websocket_connect("/ws") as websocket:
            # Send message without client_id
            message = {"status": {"state": "active"}}
            websocket.send_text(json.dumps(message))
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["error"] == "client_id is required"

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_disconnect(
        self, websocket_client, mock_redis, monkeypatch
    ):
        # Manual tracking for this test
        test_active_connections = {}

        # Patch the active_connections in the server module
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")

        # Configure Redis mock for initial state check
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": b"2023-01-01T00:00:00Z",
        }
        mock_redis.hset = AsyncMock()

        with websocket_client.websocket_connect("/ws") as websocket:
            # Register client
            message = {"client_id": "test_cleanup", "status": {"state": "active"}}
            websocket.send_text(json.dumps(message))
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["result"] == "message_processed"

            # Check that client is in active connections
            assert "test_cleanup" in test_active_connections

        # After WebSocket closes, the client should be removed from active connections
        # and a disconnect status should be set
        assert "test_cleanup" not in test_active_connections

        # Check Redis hset was called with disconnect status
        assert (
            mock_redis.hset.await_count == 2
        )  # Once for registration, once for disconnect
        # Get the last call which should be for disconnect
        last_call_args, last_call_kwargs = mock_redis.hset.call_args
        assert last_call_args[0] == "client:test_cleanup:status"
        assert last_call_kwargs["mapping"]["connected"] == "false"
        assert "disconnect_time" in last_call_kwargs["mapping"]


class TestClientControlAPI:
    TEST_CLIENT_ID = "test-control-client"

    def test_disconnect_client_success(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        mock_ws.close = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")

        # Configure Redis mock for initial state check
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"running",
        }
        mock_redis.hset = AsyncMock()

        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")
        assert response.status_code == 200
        success_msg = (
            f"Client {self.TEST_CLIENT_ID} disconnected successfully by server."
        )
        assert response.json()["message"] == success_msg

        # Check that the WebSocket was called correctly
        mock_ws.send_text.assert_awaited_once_with(
            json.dumps({"command": "disconnect"})
        )
        mock_ws.close.assert_awaited_once_with(
            code=1000, reason="Server initiated disconnect"
        )

        # Check that the client was removed from active connections
        assert self.TEST_CLIENT_ID not in test_active_connections

        # Check that Redis was updated with disconnect status
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["connected"] == "false"
        assert kwargs["mapping"]["status_detail"] == "Disconnected by server"
        assert "disconnect_time" in kwargs["mapping"]

    def test_disconnect_client_not_active_but_in_redis_connected(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"true",  # Redis stores as bytes
        }
        # Mock response for get_client_info
        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = {"connected": "true"}

            response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")

        assert response.status_code == 404
        not_found_msg = (
            f"Client {self.TEST_CLIENT_ID} not found or not actively connected."
        )
        assert not_found_msg in response.json()["detail"]

    def test_disconnect_client_already_disconnected_in_redis(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
        }
        # Mock response for get_client_info
        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = {"connected": "false"}

            response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")

        assert response.status_code == 404
        assert (
            f"Client {self.TEST_CLIENT_ID} already disconnected."
            in response.json()["detail"]
        )

    def test_disconnect_client_not_found_anywhere(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {}
        # Mock response for get_client_info
        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = None

            response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")

        assert response.status_code == 404
        not_found_msg = (
            f"Client {self.TEST_CLIENT_ID} not found or not actively connected."
        )
        assert not_found_msg in response.json()["detail"]

    def test_pause_client_success(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()

        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/pause")
        assert response.status_code == 200
        success_msg = f"Pause command sent to client {self.TEST_CLIENT_ID}."
        assert response.json()["message"] == success_msg
        mock_ws.send_text.assert_awaited_once_with(json.dumps({"command": "pause"}))
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "paused"

    def test_pause_client_not_found(self, test_client: TestClient):
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/pause")
        assert response.status_code == 404
        not_found_msg = f"Client {self.TEST_CLIENT_ID} not found or not connected."
        assert not_found_msg in response.json()["detail"]

    def test_pause_client_websocket_disconnect_on_send(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        mock_ws.close = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/pause")
        assert response.status_code == 410
        disconnect_msg = (
            f"Client {self.TEST_CLIENT_ID} disconnected during pause attempt."
        )
        assert disconnect_msg in response.json()["detail"]
        assert self.TEST_CLIENT_ID not in test_active_connections
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert kwargs["mapping"]["connected"] == "false"
        assert kwargs["mapping"]["status_detail"] == "Disconnected during pause attempt"

    def test_pause_client_runtime_error_on_send(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=RuntimeError("Connection closed"))
        mock_ws.close = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/pause")
        assert response.status_code == 500
        error_msg = f"Failed to send pause command to client {self.TEST_CLIENT_ID} due to connection state."
        assert error_msg in response.json()["detail"]
        assert self.TEST_CLIENT_ID not in test_active_connections
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert kwargs["mapping"]["connected"] == "false"
        assert (
            kwargs["mapping"]["status_detail"]
            == "Connection error during pause attempt"
        )

    def test_resume_client_success(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"paused",
        }
        mock_redis.hset = AsyncMock()

        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/resume")
        assert response.status_code == 200
        success_msg = f"Resume command sent to client {self.TEST_CLIENT_ID}."
        assert response.json()["message"] == success_msg
        mock_ws.send_text.assert_awaited_once_with(json.dumps({"command": "resume"}))
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "running"

    def test_resume_client_not_found(self, test_client: TestClient):
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/resume")
        assert response.status_code == 404
        not_found_msg = f"Client {self.TEST_CLIENT_ID} not found or not connected."
        assert not_found_msg in response.json()["detail"]

    def test_resume_client_websocket_disconnect_on_send(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        mock_ws.close = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/resume")
        assert response.status_code == 410
        disconnect_msg = (
            f"Client {self.TEST_CLIENT_ID} disconnected during resume attempt."
        )
        assert disconnect_msg in response.json()["detail"]
        assert self.TEST_CLIENT_ID not in test_active_connections
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert kwargs["mapping"]["connected"] == "false"
        assert (
            kwargs["mapping"]["status_detail"] == "Disconnected during resume attempt"
        )


def test_root_endpoint_serves_frontend(test_client, monkeypatch):
    """Test that the root endpoint serves the frontend (e.g., index.html)."""
    # To make this test robust, we should ensure StaticFiles has something to serve.
    # We can mock os.path.exists for FRONTEND_BUILD_DIR and the index.html within it.
    with patch("src.server.server.os.path.exists") as mock_path_exists:
        mock_path_exists.return_value = True  # Assume build dir and index.html exist
        response = test_client.get("/")
    assert response.status_code == 200
    # Optionally, check for content type if index.html is served
    assert "text/html" in response.headers.get("content-type", "")


def test_static_files_not_served(test_client):
    """Test that an arbitrary static file path not configured returns 404."""
    # This path is not expected to be served by StaticFiles mounted at "/"
    # unless there's a FRONTEND_BUILD_DIR/static/status_display.html
    response = test_client.get("/static/status_display.html")
    assert response.status_code == 404


def test_get_all_statuses_endpoint(test_client):
    """Test the /statuses endpoint returns proper structure."""
    response = test_client.get("/statuses")
    assert response.status_code == 200
    data = response.json()
    assert "redis_status" in data
    assert "clients" in data
    assert "data_source" in data


def test_health_endpoint(test_client):
    """Test the /health endpoint."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "redis_status" in data


def test_cors_headers_not_set_by_default(test_client):
    """Test that CORS headers are not set by default (handled by frontend proxy)."""
    response = test_client.get("/health")
    assert response.status_code == 200
    # CORS is handled by the frontend development proxy, not the backend
    assert "access-control-allow-origin" not in response.headers


def test_api_endpoints_structure(test_client):
    """Test that our API endpoints are properly structured."""
    # Test health endpoint
    health_response = test_client.get("/health")
    assert health_response.status_code == 200
    health_data = health_response.json()
    assert "status" in health_data
    assert "redis_status" in health_data

    # Test statuses endpoint
    statuses_response = test_client.get("/statuses")
    assert statuses_response.status_code == 200
    statuses_data = statuses_response.json()
    assert "redis_status" in statuses_data
    assert "clients" in statuses_data
    assert "data_source" in statuses_data
