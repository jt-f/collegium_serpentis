import asyncio  # Added for cleanup task tests
import json
from datetime import UTC, datetime, timedelta  # Added for time manipulation
from unittest.mock import (
    AsyncMock,
    call,
)  # Added call for checking multiple calls

import pytest
import redis
from fastapi import WebSocketDisconnect  # Added WebSocket for typing
from fastapi.testclient import TestClient

from src.server import server as server_module
from src.server.server import (
    app as fastapi_app,  # For lifespan testing
)
from src.server.server import (
    client_cache,
    perform_cleanup_cycle,  # Import the testable function instead
    status_store,
)
from tests.server.conftest import (
    AsyncIteratorWrapper,
    ErringAsyncIterator,
)


# Helper to reset global state that might be modified by tests
def reset_global_server_state():
    # server_module.active_connections is now handled by monkeypatch.setattr in
    # manage_server_state. So, no need to clear it here if manage_server_state
    # sets it to {}. If manage_server_state *didn't* setattr, then clear() would
    # be needed. For safety, if active_connections was set by the test, clearing
    # it here is fine.
    if hasattr(server_module, "active_connections") and isinstance(
        server_module.active_connections, dict
    ):
        server_module.active_connections.clear()
    client_cache.clear()
    status_store.clear()
    status_store["redis"] = "unknown"


@pytest.fixture(autouse=True)
def manage_server_state(monkeypatch):
    # Store original contents for non-monkeypatched globals if needed for manual
    # restoration. For active_connections, monkeypatch.setattr handles restoration
    # of the original object.
    original_client_cache_contents = dict(client_cache)
    original_status_store_contents = dict(status_store)

    # Use setattr to ensure active_connections is a new empty dict at the start
    # of the test. The test method itself will then do another setattr for its
    # specific needs.
    monkeypatch.setattr(server_module, "active_connections", {})

    # Reset other globals directly
    client_cache.clear()
    status_store.clear()
    status_store["redis"] = "unknown"

    yield  # Test runs here

    # monkeypatch automatically restores server_module.active_connections to its
    # original state (the state before this fixture's setattr call).

    # Manually restore other globals that were not handled by monkeypatch.setattr
    # in this fixture
    client_cache.clear()
    client_cache.update(original_client_cache_contents)
    status_store.clear()
    status_store.update(original_status_store_contents)


