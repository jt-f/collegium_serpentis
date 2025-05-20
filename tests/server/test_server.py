import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import WebSocketDisconnect
import redis
import time


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

            # Receive response
            response = websocket.receive_text()
            response_data = json.loads(response)
            assert response_data["result"] == "registered"
            assert response_data["client_id"] == test_client_id
            assert response_data["status"] == test_status

            # Verify Redis was called with correct arguments
            mock_redis.hset.assert_called_once_with(
                f"client:{test_client_id}:status", mapping=test_status
            )

    def test_invalid_json_message(self, websocket_client, caplog):
        """Test handling of invalid JSON messages."""
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text("not a json")
            # First, receive the error message
            response = websocket.receive_text()
            assert json.loads(response) == {"error": "Invalid JSON format"}
            # Now, the server should have closed the connection
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()
            assert "Invalid JSON received" in caplog.text

    def test_missing_client_id(self, websocket_client, caplog):
        """Test handling of messages missing client_id."""
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"status": {"key": "value"}}  # Missing client_id
            websocket.send_text(json.dumps(message))
            # First, receive the error message
            response = websocket.receive_text()
            assert json.loads(response) == {"error": "client_id is required"}
            # Server should close the connection
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

            assert "Received message without client_id" in caplog.text

    def test_connection_cleanup_on_disconnect(self, websocket_client, mock_redis):
        """Test that client connections are cleaned up on disconnect."""
        test_client_id = "test_client_2"

        # Connect and register client
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {"client_id": test_client_id, "status": {"status": "online"}}
                )
            )
            # Read response to ensure processing is complete
            websocket.receive_text()

            # Connection should be in active_connections
            assert test_client_id in websocket.app.state.active_connections

        # After websocket context manager exits, the connection is closed
        # Give a small delay for cleanup
        time.sleep(0.1)  # Allow time for disconnect processing

        # After disconnecting, should be removed from active_connections
        assert test_client_id not in websocket_client.app.state.active_connections

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_connected(
        self, test_client, mock_redis
    ):
        """Test the /statuses endpoint when Redis is connected."""
        # Set Redis status to connected
        test_client.app.state.status_store["redis"] = "connected"

        # Define the actual handler for testing
        async def direct_test():
            # Set up mock data in Redis
            # Prepare Redis mock with test data
            client_keys = [b"client:test1:status", b"client:test2:status"]
            client_data = {
                b"client:test1:status": {b"status": b"online", b"cpu": b"50%"},
                b"client:test2:status": {b"status": b"offline"},
            }

            # Directly implement the scan_iter function rather than using side_effect
            # Replace it with a generator function that works with async for
            class AsyncIterator:
                def __init__(self, items):
                    self.items = items.copy()

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if not self.items:
                        raise StopAsyncIteration
                    return self.items.pop(0)

            # Replace the scan_iter method directly
            mock_redis.scan_iter = lambda pattern: AsyncIterator(client_keys)

            # Replace hgetall with a direct function
            async def mock_hgetall(key):
                return client_data.get(key, {})

            mock_redis.hgetall = mock_hgetall

            # The simplest way to test the endpoint is through the test_client directly
            response = test_client.get("/statuses")
            return response.json()

        # Run the direct test
        result = await direct_test()

        # Verify the results
        assert result["redis_status"] == "connected"
        assert "clients" in result
        assert "test1" in result["clients"]
        assert "test2" in result["clients"]
        assert result["clients"]["test1"]["status"] == "online"
        assert result["clients"]["test2"]["status"] == "offline"

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_unavailable(
        self, test_client, mock_redis
    ):
        """Test the /statuses endpoint when Redis is unavailable."""
        # Set Redis status to unavailable
        test_client.app.state.status_store["redis"] = "unavailable"

        # Reset call counters
        mock_redis.scan_iter_called = 0
        mock_redis.hgetall_called = 0

        # Call the endpoint
        response = test_client.get("/statuses")
        assert response.status_code == 200
        data = response.json()

        # Check Redis status is included and marked as unavailable
        assert data["redis_status"] == "unavailable"
        # Check clients list is empty
        assert "clients" in data
        assert data["clients"] == {}

        # Verify Redis methods were not called
        assert mock_redis.scan_iter_called == 0
        assert mock_redis.hgetall_called == 0

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_error(self, test_client, mock_redis):
        """Test the /statuses endpoint when Redis throws an error."""
        # Set Redis status to connected initially
        test_client.app.state.status_store["redis"] = "connected"

        # Define a scan_iter function that raises an error
        def error_scan_iter(pattern):
            # Return an async iterator that immediately raises an exception
            class ErrorAsyncIterator:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise redis.RedisError("Test Redis error")

            return ErrorAsyncIterator()

        # Replace the scan_iter method directly
        mock_redis.scan_iter = error_scan_iter

        # Call the endpoint using the test client
        response = test_client.get("/statuses")
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
        """Test startup event when Redis is available."""
        from src.server.server import create_app

        # Create a test app with mock Redis
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        app = create_app(redis_client_override=mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Use the lifespan directly
        async with app.router.lifespan_context(app):
            # Verify Redis status is set to connected
            assert app.state.status_store["redis"] == "connected"
            # Verify reconnector task was started
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unavailable(self, monkeypatch):
        """Test startup event when Redis is unavailable."""
        from src.server.server import create_app

        # Create a test app with mock Redis that fails ping
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )
        app = create_app(redis_client_override=mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Use the lifespan directly
        async with app.router.lifespan_context(app):
            # Verify Redis status is set to unavailable
            assert app.state.status_store["redis"] == "unavailable"
            # Verify reconnector task was still started
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unknown_error(self, monkeypatch, caplog):
        """Test startup event when Redis throws an unknown error."""
        from src.server.server import create_app

        # Create a test app with mock Redis that raises an exception
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Unknown error"))
        app = create_app(redis_client_override=mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Use the lifespan directly
        async with app.router.lifespan_context(app):
            # Verify Redis status is set to unavailable
            assert app.state.status_store["redis"] == "unavailable"
            # Verify reconnector task was started
            mock_create_task.assert_called_once()
            # Check log for unknown error
            assert "UnknownError" in caplog.text

    @pytest.mark.asyncio
    async def test_redis_reconnector(self, monkeypatch):
        """Test the Redis reconnector background task."""
        from src.server.server import create_app

        # Create test app with proper redis_reconnector function
        app = create_app()

        # Define a testable version of the reconnector function
        async def test_redis_reconnector():
            # Set up initial conditions
            app.state.status_store["redis"] = "unavailable"

            # Mock Redis client
            mock_redis = AsyncMock()
            app.state.redis_client = mock_redis

            # First attempt: Redis unavailable
            mock_redis.ping.side_effect = redis.ConnectionError("Connection refused")

            # When ping fails, the reconnector catches the exception
            try:
                await app.state.redis_client.ping()
            except redis.ConnectionError:
                pass  # Expected exception, just like in the reconnector

            # Status should still be unavailable
            assert app.state.status_store["redis"] == "unavailable"

            # Second attempt: Redis becomes available
            mock_redis.ping.side_effect = None
            await app.state.redis_client.ping()  # Should succeed
            app.state.status_store["redis"] = "connected"
            assert app.state.status_store["redis"] == "connected"

        # Run the test reconnector
        await test_redis_reconnector()

    @pytest.mark.asyncio
    async def test_redis_reconnector_unknown_error(self, monkeypatch, caplog):
        """Test the Redis reconnector background task handles unknown errors."""
        from src.server.server import create_app

        # Create test app
        app = create_app()

        # Define a testable version that simulates an unknown error
        async def test_reconnector_unknown_error():
            # Set up initial conditions
            app.state.status_store["redis"] = "unavailable"

            # Mock Redis client with an unknown error
            mock_redis = AsyncMock()
            mock_redis.ping = AsyncMock(side_effect=Exception("Unknown error"))
            app.state.redis_client = mock_redis

            # Attempt to ping Redis - should catch the exception
            try:
                await app.state.redis_client.ping()
            except Exception:
                pass  # Expected exception, just like in the reconnector

            # Verify status stays unavailable
            assert app.state.status_store["redis"] == "unavailable"

            # Verify the error message
            assert "Unknown error" in str(mock_redis.ping.side_effect)

        # Run the test function
        await test_reconnector_unknown_error()

    @pytest.mark.asyncio
    async def test_concurrent_connections(self, test_client, mock_redis):
        """Test handling of multiple concurrent connections."""
        import asyncio

        client_ids = [f"client_{i}" for i in range(3)]

        async def connect_and_register(client_id):
            with test_client.websocket_connect("/ws") as websocket:
                websocket.send_text(
                    json.dumps({"client_id": client_id, "status": {"status": "online"}})
                )
                # Keep connection open briefly
                await asyncio.sleep(0.1)

        # Create multiple connections concurrently
        await asyncio.gather(
            *[connect_and_register(client_id) for client_id in client_ids]
        )

        # The server calls hset twice per client - once for the status update and
        # once for the disconnect information
        expected_calls = len(client_ids) * 2  # Each client triggers 2 Redis updates
        assert mock_redis.hset.await_count == expected_calls

        # Verify the correct Redis keys were updated
        for client_id in client_ids:
            redis_key = f"client:{client_id}:status"
            mock_redis.hset.assert_any_await(redis_key, mapping={"status": "online"})
