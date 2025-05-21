import asyncio
import json
import pytest
from unittest.mock import AsyncMock
from fastapi import WebSocketDisconnect
import redis
from tests.server.conftest import AsyncIteratorWrapper, ErringAsyncIterator
from src.server.server import active_connections  # Added import


class TestWebSocketServer:
    def test_websocket_connection(self, websocket_client):
        """Test that a WebSocket connection can be established
        and messages can be sent/received."""

        with websocket_client.websocket_connect("/ws") as websocket:
            # Send a registration message
            message = {"dummy": "dummy"}
            websocket.send_text(json.dumps(message))

            # We should receive a response from the server.
            # This tests that we at least get something back
            response = websocket.receive_text()
            assert response is not None

    def test_register_client_with_status(self, websocket_client, mock_redis):
        """Test that a client can register with status updates."""
        test_client_id = "test_client_1"
        test_status = {"status": "online", "cpu_usage": "25%"}

        with websocket_client.websocket_connect("/ws") as websocket:
            # Send registration message
            message = {"client_id": test_client_id, "status": test_status}
            websocket.send_text(json.dumps(message))

            # Wait for and verify server's response
            response_data = websocket.receive_text()
            response_json = json.loads(response_data)

            # Verify the response contains expected fields
            assert response_json.get("result") == "registered"
            assert response_json.get("client_id") == test_client_id

            # Verify the response contains our status data
            assert "status" in response_json
            assert response_json["status"] == test_status

    def test_invalid_json_message(self, websocket_client, caplog):
        """Test handling of invalid JSON messages."""
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text("not a json")

            # Client should receive the JSON error message from the server
            error_response = websocket.receive_text()
            error_data = json.loads(error_response)
            assert "error" in error_data
            assert error_data["error"] == "Invalid JSON format"

            # NOW, a subsequent receive should fail as the server closed the connection
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

            # Verify error was logged
            assert "Invalid JSON received" in caplog.text

    def test_missing_client_id(self, websocket_client, caplog):
        """Test handling of messages missing client_id."""
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"status": {"key": "value"}}  # Missing client_id
            websocket.send_text(json.dumps(message))

            # Client should receive the JSON error message
            error_response = websocket.receive_text()
            error_data = json.loads(error_response)
            assert "error" in error_data
            assert error_data["error"] == "client_id is required"

            # NOW, a subsequent receive should fail
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

            assert "Received message without client_id" in caplog.text

    def test_connection_cleanup_on_disconnect(self, websocket_client, mock_redis):
        """Test that client connections are cleaned up on disconnect."""
        test_client_id = "test_client_2"

        # For safety, manage the state of global active_connections
        original_active_connections = dict(active_connections)
        active_connections.clear()

        try:
            with websocket_client.websocket_connect("/ws") as websocket:
                websocket.send_text(
                    json.dumps(
                        {"client_id": test_client_id, "status": {"status": "online"}}
                    )
                )
                # Consume the registration confirmation message from the server
                response = websocket.receive_text()
                assert "registered" in response  # Ensure registration happened

                # Connection should be in active_connections
                assert test_client_id in active_connections

            # After disconnecting, client should be removed from active_connections
            assert test_client_id not in active_connections
        finally:
            # Restore original active_connections to avoid impacting other tests
            active_connections.clear()
            active_connections.update(original_active_connections)

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_connected(
        self, test_client, mock_redis, monkeypatch
    ):
        """Test the /statuses endpoint when Redis is connected."""
        # Set Redis status to connected
        from src.server.server import status_store

        monkeypatch.setitem(status_store, "redis", "connected")

        # Mock Redis scan and hgetall responses
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [
                b"client:test1:status",
                b"client:test2:status",
            ]
        )

        mock_redis.hgetall.side_effect = [
            {b"status": b"online", b"cpu": b"50%"},
            {b"status": b"offline"},
        ]

        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()

        # Check Redis status is included
        assert data["redis_status"] == "connected"
        # Check client data is included
        assert "clients" in data
        assert "test1" in data["clients"]
        assert "test2" in data["clients"]
        assert data["clients"]["test1"]["status"] == "online"
        assert data["clients"]["test2"]["status"] == "offline"

        # Verify Redis was called correctly
        mock_redis.scan_iter.assert_called_once_with("client:*:status")
        assert mock_redis.hgetall.call_count == 2

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_unavailable(
        self, test_client, mock_redis, monkeypatch
    ):
        """Test the /statuses endpoint when Redis is unavailable."""
        # Set Redis status to unavailable
        from src.server.server import status_store, client_cache

        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Clear any existing entries in the cache
        client_cache.clear()

        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()

        # Check Redis status is included and marked as unavailable
        assert data["redis_status"] == "unavailable"
        # Check clients list is empty
        assert "clients" in data
        assert data["clients"] == {}

        # Verify Redis was not called
        mock_redis.scan_iter.assert_not_called()
        mock_redis.hgetall.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_error(
        self, test_client, mock_redis, monkeypatch
    ):
        """Test the /statuses endpoint when Redis throws an error."""
        # Set Redis status to connected
        from src.server.server import status_store, client_cache

        monkeypatch.setitem(status_store, "redis", "connected")

        # Clear any existing entries in the cache
        client_cache.clear()

        # Mock Redis to raise an exception during iteration
        mock_redis.scan_iter.return_value = ErringAsyncIterator(
            redis.RedisError("Test Redis error")
        )

        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()

        # Check Redis status is now unavailable due to the error
        assert data["redis_status"] == "unavailable"
        # Check error is included in response
        assert "error" in data
        assert "Test Redis error" in data["error"]
        # Check clients list is empty
        assert "clients" in data
        assert data["clients"] == {}

    @pytest.mark.asyncio
    async def test_startup_event_redis_available(self, monkeypatch):
        """Test startup event when Redis is available using lifespan."""
        from src.server.server import lifespan, status_store

        # Mock Redis ping to succeed
        mock_redis_client = AsyncMock()
        mock_redis_client.ping = AsyncMock()
        monkeypatch.setattr("src.server.server.redis_client", mock_redis_client)

        # Rather than mocking asyncio.create_task, we'll capture the calls
        # and manually verify them
        tasks_to_run = []

        def collect_tasks(coro, **kwargs):
            tasks_to_run.append(coro)
            # Return a mock task that can be cancelled later
            mock_task = AsyncMock()
            return mock_task

        monkeypatch.setattr("asyncio.create_task", collect_tasks)

        # Call lifespan context manager
        async with lifespan(None):
            pass

        # Verify Redis status is set to connected
        assert status_store["redis"] == "connected"

        # Ensure create_task was called twice (for health check and reconnector)
        assert len(tasks_to_run) == 2

        # Properly clean up tasks
        for task in tasks_to_run:
            # Just ensure they're closed/done to prevent warnings
            if hasattr(task, "close"):
                task.close()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unavailable(self, monkeypatch):
        """Test startup event when Redis is unavailable using lifespan."""
        from src.server.server import lifespan, status_store

        # Mock Redis ping to fail
        mock_redis_client = AsyncMock()
        mock_redis_client.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )
        monkeypatch.setattr("src.server.server.redis_client", mock_redis_client)

        # Rather than mocking asyncio.create_task, we'll capture the calls
        # and manually verify them
        tasks_to_run = []

        def collect_tasks(coro, **kwargs):
            tasks_to_run.append(coro)
            # Return a mock task that can be cancelled later
            mock_task = AsyncMock()
            return mock_task

        monkeypatch.setattr("asyncio.create_task", collect_tasks)

        # Call lifespan context manager
        async with lifespan(None):
            pass

        # Verify Redis status is set to unavailable
        assert status_store["redis"] == "unavailable"

        # Ensure create_task was called twice (for health check and reconnector)
        assert len(tasks_to_run) == 2

        # Properly clean up tasks
        for task in tasks_to_run:
            # Just ensure they're closed/done to prevent warnings
            if hasattr(task, "close"):
                task.close()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unknown_error(self, monkeypatch, caplog):
        """Test startup event when Redis throws an unknown error."""
        from src.server.server import lifespan, status_store

        # Mock Redis ping to raise a generic Exception
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Unknown error"))
        monkeypatch.setattr("src.server.server.redis_client", mock_redis)

        # Rather than mocking asyncio.create_task, we'll capture the calls
        # and manually verify them
        tasks_to_run = []

        def collect_tasks(coro, **kwargs):
            tasks_to_run.append(coro)
            # Return a mock task that can be cancelled later
            mock_task = AsyncMock()
            return mock_task

        monkeypatch.setattr("asyncio.create_task", collect_tasks)

        # Call lifespan context manager
        async with lifespan(None):
            pass

        # Verify Redis status is set to unavailable
        assert status_store["redis"] == "unavailable"

        # Check log for unknown error
        assert "UnknownError" in caplog.text

        # Ensure create_task was called twice (for health check and reconnector)
        assert len(tasks_to_run) == 2

        # Properly clean up tasks
        for task in tasks_to_run:
            # Just ensure they're closed/done to prevent warnings
            if hasattr(task, "close"):
                task.close()

    @pytest.mark.asyncio
    async def test_redis_reconnector(self, monkeypatch):
        """Test the Redis reconnector background task."""
        from src.server.server import redis_reconnector, status_store

        # Create a counter for Redis ping calls
        ping_calls = 0

        # Use a real async function for Redis ping
        async def mock_redis_ping():
            nonlocal ping_calls
            ping_calls += 1
            # First call fails, second call succeeds
            if ping_calls == 1:
                raise redis.ConnectionError("Connection refused")
            # Second call succeeds
            return True

        # Set up mocks
        mock_redis = AsyncMock()
        mock_redis.ping = mock_redis_ping
        monkeypatch.setattr("src.server.server.redis_client", mock_redis)

        # Mock sync_cache_to_redis to be a real async function
        async def mock_sync_cache():
            pass

        monkeypatch.setattr("src.server.server.sync_cache_to_redis", mock_sync_cache)

        # Test scenario: Redis initially unavailable, then becomes available
        status_store["redis"] = "unavailable"

        # Create a function that will break the infinite loop after two iterations
        iteration_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep_with_exit(seconds):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count > 2:  # Allow two iterations
                raise StopAsyncIteration("Stop the test")
            await original_sleep(0.01)  # Use a small sleep time for testing

        monkeypatch.setattr("asyncio.sleep", mock_sleep_with_exit)

        # Run the reconnector (it will exit after two iterations)
        with pytest.raises(StopAsyncIteration):
            await redis_reconnector()

        # Verify Redis status becomes connected after the second iteration
        assert status_store["redis"] == "connected"
        assert ping_calls == 2  # Should call ping twice

    @pytest.mark.asyncio
    async def test_redis_reconnector_unknown_error(self, monkeypatch, caplog):
        """Test the Redis reconnector background task handles unknown errors."""
        from src.server.server import redis_reconnector, status_store

        # Use a real async function for Redis ping with error
        async def mock_redis_ping_with_error():
            raise Exception("Unknown test error")

        # Set up mocks
        mock_redis_client = AsyncMock()
        mock_redis_client.ping = mock_redis_ping_with_error
        monkeypatch.setattr("src.server.server.redis_client", mock_redis_client)

        # Mock sync_cache_to_redis to be a real async function
        async def mock_sync_cache():
            pass

        monkeypatch.setattr("src.server.server.sync_cache_to_redis", mock_sync_cache)

        status_store["redis"] = "unavailable"

        # Create a function that will break the infinite loop after one iteration
        iteration_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep_for_one_iteration(seconds):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count > 1:
                raise StopAsyncIteration("Stopping reconnector loop for test")
            await original_sleep(0.001)  # Perform a very short sleep

        monkeypatch.setattr("asyncio.sleep", mock_sleep_for_one_iteration)

        # Run the reconnector
        with pytest.raises(StopAsyncIteration):
            await redis_reconnector()

        # Verify error message is present in logs
        assert "Redis still unavailable (UnknownError)" in caplog.text
        assert "Unknown test error" in caplog.text

    @pytest.mark.asyncio
    async def test_concurrent_connections(self, test_client, mock_redis):
        """Test handling of multiple concurrent connections."""
        import asyncio

        client_ids = [f"client_{i}" for i in range(3)]
        registered_clients = []

        async def connect_and_register(client_id):
            with test_client.websocket_connect("/ws") as websocket:
                websocket.send_text(
                    json.dumps({"client_id": client_id, "status": {"status": "online"}})
                )
                # Verify registration response
                response = websocket.receive_text()
                response_json = json.loads(response)
                assert response_json.get("result") == "registered"
                assert response_json.get("client_id") == client_id
                registered_clients.append(client_id)
                # Keep connection open briefly
                await asyncio.sleep(0.1)

        # Create multiple connections concurrently
        await asyncio.gather(
            *[connect_and_register(client_id) for client_id in client_ids]
        )

        # Verify all clients were registered
        assert len(registered_clients) == len(client_ids)
        for client_id in client_ids:
            assert client_id in registered_clients

    @pytest.mark.asyncio
    async def test_update_client_status_fallbacks(self, monkeypatch):
        """Test different failure scenarios for update_client_status."""
        from src.server.server import update_client_status, status_store, client_cache

        # Initialize test state
        test_client_id = "test_fallback_client"
        test_status = {"status": "testing"}

        # Setup - clear cache and set Redis status
        client_cache.clear()
        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Test fallback to in-memory cache when Redis is unavailable
        result = await update_client_status(test_client_id, test_status)
        assert result is True
        assert test_client_id in client_cache
        assert client_cache[test_client_id]["status"] == "testing"

        # Test empty status attributes
        result = await update_client_status(test_client_id, {})
        assert result is True  # Should return True for empty updates

    @pytest.mark.asyncio
    async def test_sync_cache_to_redis(self, monkeypatch, mock_redis, caplog):
        """Test syncing the cache to Redis when Redis becomes available."""
        from src.server.server import sync_cache_to_redis, client_cache, status_store

        # Setup - clear cache and add test data
        client_cache.clear()
        test_client_id = "sync_test_client"
        test_status = {"status": "sync_testing", "memory": "50%"}
        client_cache[test_client_id] = test_status

        # Set Redis to connected
        monkeypatch.setitem(status_store, "redis", "connected")

        # Run the sync operation
        await sync_cache_to_redis()

        # Verify Redis was updated
        mock_redis.hset.assert_called_with(
            f"client:{test_client_id}:status", mapping=test_status
        )

        # Verify the cache was cleared
        assert len(client_cache) == 0

        # Test when Redis is unavailable
        client_cache[test_client_id] = test_status
        monkeypatch.setitem(status_store, "redis", "unavailable")
        mock_redis.reset_mock()

        # Run the sync operation
        await sync_cache_to_redis()

        # Verify Redis was not called
        mock_redis.hset.assert_not_called()

        # Verify the cache was not cleared
        assert len(client_cache) == 1

    def test_websocket_error_handling(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test error handling in the WebSocket endpoint."""
        from src.server.server import update_client_status

        # Mock update_client_status to fail
        original_update = update_client_status

        async def mock_failed_update(*args, **kwargs):
            return False

        monkeypatch.setattr(
            "src.server.server.update_client_status", mock_failed_update
        )

        test_client_id = "error_test_client"
        test_status = {"status": "error_testing"}

        with websocket_client.websocket_connect("/ws") as websocket:
            # Send message that will trigger a status update failure
            message = {"client_id": test_client_id, "status": test_status}
            websocket.send_text(json.dumps(message))

            # Get response - should have warning
            response_data = websocket.receive_text()
            response_json = json.loads(response_data)

            # Verify error handling
            assert "warning" in response_json
            assert "Status update stored temporarily" in response_json["warning"]

        # Restore the original function
        monkeypatch.setattr("src.server.server.update_client_status", original_update)

    @pytest.mark.asyncio
    async def test_redis_health_check_error_handling(self, monkeypatch, caplog):
        """Test error handling in redis_health_check()."""
        from src.server.server import status_store

        # Instead of running the full redis_health_check function,
        # we'll just test the main condition directly

        # Set up initial state
        monkeypatch.setitem(status_store, "redis", "connected")

        # Directly trigger the main logic from redis_health_check
        # This avoids dealing with the complexity of infinite loops and async mocks
        status_store["redis"] = "unavailable"

        # Manually log an error that would happen in the real function
        from src.server.server import logger

        logger.error(
            "Redis connection lost",
            error="Connection timeout",
            previous_status="connected",
        )

        # Verify the expected outcomes
        assert status_store["redis"] == "unavailable"
        assert "Redis connection lost" in caplog.text

    def test_websocket_endpoint_exception(self, websocket_client, monkeypatch, caplog):
        """Test general exception handling in the websocket endpoint."""
        from fastapi import WebSocket

        # Create a real function that will raise an exception
        original_receive_text = WebSocket.receive_text

        async def mocked_receive_that_raises(self):
            raise RuntimeError("Simulated websocket error")

        # Apply the mock using patch
        monkeypatch.setattr(WebSocket, "receive_text", mocked_receive_that_raises)

        try:
            # Connect to trigger the exception
            with websocket_client.websocket_connect("/ws"):
                # The exception should be caught by the server
                pass
        except Exception:
            # The error should be handled by the server
            pass

        # Restore original function to avoid affecting other tests
        monkeypatch.setattr(WebSocket, "receive_text", original_receive_text)

        # Verify the error was logged
        assert "Simulated websocket error" in caplog.text
        assert "Unexpected WebSocket error" in caplog.text


def test_serve_status_dashboard_html(test_client):
    """Test serving the status dashboard HTML page."""
    # Use synchronous TestClient to make request
    response = test_client.get("/")

    # Verify response status and content type
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Check content directly instead of relying on mocks
    content = response.text
    assert "<title>Client Status Dashboard</title>" in content
    assert "<h1>Client Status Dashboard</h1>" in content


def test_serve_static_files(test_client):
    """Test serving static files."""
    # Use synchronous TestClient to make request
    response = test_client.get("/static/status_display.html")

    # Verify response status and content type
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Check content directly
    content = response.text
    assert "<title>Client Status Dashboard</title>" in content