class TestWebSocketServer:
    def test_websocket_connection(self, websocket_client):
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"dummy": "dummy"}
            websocket.send_text(json.dumps(message))
            response = websocket.receive_text()
            assert response is not None

    def test_register_client_with_status(self, websocket_client, mock_redis):
        test_client_id = "test_client_1"
        test_status = {"status": "online", "cpu_usage": "25%"}
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"client_id": test_client_id, "status": test_status}
            websocket.send_text(json.dumps(message))
            response_data = websocket.receive_text()
            response_json = json.loads(response_data)
            assert response_json.get("result") == "message_processed"
            assert response_json.get("client_id") == test_client_id
            assert "status_updated" in response_json
            expected_updated_keys = list(test_status.keys()) + [
                "connected",
                "connect_time",
            ]
            assert all(
                key in response_json["status_updated"] for key in expected_updated_keys
            )

    def test_invalid_json_message(self, websocket_client, caplog):
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text("not a json")
            error_response = websocket.receive_text()
            error_data = json.loads(error_response)
            assert "error" in error_data
            assert error_data["error"] == "Invalid JSON format"
            assert "Invalid JSON received" in caplog.text

    def test_missing_client_id(self, websocket_client, caplog):
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"status": {"key": "value"}}
            websocket.send_text(json.dumps(message))
            error_response = websocket.receive_text()
            error_data = json.loads(error_response)
            assert "error" in error_data
            assert error_data["error"] == "client_id is required"
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()
            assert "Received message without client_id" in caplog.text

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_disconnect(
        self, websocket_client, mock_redis, monkeypatch
    ):
        test_client_id = "test_client_2"
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {"client_id": test_client_id, "status": {"status": "online"}}
                )
            )
            websocket.receive_text()
            assert (
                test_client_id in server_module.active_connections
            )  # Use server_module
        assert (
            test_client_id not in server_module.active_connections
        )  # Use server_module

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_connected(
        self, test_client, mock_redis, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [b"client:test1:status", b"client:test2:status"]
        )
        mock_redis.hgetall.side_effect = [
            {b"status": b"online", b"cpu": b"50%"},
            {b"status": b"offline"},
        ]
        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "connected"
        assert (
            "clients" in data
            and "test1" in data["clients"]
            and "test2" in data["clients"]
        )
        assert data["clients"]["test1"]["status"] == "online"
        mock_redis.scan_iter.assert_called_once_with("client:*:status")
        assert mock_redis.hgetall.call_count == 2

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_unavailable(
        self, test_client, mock_redis, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "unavailable")
        client_cache.clear()
        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "unavailable"
        assert data["clients"] == {}
        mock_redis.scan_iter.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_error(
        self, test_client, mock_redis, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        client_cache.clear()
        mock_redis.scan_iter.return_value = ErringAsyncIterator(
            redis.RedisError("Test Redis error")
        )
        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "unavailable"
        assert "error_redis" in data
        assert "Test Redis error" in data["error_redis"]
        assert data["clients"] == {}

    @pytest.mark.asyncio
    async def test_startup_event_redis_available(self, monkeypatch):
        from src.server.server import lifespan

        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock()
        monkeypatch.setattr(
            "src.server.server.redis_client", mock_redis_client_instance
        )

        tasks_created = []
        original_create_task = asyncio.create_task

        def collect_tasks_side_effect(coro):
            task = original_create_task(coro)
            tasks_created.append(task)
            return task

        monkeypatch.setattr("asyncio.create_task", collect_tasks_side_effect)

        async with lifespan(fastapi_app):  # Pass the app instance
            pass

        assert status_store["redis"] == "connected"
        assert (
            len(tasks_created) == 3
        )  # redis_health_check, redis_reconnector, cleanup_disconnected_clients

        # Cancel tasks to prevent them from running indefinitely
        for task in tasks_created:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_startup_event_redis_unavailable(self, monkeypatch):
        from src.server.server import lifespan

        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )
        monkeypatch.setattr(
            "src.server.server.redis_client", mock_redis_client_instance
        )

        tasks_created = []
        original_create_task = asyncio.create_task

        def collect_tasks_side_effect(coro):
            task = original_create_task(coro)
            tasks_created.append(task)
            return task

        monkeypatch.setattr("asyncio.create_task", collect_tasks_side_effect)

        async with lifespan(fastapi_app):  # Pass the app instance
            pass

        assert status_store["redis"] == "unavailable"
        assert len(tasks_created) == 3

        for task in tasks_created:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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

        # Clear and populate the actual active_connections dict
        server_module.active_connections.clear()
        server_module.active_connections[self.TEST_CLIENT_ID] = mock_ws

        monkeypatch.setitem(status_store, "redis", "connected")

        # Configure Redis mock for initial state check
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"running",
        }
        mock_redis.hset = AsyncMock()

        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")

        assert response.status_code == 200

        # Verify disconnect command is sent BEFORE closing
        mock_ws.send_text.assert_awaited_once_with(
            json.dumps({"command": "disconnect"})
        )
        mock_ws.close.assert_awaited_once_with(
            code=1000, reason="Server initiated disconnect"
        )

        # Ensure send_text was called before close
        call_order = mock_ws.mock_calls
        send_call_index = -1
        close_call_index = -1
        for i, c in enumerate(call_order):
            if c == call.send_text(json.dumps({"command": "disconnect"})):
                send_call_index = i
            elif c == call.close(code=1000, reason="Server initiated disconnect"):
                close_call_index = i

        assert send_call_index != -1, "send_text was not called"
        assert close_call_index != -1, "close was not called"
        assert (
            send_call_index < close_call_index
        ), "send_text was not called before close"

        # The client should be removed from active_connections
        assert self.TEST_CLIENT_ID not in server_module.active_connections
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert "mapping" in kwargs
        status_update = kwargs["mapping"]
        assert status_update.get("connected") == "false"
        assert status_update.get("status_detail") == "Disconnected by server"
        assert "disconnect_time" in status_update

    def test_disconnect_client_not_active_but_in_redis_connected(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"true",  # Redis stores as bytes
            b"client_state": b"running",
        }
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")
        assert response.status_code == 404
        detail_msg = (
            f"Client {self.TEST_CLIENT_ID} not found or not actively connected."
        )
        assert detail_msg in response.json()["detail"]
        mock_redis.hgetall.assert_awaited_with(f"client:{self.TEST_CLIENT_ID}:status")

    def test_disconnect_client_already_disconnected_in_redis(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": b"sometime",
        }
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")
        assert response.status_code == 404
        disconnect_msg = f"Client {self.TEST_CLIENT_ID} already disconnected."
        assert disconnect_msg in response.json()["detail"]
        mock_redis.hgetall.assert_awaited_with(f"client:{self.TEST_CLIENT_ID}:status")

    def test_disconnect_client_not_found_anywhere(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {}
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/disconnect")
        assert response.status_code == 404
        detail_msg = (
            f"Client {self.TEST_CLIENT_ID} not found or not actively connected."
        )
        assert detail_msg in response.json()["detail"]

    def test_pause_client_success(
        self,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        mock_ws.close = AsyncMock()
        # Configure the mock to behave like a truthy WebSocket
        mock_ws.configure_mock(__bool__=lambda self: True)

        # Clear and populate the actual active_connections dict (don't replace it)
        server_module.active_connections.clear()
        server_module.active_connections[self.TEST_CLIENT_ID] = mock_ws

        monkeypatch.setitem(status_store, "redis", "connected")

        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"running",
        }
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
        mock_ws.send_text = AsyncMock(side_effect=RuntimeError("Send failed"))
        mock_ws.close = AsyncMock()

        # Patch the active_connections in the server module
        test_active_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.active_connections", test_active_connections
        )

        monkeypatch.setitem(status_store, "redis", "connected")
        response = test_client.post(f"/clients/{self.TEST_CLIENT_ID}/pause")
        assert response.status_code == 500
        error_msg = (
            f"Failed to send pause command to client {self.TEST_CLIENT_ID} "
            f"due to connection state."
        )
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
        mock_ws.close = AsyncMock()
        # Configure the mock to behave like a truthy WebSocket
        mock_ws.configure_mock(__bool__=lambda self: True)

        # Clear and populate the actual active_connections dict (don't replace it)
        server_module.active_connections.clear()
        server_module.active_connections[self.TEST_CLIENT_ID] = mock_ws

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


