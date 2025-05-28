"""
Unit tests for the WebSocket server.

This module tests all the WebSocket connection behavior, client status management,
and REST API functionality, with appropriate mocking for Redis.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from src.server import config  # Added import
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

    # Mock the connection dictionaries to have a clean slate
    mock_worker_connections = {}
    monkeypatch.setattr("src.server.server.worker_connections", mock_worker_connections)
    mock_frontend_connections = {}
    monkeypatch.setattr(
        "src.server.server.frontend_connections", mock_frontend_connections
    )

    reset_global_server_state()

    yield

    # Additional cleanup if necessary (though monkeypatch should auto-restore)
    reset_global_server_state()
    client_cache.clear()


class TestWebSocketServer:
    def test_websocket_worker_connection_and_registration(
        self, websocket_client, monkeypatch
    ):
        """Test a worker client connects, registers, and is added to worker_connections."""
        # Access the mocked worker_connections directly from the module after monkeypatching in fixture
        from src.server import server as server_module

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            client_id = "test_worker_client"
            registration_message = {
                "client_id": client_id,
                "status": {
                    "client_role": "worker",
                    "client_type": "test_py_agent",
                    "state": "initializing",
                },
            }
            websocket.send_text(json.dumps(registration_message))
            response = websocket.receive_text()  # Server acknowledgement
            data = json.loads(response)

            assert data["result"] == "registration_complete"
            assert data["client_id"] == client_id
            assert client_id in server_module.worker_connections
            # Instead, check if the type is as expected from the server's perspective
            from starlette.websockets import WebSocket as StarletteWebSocket

            assert isinstance(
                server_module.worker_connections[client_id], StarletteWebSocket
            )

    def test_websocket_frontend_connection_and_registration(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test a frontend client connects, registers, receives all_clients_update, and is added to frontend_connections."""
        from src.server import server as server_module

        frontend_connections = server_module.frontend_connections  # Direct access

        # Mock get_all_client_statuses to return some initial data
        mock_all_statuses = {"other_client_1": {"state": "running"}}
        mock_redis.hgetall_side_effect = (
            None  # Clear any previous side effects for hgetall
        )
        monkeypatch.setattr(
            "src.server.server.get_all_client_statuses",
            AsyncMock(return_value=(mock_all_statuses, "redis", None)),
        )

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            client_id = "test_frontend_client"
            registration_message = {
                "client_id": client_id,
                "status": {
                    "client_role": "frontend",
                    "client_type": "test_react_dashboard",
                },
            }
            websocket.send_text(json.dumps(registration_message))

            # Message 1: Broadcast of its own status update (due to its own registration)
            response_A = websocket.receive_text()
            data_A = json.loads(response_A)
            assert data_A["type"] == "client_status_update"
            assert data_A["client_id"] == client_id
            # Optionally, assert more about data_A["status"] if needed

            # Message 2: All clients update (since it's a frontend client)
            response_B = websocket.receive_text()
            data_B = json.loads(response_B)
            assert data_B["type"] == "all_clients_update"
            assert data_B["data"]["clients"] == mock_all_statuses
            assert data_B["data"]["redis_status"] == status_store["redis"]

            # Message 3: Registration complete acknowledgment
            response_C = websocket.receive_text()
            data_C = json.loads(response_C)
            assert data_C["result"] == "registration_complete"
            assert data_C["client_id"] == client_id

            assert client_id in frontend_connections
            from starlette.websockets import WebSocket as StarletteWebSocket

            assert isinstance(frontend_connections[client_id], StarletteWebSocket)

    def test_register_client_with_status_updates_redis(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test registration of a worker client updates Redis and broadcasts."""
        from src.server import server as server_module

        worker_connections = server_module.worker_connections  # Direct access
        # Mock broadcast_to_frontends directly on the module
        mock_broadcast_actual = AsyncMock()
        monkeypatch.setattr(
            server_module, "broadcast_to_frontends", mock_broadcast_actual
        )
        monkeypatch.setitem(
            status_store, "redis", "connected"
        )  # Ensure Redis is marked as connected

        # REMOVED local patch for src.server.redis_manager.get_client_info
        # Rely on conftest.py mock_redis.hgetall returning {} by default.
        # with patch("src.server.redis_manager.get_client_info", AsyncMock(return_value=None)) as mock_internal_get_client_info:
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            client_id = "test_worker_redis"
            status_payload = {
                "state": "active",
                "cpu_usage": "25%",
                "client_role": "worker",
            }
            message = {
                "client_id": client_id,
                "status": status_payload,
            }
            websocket.send_text(json.dumps(message))
            # For a worker, the server sends only one message back: registration_complete.
            # The broadcast_to_frontends call doesn't send to this worker.
            response = websocket.receive_text()  # This should be registration_complete
            data = json.loads(response)

            assert data["result"] == "registration_complete"
            assert client_id in worker_connections

            # Check Redis update via update_client_status call
            mock_redis.hset.assert_called()
            args, kwargs = mock_redis.hset.call_args
            assert args[0] == f"client:{client_id}:status"
            assert kwargs["mapping"]["connected"] == "true"
            assert "connect_time" in kwargs["mapping"]
            assert "last_seen" in kwargs["mapping"]
            assert kwargs["mapping"]["client_role"] == "worker"
            assert kwargs["mapping"]["state"] == "active"
            assert kwargs["mapping"]["cpu_usage"] == "25%"

            # Check broadcast was called
            # Construct the expected status that get_client_info (called by server for broadcast)
            # should return. This is based on what hset just wrote.
            expected_status_for_broadcast_get = {
                b"client_id": client_id.encode(),
                b"connected": b"true",
                b"connect_time": mock_redis.hset.call_args.kwargs["mapping"][
                    "connect_time"
                ].encode(),
                b"last_seen": mock_redis.hset.call_args.kwargs["mapping"][
                    "last_seen"
                ].encode(),
                b"client_role": status_payload[
                    "client_role"
                ].encode(),  # from original status_payload
                b"state": status_payload["state"].encode(),
                b"cpu_usage": status_payload["cpu_usage"].encode(),
            }
            mock_redis.hgetall.return_value = expected_status_for_broadcast_get

            # The with patch block for get_client_info is removed here.
            # The server will call the real get_client_info, which will use the mock_redis.hgetall configured above.

            mock_broadcast_actual.assert_called_once()  # Check the actual mock
            broadcast_args = mock_broadcast_actual.call_args[0][0]
            assert broadcast_args["type"] == "client_status_update"
            assert broadcast_args["client_id"] == client_id
            assert broadcast_args["status"] is not None
            assert broadcast_args["status"]["client_role"] == "worker"
            assert broadcast_args["status"]["state"] == "active"

    def test_registration_missing_client_id_or_role(self, websocket_client, caplog):
        """Test registration failure if client_id or client_role is missing."""
        test_cases = [
            (
                {"status": {"client_role": "worker"}},
                "client_id and client_role in status are required for registration",
            ),  # Missing client_id
            (
                {"client_id": "some_id", "status": {}},
                "client_id and client_role in status are required for registration",
            ),  # Missing client_role
            (
                {"client_id": "some_id"},
                "client_id and client_role in status are required for registration",
            ),  # Missing status and client_role
        ]
        for message, error_detail in test_cases:
            with websocket_client.websocket_connect(
                config.WEBSOCKET_ENDPOINT_PATH
            ) as websocket:
                websocket.send_text(json.dumps(message))
                response = websocket.receive_text()
                data = json.loads(response)
                assert data.get("error") == error_detail
                # Connection should be closed by server with code 1008
                # TestClient doesn't easily expose close code, so we check logs or behavior.
                # For now, ensuring error message is prime concern.

    def test_invalid_json_message_during_registration(self, websocket_client, caplog):
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            websocket.send_text("invalid json")
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["error"] == "Invalid JSON format for registration"

    # Old tests for missing client_id, invalid json can be adapted or removed if covered by new ones.
    # test_invalid_json_message and test_missing_client_id might be redundant now.

    def test_subsequent_status_update_from_worker(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test that a worker client can send further status updates after registration."""
        from src.server import server as server_module

        worker_connections = server_module.worker_connections  # Direct access
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(server_module, "broadcast_to_frontends", broadcast_mock)
        monkeypatch.setitem(
            status_store, "redis", "connected"
        )  # Ensure Redis is marked as connected
        mock_redis.hset.reset_mock()

        client_id = "worker_updater"

        # Simulate the client's state in Redis *after* registration but *before* the subsequent update.
        # Configure mock_redis.hgetall directly for this test.
        mock_state_after_reg_in_redis = {
            b"client_id": client_id.encode(),
            b"client_role": b"worker",
            b"temp": b"30C",  # From registration
            b"connected": b"true",
            b"connect_time": b"some_iso_time_str",
            b"last_seen": b"some_iso_time_str",
        }
        mock_redis.hgetall.return_value = mock_state_after_reg_in_redis

        # REMOVED local patch for src.server.redis_manager.get_client_info
        # with patch("src.server.redis_manager.get_client_info", AsyncMock(return_value=mock_state_after_reg)) as mock_internal_get_client_info:
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Registration
            reg_message = {
                "client_id": client_id,
                "status": {"client_role": "worker", "temp": "30C"},
            }
            websocket.send_text(json.dumps(reg_message))
            # Worker client receives only one message (registration_complete) after registration.
            websocket.receive_text()  # Consume registration_complete ack
            assert client_id in worker_connections
            mock_redis.hset.reset_mock()  # Clear calls from registration phase
            broadcast_mock.reset_mock()  # Also reset broadcast mock

            # Subsequent status update
            update_status_payload = {"temp": "35C", "load": "high"}
            update_message = {"client_id": client_id, "status": update_status_payload}

            # Mock datetime for predictable last_seen timestamp
            fixed_timestamp_iso = "2024-01-01T12:00:00.000000+00:00"

            # 1. Mock the datetime instance that .now(UTC) would return
            mock_now_dt_instance = MagicMock()
            mock_now_dt_instance.isoformat.return_value = fixed_timestamp_iso

            # 2. Mock the .now() method of the datetime module
            mock_now_method = MagicMock(return_value=mock_now_dt_instance)

            # 3. Mock the datetime module itself, assigning the mocked .now() and real UTC
            mock_datetime_module = MagicMock()
            mock_datetime_module.now = mock_now_method
            from datetime import UTC  # Import real UTC to assign to mock

            mock_datetime_module.UTC = UTC

            updated_state_in_redis_for_hgetall = {
                **mock_state_after_reg_in_redis,
                b"temp": b"35C",
                b"load": b"high",
                b"last_seen": fixed_timestamp_iso.encode(),
            }
            mock_redis.hgetall.return_value = updated_state_in_redis_for_hgetall

            with patch("src.server.server.datetime", mock_datetime_module):
                websocket.send_text(json.dumps(update_message))
                response = websocket.receive_text()
                data = json.loads(response)

            assert data["result"] == "message_processed"
            assert data["client_id"] == client_id
            assert "temp" in data["status_updated"]
            assert "load" in data["status_updated"]

            # Check Redis hset was called for the update
            mock_redis.hset.assert_called_once()
            args, kwargs = mock_redis.hset.call_args
            assert args[0] == f"client:{client_id}:status"
            assert kwargs["mapping"]["temp"] == "35C"
            assert kwargs["mapping"]["load"] == "high"
            assert "last_seen" in kwargs["mapping"]

            # Check broadcast was called with the updated status
            # The server internally calls get_client_info, which uses mock_redis.hgetall.
            # We need to ensure mock_redis.hgetall will return the state *after* the update.
            broadcast_mock.assert_called_once()
            broadcast_args = broadcast_mock.call_args[0][0]
            assert broadcast_args["type"] == "client_status_update"
            assert broadcast_args["client_id"] == client_id
            assert broadcast_args["status"]["temp"] == "35C"
            assert broadcast_args["status"]["load"] == "high"
            assert (
                broadcast_args["status"]["last_seen"] == fixed_timestamp_iso
            )  # Assert fixed timestamp

    @pytest.mark.asyncio
    async def test_connection_cleanup_on_disconnect_worker(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test cleanup for a worker client disconnecting."""
        from src.server import server as server_module

        worker_connections = server_module.worker_connections  # Direct access
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(server_module, "broadcast_to_frontends", broadcast_mock)
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset.reset_mock()

        client_id = "test_cleanup_worker"
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register client
            message = {
                "client_id": client_id,
                "status": {"client_role": "worker", "state": "active"},
            }
            websocket.send_text(json.dumps(message))
            websocket.receive_text()  # Ack registration
            assert client_id in worker_connections
            call_count_after_reg = mock_redis.hset.call_count

            # Reset the broadcast mock after registration messages are handled
            # if we are only interested in the broadcast caused by disconnect.
            broadcast_mock.reset_mock()

        # After WebSocket closes (implicitly by exiting 'with')
        assert client_id not in worker_connections

        # Check Redis hset was called for disconnect
        assert mock_redis.hset.call_count == call_count_after_reg + 1
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{client_id}:status"
        assert kwargs["mapping"]["connected"] == "false"
        assert "disconnect_time" in kwargs["mapping"]
        assert kwargs["mapping"]["status_detail"] == "Disconnected by client"

        # Check broadcast of disconnect state
        expected_broadcast_status_on_disconnect = {
            "client_id": client_id,
            "client_role": "worker",
            "connected": "false",
            "disconnect_time": mock_redis.hset.call_args.kwargs["mapping"][
                "disconnect_time"
            ],
            "status_detail": "Disconnected by client",
            "state": "active",  # Old state might still be there if not overwritten
        }
        with patch(
            "src.server.server.get_client_info",
            AsyncMock(return_value=expected_broadcast_status_on_disconnect),
        ):
            broadcast_mock.assert_called_once()
            broadcast_args = broadcast_mock.call_args[0][0]
            assert broadcast_args["type"] == "client_status_update"
            assert broadcast_args["client_id"] == client_id
            assert broadcast_args["status"]["connected"] == "false"
            assert broadcast_args["status"]["status_detail"] == "Disconnected by client"

    # test_connection_cleanup_on_disconnect can be removed or adapted into the worker/frontend specific versions.


class TestClientControlAPI:
    TEST_CLIENT_ID = "test-control-client"

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_disconnect_client_success(
        self,
        mock_broadcast: AsyncMock,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        # Set up the mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        mock_ws.close = AsyncMock()

        # Patch the worker_connections in the server module
        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        # Ensure frontend_connections is an empty dict for this test type
        monkeypatch.setattr("src.server.server.frontend_connections", {})

        monkeypatch.setitem(status_store, "redis", "connected")

        # Configure Redis mock for initial state check
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"running",
            b"client_role": b"worker",  # Important for get_client_info
        }
        mock_redis.hset = AsyncMock()
        # Mock get_client_info for broadcast_client_state_change
        # It will be called after the update_client_status call.
        # The status passed to broadcast should reflect the disconnection.
        disconnect_status_for_broadcast = {
            "connected": "false",
            "status_detail": "Disconnected by server request (HTTP)",
            # disconnect_time will be dynamic, client_state: "offline"
        }
        # Patch get_client_info that broadcast_client_state_change will use
        with patch(
            "src.server.server.get_client_info"
        ) as mock_get_client_info_for_broadcast:
            # This mock will be used by broadcast_client_state_change
            # after the client status has been updated in Redis by update_client_status.
            # So, it should return the state *after* disconnect.
            mock_get_client_info_for_broadcast.return_value = (
                disconnect_status_for_broadcast
            )

            response = test_client.post(
                config.DISCONNECT_CLIENT_ENDPOINT_PATH.format(
                    client_id=self.TEST_CLIENT_ID
                )
            )

        assert response.status_code == 200
        # The message changed slightly in server implementation
        assert "Disconnection process for client" in response.json()["message"]
        assert "initiated and broadcasted" in response.json()["message"]

        # Check that the WebSocket was called correctly
        mock_ws.send_text.assert_awaited_once_with(
            json.dumps({"command": "disconnect"})
        )
        mock_ws.close.assert_awaited_once_with(
            code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
            reason="Server initiated disconnect via HTTP",  # Updated reason
        )

        # Check that the client was removed from active connections
        assert self.TEST_CLIENT_ID not in test_worker_connections

        # Check that Redis was updated with disconnect status (via update_client_status)
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["connected"] == "false"
        assert (
            kwargs["mapping"]["status_detail"]
            == "Disconnected by server request (HTTP)"
        )  # Updated detail
        assert "disconnect_time" in kwargs["mapping"]
        assert kwargs["mapping"]["client_state"] == "offline"  # Added check

        # Check that broadcast_client_state_change was called
        mock_broadcast.assert_awaited_once()
        call_args_list = mock_broadcast.call_args_list
        assert call_args_list[0][0][0] == self.TEST_CLIENT_ID  # client_id

        status_update_arg = call_args_list[0][0][1]
        assert status_update_arg["connected"] == "false"
        assert (
            status_update_arg["status_detail"]
            == "Disconnected by server request (HTTP)"
        )
        assert status_update_arg["client_state"] == "offline"
        assert "disconnect_time" in status_update_arg

    def test_disconnect_client_not_active_but_in_redis_connected(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        # Ensure worker_connections is empty
        monkeypatch.setattr("src.server.server.worker_connections", {})
        monkeypatch.setitem(status_store, "redis", "connected")

        # Simulate client exists in Redis and is connected
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_role": b"worker",
        }
        # Mock get_client_info which is used to check current status from Redis
        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = {
                "connected": "true",
                "client_role": "worker",
            }
            response = test_client.post(
                config.DISCONNECT_CLIENT_ENDPOINT_PATH.format(
                    client_id=self.TEST_CLIENT_ID
                )
            )

        assert (
            response.status_code == 200
        )  # Server now proceeds to mark as disconnected in Redis
        # The message reflects that it's processing the disconnect even if not in active_connections
        assert "Disconnection process for client" in response.json()["message"]

        # Redis should be updated to disconnected
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["connected"] == "false"
        assert (
            kwargs["mapping"]["status_detail"]
            == "Disconnected by server request (HTTP)"
        )

    def test_disconnect_client_already_disconnected_in_redis(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setattr(
            "src.server.server.worker_connections", {}
        )  # Not in active connections
        monkeypatch.setitem(status_store, "redis", "connected")

        # Simulate client already disconnected in Redis
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"client_role": b"worker",
            b"disconnect_time": b"some_time_ago",
        }
        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = {
                "connected": "false",
                "client_role": "worker",
                "disconnect_time": "some_time_ago",
            }
            response = test_client.post(
                config.DISCONNECT_CLIENT_ENDPOINT_PATH.format(
                    client_id=self.TEST_CLIENT_ID
                )
            )

        assert (
            response.status_code == 200
        )  # Server now allows "disconnecting" an already disconnected client
        assert (
            "already disconnected. Status re-broadcasted." in response.json()["message"]
        )

        # Redis should still be called to ensure the disconnect state is affirmed/re-broadcasted
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert kwargs["mapping"]["connected"] == "false"

    def test_disconnect_client_not_found_anywhere(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setattr("src.server.server.worker_connections", {})
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {}  # Not in Redis

        with patch("src.server.server.get_client_info") as mock_get_client_info:
            mock_get_client_info.return_value = None  # Truly not found
            response = test_client.post(
                config.DISCONNECT_CLIENT_ENDPOINT_PATH.format(
                    client_id=self.TEST_CLIENT_ID
                )
            )

        # Even if not found, server attempts to mark as disconnected in Redis
        # This behavior changed: server will try to set a "disconnected" status.
        assert response.status_code == 200
        assert "Disconnection process for client" in response.json()["message"]

        mock_redis.hset.assert_awaited_once()  # Ensures an attempt to write disconnect status
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["connected"] == "false"

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_pause_client_success(
        self,
        mock_broadcast: AsyncMock,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()

        # Mock get_client_info for broadcast
        # This will be called by broadcast_client_state_change AFTER update_client_status
        # So it should reflect the 'paused' state.
        paused_status_for_broadcast = {
            "client_state": "paused",
            "client_id": self.TEST_CLIENT_ID,
            "connected": "true",
        }
        with patch(
            "src.server.server.get_client_info",
            AsyncMock(return_value=paused_status_for_broadcast),
        ):
            response = test_client.post(
                config.PAUSE_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )

        assert response.status_code == 200
        # Message updated in server.py
        assert "Pause command processed for client" in response.json()["message"]
        assert "State updated and broadcasted" in response.json()["message"]

        mock_ws.send_text.assert_awaited_once_with(json.dumps({"command": "pause"}))

        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "paused"

        mock_broadcast.assert_awaited_once()
        call_args_list = mock_broadcast.call_args_list
        assert call_args_list[0][0][0] == self.TEST_CLIENT_ID  # client_id
        status_update_arg = call_args_list[0][0][1]
        assert status_update_arg["client_state"] == "paused"

    def test_pause_client_not_found_or_disconnected_in_redis(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setattr(
            "src.server.server.worker_connections", {}
        )  # Not in active WebSocket connections
        monkeypatch.setitem(status_store, "redis", "connected")

        # Scenario 1: Client not in Redis at all
        mock_redis.hgetall.return_value = {}
        with patch("src.server.server.get_client_info", AsyncMock(return_value=None)):
            response = test_client.post(
                config.PAUSE_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )
        assert response.status_code == 404
        assert "not found or already disconnected" in response.json()["detail"]

        # Scenario 2: Client in Redis but marked as disconnected
        mock_redis.hgetall.return_value = {b"connected": b"false"}
        with patch(
            "src.server.server.get_client_info",
            AsyncMock(return_value={"connected": "false"}),
        ):
            response = test_client.post(
                config.PAUSE_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )
        assert response.status_code == 404
        assert "not found or already disconnected" in response.json()["detail"]

    # Renamed test_pause_client_not_found to be more specific

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_pause_client_websocket_disconnect_on_send(
        self,
        mock_broadcast: AsyncMock,  # broadcast will still be called as state is updated
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        mock_ws.close = AsyncMock()  # Should not be called by pause endpoint directly

        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()  # For the status_update to "paused"

        response = test_client.post(
            config.PAUSE_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
        )

        # Server now proceeds to update state to 'paused' even if send fails due to disconnect
        assert response.status_code == 200
        assert "Pause command processed for client" in response.json()["message"]

        # Check client removed from active connections due to error handling in server
        assert self.TEST_CLIENT_ID not in test_worker_connections

        # Redis should be updated to 'paused'
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "paused"

        # Broadcast should still occur for the state change to "paused"
        mock_broadcast.assert_awaited_once()
        status_update_arg = mock_broadcast.call_args[0][1]
        assert status_update_arg["client_state"] == "paused"

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_pause_client_runtime_error_on_send(
        self,
        mock_broadcast: AsyncMock,  # broadcast should still be called
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        mock_ws = AsyncMock()
        # Simulate RuntimeError, e.g., if connection is already closed by client
        mock_ws.send_text = AsyncMock(
            side_effect=RuntimeError("Connection already closed")
        )
        mock_ws.close = AsyncMock()

        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()  # For update to "paused"

        # Server behavior changed: it logs the error and proceeds to update state to "paused".
        # It does not raise 500 from the (WebSocketDisconnect, RuntimeError) block anymore for send_text.
        # The raise HTTPException for other Exception types is still there.
        response = test_client.post(
            config.PAUSE_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
        )

        assert response.status_code == 200  # Proceeds to update state
        assert "Pause command processed for client" in response.json()["message"]

        # Check client removed from active connections due to error handling
        assert self.TEST_CLIENT_ID not in test_worker_connections

        # Redis should be updated to 'paused'
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "paused"

        mock_broadcast.assert_awaited_once()
        status_update_arg = mock_broadcast.call_args[0][1]
        assert status_update_arg["client_state"] == "paused"

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_resume_client_success(
        self,
        mock_broadcast: AsyncMock,
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")

        # Simulate client is paused in Redis
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"client_state": b"paused",
            b"client_role": b"worker",
        }
        mock_redis.hset = AsyncMock()

        # Mock get_client_info for broadcast
        running_status_for_broadcast = {
            "client_state": "running",
            "client_id": self.TEST_CLIENT_ID,
            "connected": "true",
        }
        with patch(
            "src.server.server.get_client_info",
            AsyncMock(return_value=running_status_for_broadcast),
        ):
            response = test_client.post(
                config.RESUME_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )

        assert response.status_code == 200
        assert "Resume command processed for client" in response.json()["message"]
        assert "State updated and broadcasted" in response.json()["message"]

        mock_ws.send_text.assert_awaited_once_with(json.dumps({"command": "resume"}))

        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "running"

        mock_broadcast.assert_awaited_once()
        call_args_list = mock_broadcast.call_args_list
        assert call_args_list[0][0][0] == self.TEST_CLIENT_ID  # client_id
        status_update_arg = call_args_list[0][0][1]
        assert status_update_arg["client_state"] == "running"

    def test_resume_client_not_found_or_disconnected_in_redis(
        self, test_client: TestClient, mock_redis: AsyncMock, monkeypatch
    ):
        monkeypatch.setattr("src.server.server.worker_connections", {})
        monkeypatch.setitem(status_store, "redis", "connected")

        # Scenario 1: Client not in Redis
        mock_redis.hgetall.return_value = {}  # Not in Redis
        with patch("src.server.server.get_client_info", AsyncMock(return_value=None)):
            response = test_client.post(
                config.RESUME_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )
        assert response.status_code == 404
        assert "not found or already disconnected" in response.json()["detail"]

        # Scenario 2: Client in Redis but disconnected
        mock_redis.hgetall.return_value = {
            b"connected": b"false"
        }  # In Redis but disconnected
        with patch(
            "src.server.server.get_client_info",
            AsyncMock(return_value={"connected": "false"}),
        ):
            response = test_client.post(
                config.RESUME_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
            )
        assert response.status_code == 404
        assert "not found or already disconnected" in response.json()["detail"]

    # Renamed test_resume_client_not_found to be more specific

    @patch("src.server.server.broadcast_client_state_change", new_callable=AsyncMock)
    def test_resume_client_websocket_disconnect_on_send(
        self,
        mock_broadcast: AsyncMock,  # Broadcast should still be called
        test_client: TestClient,
        mock_redis: AsyncMock,
        monkeypatch,
    ):
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        mock_ws.close = AsyncMock()

        test_worker_connections = {self.TEST_CLIENT_ID: mock_ws}
        monkeypatch.setattr(
            "src.server.server.worker_connections", test_worker_connections
        )
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()  # For update to "running"

        response = test_client.post(
            config.RESUME_CLIENT_ENDPOINT_PATH.format(client_id=self.TEST_CLIENT_ID)
        )

        # Server updates state to 'running' even if send fails due to disconnect
        assert response.status_code == 200
        assert "Resume command processed for client" in response.json()["message"]

        assert self.TEST_CLIENT_ID not in test_worker_connections  # Cleaned up

        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == f"client:{self.TEST_CLIENT_ID}:status"
        assert kwargs["mapping"]["client_state"] == "running"

        mock_broadcast.assert_awaited_once()
        status_update_arg = mock_broadcast.call_args[0][1]
        assert status_update_arg["client_state"] == "running"


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
    response = test_client.get(config.STATUSES_ENDPOINT_PATH)
    assert response.status_code == 200
    data = response.json()
    assert "redis_status" in data
    assert "clients" in data
    assert "data_source" in data


def test_health_endpoint(test_client):
    """Test the /health endpoint."""
    response = test_client.get(config.HEALTH_ENDPOINT_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "redis_status" in data


def test_cors_headers_not_set_by_default(test_client):
    """Test that CORS headers are not set by default (handled by frontend proxy)."""
    response = test_client.get(config.HEALTH_ENDPOINT_PATH)
    assert response.status_code == 200
    # CORS is handled by the frontend development proxy, not the backend
    assert "access-control-allow-origin" not in response.headers


def test_api_endpoints_structure(test_client):
    """Test that our API endpoints are properly structured."""
    # Test health endpoint
    health_response = test_client.get(config.HEALTH_ENDPOINT_PATH)
    assert health_response.status_code == 200
    health_data = health_response.json()
    assert "status" in health_data
    assert "redis_status" in health_data

    # Test statuses endpoint
    statuses_response = test_client.get(config.STATUSES_ENDPOINT_PATH)
    assert statuses_response.status_code == 200
    statuses_data = statuses_response.json()
    assert "redis_status" in statuses_data
    assert "clients" in statuses_data
    assert "data_source" in statuses_data


def test_registered_routes(test_app):
    """Test that the expected API endpoints are registered with their methods."""
    routes = test_app.routes
    # Extract paths and methods from registered routes
    registered_paths = {}
    for route in routes:
        if hasattr(route, "path") and hasattr(route, "methods") and route.methods:
            registered_paths[route.path] = list(route.methods)

    # Define expected paths and their methods
    expected_routes = {
        config.HEALTH_ENDPOINT_PATH: ["GET"],
        config.STATUSES_ENDPOINT_PATH: ["GET"],
        config.DISCONNECT_CLIENT_ENDPOINT_PATH: ["POST"],
        config.PAUSE_CLIENT_ENDPOINT_PATH: ["POST"],
        config.RESUME_CLIENT_ENDPOINT_PATH: ["POST"],
    }

    for path, methods in expected_routes.items():
        # For paths with parameters like {client_id}, we need to check the base path
        # FastAPI's routing table will show the path with the parameter placeholder
        if "{" in path and "}" in path:
            base_path = path.split("{")[0]
            found = False
            for registered_path in registered_paths:
                if registered_path.startswith(base_path):
                    assert methods[0] in registered_paths[registered_path]
                    found = True
                    break
            assert found, f"Expected path {path} not found in registered routes."
        else:
            assert (
                path in registered_paths
            ), f"Path {path} not found in registered routes."
            assert (
                methods[0] in registered_paths[path]
            ), f"Method {methods[0]} not found for path {path}."
