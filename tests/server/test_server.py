import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import WebSocketDisconnect
import redis


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

            # Verify Redis was updated
            mock_redis.hset.assert_called_once_with(
                f"client:{test_client_id}:status", mapping=test_status
            )

    def test_invalid_json_message(self, websocket_client, caplog):
        """Test handling of invalid JSON messages."""
        with websocket_client.websocket_connect("/ws") as websocket:
            websocket.send_text("not a json")
            # The server should close the connection on invalid JSON
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

            # Verify error was logged
            assert "Received invalid JSON" in caplog.text

    def test_missing_client_id(self, websocket_client, caplog):
        """Test handling of messages missing client_id."""
        with websocket_client.websocket_connect("/ws") as websocket:
            message = {"status": {"key": "value"}}  # Missing client_id
            websocket.send_text(json.dumps(message))

            # Server should close the connection
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

            assert "client_id is required" in caplog.text

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

            # Connection should be in active_connections
            assert test_client_id in websocket.app.state.active_connections

        # After disconnecting, should be removed from active_connections
        assert test_client_id not in websocket.app.state.active_connections

    @pytest.mark.asyncio
    async def test_get_all_statuses_endpoint_redis_connected(
        self, test_client, mock_redis, monkeypatch
    ):
        """Test the /statuses endpoint when Redis is connected."""
        # Set Redis status to connected
        from src.server import status_store

        monkeypatch.setitem(status_store, "redis", "connected")

        # Mock Redis scan and hgetall responses
        mock_redis.scan_iter.return_value = [
            b"client:test1:status",
            b"client:test2:status",
        ]
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
        from src.server import status_store

        monkeypatch.setitem(status_store, "redis", "unavailable")

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
        from src.server import status_store

        monkeypatch.setitem(status_store, "redis", "connected")

        # Mock Redis to raise an exception
        mock_redis.scan_iter.side_effect = redis.RedisError("Test Redis error")

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
        """Test startup event when Redis is available."""
        from src.server import startup_event, status_store

        # Mock Redis ping to succeed
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        monkeypatch.setattr("src.server.redis_client", mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Call startup event
        await startup_event()

        # Verify Redis status is set to connected
        assert status_store["redis"] == "connected"
        # Verify reconnector task was started
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unavailable(self, monkeypatch):
        """Test startup event when Redis is unavailable."""
        from src.server import startup_event, status_store

        # Mock Redis ping to fail
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )
        monkeypatch.setattr("src.server.redis_client", mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Call startup event
        await startup_event()

        # Verify Redis status is set to unavailable
        assert status_store["redis"] == "unavailable"
        # Verify reconnector task was still started
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_event_redis_unknown_error(self, monkeypatch, caplog):
        """Test startup event when Redis throws an unknown error."""
        from src.server import lifespan, status_store

        # Mock Redis ping to raise a generic Exception
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Unknown error"))
        monkeypatch.setattr("src.server.server.redis_client", mock_redis)

        # Mock asyncio.create_task
        mock_create_task = MagicMock()
        monkeypatch.setattr("asyncio.create_task", mock_create_task)

        # Call lifespan context manager
        async with lifespan(None):
            pass

        # Verify Redis status is set to unavailable
        assert status_store["redis"] == "unavailable"
        # Verify reconnector task was started
        mock_create_task.assert_called_once()
        # Check log for unknown error
        assert "UnknownError" in caplog.text

    @pytest.mark.asyncio
    async def test_redis_reconnector(self, monkeypatch):
        """Test the Redis reconnector background task."""
        from src.server import redis_reconnector, status_store

        # Set up mocks
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        mock_redis = AsyncMock()
        monkeypatch.setattr("src.server.redis_client", mock_redis)

        # Test scenario: Redis initially unavailable, then becomes available
        status_store["redis"] = "unavailable"

        # First attempt: Redis still unavailable
        mock_redis.ping.side_effect = [redis.ConnectionError("Connection refused")]

        # Create a function that will break the infinite loop after one iteration
        iteration_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep_with_exit(seconds):
            nonlocal iteration_count
            iteration_count += 1
            if iteration_count > 1:
                raise StopAsyncIteration("Stop the test")
            await original_sleep(0.01)  # Use a small sleep time for testing

        monkeypatch.setattr("asyncio.sleep", mock_sleep_with_exit)

        # Run the reconnector (it will exit after one iteration)
        with pytest.raises(StopAsyncIteration):
            await redis_reconnector()

        # Verify Redis status remains unavailable after the first failed attempt
        assert status_store["redis"] == "unavailable"

        # Reset for second test
        iteration_count = 0
        mock_redis.ping.side_effect = None
        mock_redis.ping.reset_mock()

        # Second attempt: Redis becomes available
        with pytest.raises(StopAsyncIteration):
            await redis_reconnector()

        # Verify Redis status is now connected
        assert status_store["redis"] == "connected"

    @pytest.mark.asyncio
    async def test_redis_reconnector_unknown_error(self, monkeypatch, caplog):
        """Test the Redis reconnector background task handles unknown errors."""
        from src.server.server import redis_reconnector, status_store

        # Set up mocks
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        # Mock Redis ping to raise a generic Exception
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Unknown error"))
        monkeypatch.setattr("src.server.server.redis_client", mock_redis)

        # Set redis status to unavailable to trigger reconnector logic
        status_store["redis"] = "unavailable"

        # Patch loop to exit after one iteration
        async def mock_sleep_with_exit(seconds):
            raise KeyboardInterrupt()

        monkeypatch.setattr("asyncio.sleep", mock_sleep_with_exit)

        # Run reconnector and expect KeyboardInterrupt to break loop
        with pytest.raises(KeyboardInterrupt):
            await redis_reconnector()
        # Check log for unknown error
        assert "UnknownError" in caplog.text

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

        # Verify all clients were registered in Redis
        assert mock_redis.hset.await_count == len(client_ids)
        for client_id in client_ids:
            mock_redis.hset.assert_any_await(
                f"client:{client_id}:status", mapping={"status": "online"}
            )