@pytest.mark.asyncio
class TestCleanupTask:
    CLIENT_ID_OLD = "client-old-disconnected"
    CLIENT_ID_RECENT = "client-recent-disconnected"
    CLIENT_ID_CONNECTED = "client-still-connected"
    CLIENT_ID_CACHE_OLD = "client-cache-old"

    @pytest.fixture(autouse=True)
    def manage_cleanup_params(self, monkeypatch):
        # We can't patch local variables inside the function, so we'll test the cleanup logic directly
        # The cleanup_disconnected_clients function has hardcoded intervals that we can't easily change
        pass

    async def test_cleanup_deletes_old_disconnected_client_redis_and_cache(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=60)).isoformat()

        # Client in Redis and Cache, old disconnect
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_OLD}:status".encode()]
        )
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": old_disconnect_time.encode(),
        }
        mock_redis.delete = AsyncMock()  # Ensure delete is awaitable

        client_cache[self.CLIENT_ID_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }

        await perform_cleanup_cycle()

        mock_redis.delete.assert_awaited_with(f"client:{self.CLIENT_ID_OLD}:status")
        assert self.CLIENT_ID_OLD not in client_cache

    async def test_cleanup_keeps_recent_disconnected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        recent_disconnect_time = (now - timedelta(seconds=10)).isoformat()

        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_RECENT}:status".encode()]
        )
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": recent_disconnect_time.encode(),
        }
        mock_redis.delete = (
            AsyncMock()
        )  # Ensure delete is awaitable (though not called here)

        client_cache[self.CLIENT_ID_RECENT] = {
            "connected": "false",
            "disconnect_time": recent_disconnect_time,
        }

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()
        assert self.CLIENT_ID_RECENT in client_cache  # Should remain in cache too

    async def test_cleanup_keeps_connected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_CONNECTED}:status".encode()]
        )
        mock_redis.hgetall.return_value = {b"connected": b"true"}
        mock_redis.delete = (
            AsyncMock()
        )  # Ensure delete is awaitable (though not called here)

        client_cache[self.CLIENT_ID_CONNECTED] = {"connected": "true"}

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()
        assert self.CLIENT_ID_CONNECTED in client_cache

    async def test_cleanup_deletes_old_disconnected_client_from_cache_redis_unavailable(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "unavailable")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=60)).isoformat()

        client_cache[self.CLIENT_ID_CACHE_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper([])

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()  # Redis delete should not be called
        assert self.CLIENT_ID_CACHE_OLD not in client_cache

    async def test_cleanup_handles_redis_error_gracefully(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=60)).isoformat()

        mock_redis.scan_iter.side_effect = redis.RedisError("Scan failed")

        # Client in cache that should be cleaned up
        client_cache[self.CLIENT_ID_CACHE_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }

        await perform_cleanup_cycle()

        assert status_store["redis"] == "unavailable"  # Status should be updated
        mock_redis.delete.assert_not_awaited()  # No Redis delete if scan failed
        assert (
            self.CLIENT_ID_CACHE_OLD not in client_cache
        )  # Cache cleanup should still run


# Keep existing HTML and static file tests
def test_serve_status_dashboard_html(test_client):
    response = test_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    content = response.text
    assert "<title>Client Status Dashboard</title>" in content
    assert "<h1>Client Status Dashboard</h1>" in content


def test_serve_static_files(test_client):
    response = test_client.get("/static/status_display.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    content = response.text
    assert "<title>Client Status Dashboard</title>" in content
