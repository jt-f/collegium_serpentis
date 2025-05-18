import json
import pytest
from fastapi import WebSocketDisconnect


class TestWebSocketServer:
    def test_websocket_connection(self, websocket_client):
        """Test that a WebSocket connection can be established."""
        with websocket_client.websocket_connect("/ws") as websocket:
            assert websocket.client_state.name == "CONNECTED"

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
    async def test_get_all_statuses_endpoint(self, test_client, mock_redis):
        """Test the /statuses endpoint."""
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

        assert "test1" in data
        assert "test2" in data
        assert data["test1"]["status"] == "online"
        assert data["test2"]["status"] == "offline"

        # Verify Redis was called correctly
        mock_redis.scan_iter.assert_called_once_with("client:*:status")
        assert mock_redis.hgetall.call_count == 2

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
