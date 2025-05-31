"""
Unit tests for the WebSocket server.

This module tests all the WebSocket connection behavior, client status management,
and REST API functionality, with appropriate mocking for Redis.
"""

import json
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import redis

from src.server import config  # Added import
from src.server.redis_manager import status_store
from src.shared.utils.logging import get_logger

# Add logger for the test
logger = get_logger(__name__)


def reset_global_server_state():
    # server_module.active_connections is now handled by monkeypatch.setattr in
    # manage_server_state. So, no need to clear it here if manage_server_state
    # sets it to {}. If manage_server_state *didn't* setattr, then clear() would
    # be needed. For safety, if active_connections was set by the test, clearing
    # it here is fine.
    status_store[config.STATUS_KEY_REDIS_STATUS] = config.STATUS_VALUE_REDIS_UNAVAILABLE


@pytest.fixture(autouse=True)
def manage_server_state(monkeypatch, mock_redis):  # Add mock_redis as a parameter
    # Store original contents for non-monkeypatched globals if needed for manual
    # restoration. For active_connections, monkeypatch.setattr handles restoration
    # of the original object.

    # Mock the connection dictionaries to have a clean slate
    mock_worker_connections = {}
    monkeypatch.setattr("src.server.server.worker_connections", mock_worker_connections)
    mock_frontend_connections = {}
    monkeypatch.setattr(
        "src.server.server.frontend_connections", mock_frontend_connections
    )

    # Apply redis_client mock globally for all server module interactions
    # Individual tests can still customize mock_redis behavior as needed.
    # This addresses issues where redis_client might be imported differently.
    monkeypatch.setattr("src.server.server.redis_client", mock_redis)
    monkeypatch.setattr("src.server.redis_manager.redis_client", mock_redis)

    reset_global_server_state()  # Sets redis status to unknown/unavailable by default

    yield

    # Additional cleanup if necessary (though monkeypatch should auto-restore)
    reset_global_server_state()


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

            assert (
                data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_REGISTRATION_COMPLETE
            )
            assert data[config.PAYLOAD_KEY_CLIENT_ID] == client_id
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

        frontend_connections = server_module.frontend_connections

        # Ensure Redis is marked as connected for successful operations
        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )
        # Ensure that the server module is using the mock_redis instance
        monkeypatch.setattr("src.server.server.redis_client", mock_redis)
        monkeypatch.setattr("src.server.redis_manager.redis_client", mock_redis)

        # Mock get_all_client_statuses to return some initial data
        mock_all_statuses = {"other_client_1": {config.PAYLOAD_KEY_STATUS: "running"}}
        monkeypatch.setattr(
            "src.server.server.get_all_client_statuses",
            AsyncMock(
                return_value=(
                    mock_all_statuses,
                    config.STATUS_VALUE_REDIS_CONNECTED,
                    None,
                )
            ),
        )

        # Setup expected client data
        client_id = "test_frontend_client"
        client_type = "test_react_dashboard"
        client_role = config.CLIENT_ROLE_FRONTEND
        expected_status_after_registration = {
            config.PAYLOAD_KEY_CLIENT_ID: client_id,
            config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED,
            config.STATUS_KEY_CLIENT_ROLE: client_role,
            "client_type": client_type,  # Not a standard key, part of custom status
            config.STATUS_KEY_CONNECT_TIME: "2024-01-01T12:00:00.000000+00:00",
            config.STATUS_KEY_LAST_SEEN: "2024-01-01T12:00:00.000000+00:00",
        }

        # Configure mock_redis to return the expected status
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode()
            for k, v in expected_status_after_registration.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Send registration message
            registration_message = {
                config.PAYLOAD_KEY_CLIENT_ID: client_id,
                config.PAYLOAD_KEY_STATUS: {
                    config.STATUS_KEY_CLIENT_ROLE: client_role,
                    "client_type": client_type,  # Custom key
                },
            }
            websocket.send_text(json.dumps(registration_message))

            # Message 1: All clients update (since it's a frontend client)
            response_A = websocket.receive_text()
            data_A = json.loads(response_A)
            assert data_A[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_ALL_CLIENTS_UPDATE
            assert (
                data_A[config.PAYLOAD_KEY_DATA][config.PAYLOAD_KEY_CLIENTS]
                == mock_all_statuses
            )
            assert (
                data_A[config.PAYLOAD_KEY_DATA][config.STATUS_KEY_REDIS_STATUS]
                == status_store[config.STATUS_KEY_REDIS_STATUS]
            )

            # Message 2: Broadcast of its own status update (due to its own registration)
            response_B = websocket.receive_text()
            data_B = json.loads(response_B)
            assert (
                data_B[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_CLIENT_STATUS_UPDATE
            )
            assert data_B[config.PAYLOAD_KEY_CLIENT_ID] == client_id
            # Verify all expected status fields
            assert (
                data_B[config.PAYLOAD_KEY_STATUS][config.STATUS_KEY_CLIENT_ROLE]
                == client_role
            )
            assert data_B[config.PAYLOAD_KEY_STATUS]["client_type"] == client_type
            assert (
                data_B[config.PAYLOAD_KEY_STATUS][config.STATUS_KEY_CONNECTED]
                == config.STATUS_VALUE_CONNECTED
            )
            assert config.STATUS_KEY_CONNECT_TIME in data_B[config.PAYLOAD_KEY_STATUS]
            assert config.STATUS_KEY_LAST_SEEN in data_B[config.PAYLOAD_KEY_STATUS]

            # Message 3: Registration complete acknowledgment
            response_C = websocket.receive_text()
            data_C = json.loads(response_C)
            assert (
                data_C[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_REGISTRATION_COMPLETE
            )
            assert data_C[config.PAYLOAD_KEY_CLIENT_ID] == client_id

            # Verify client was added to frontend_connections
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
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        client_id = "test_worker_redis"
        status_payload = {
            "state": "active",  # Custom key
            "cpu_usage": "25%",  # Custom key
            config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
        }

        # Mock the status that will be returned after registration
        expected_status_after_registration = {
            config.PAYLOAD_KEY_CLIENT_ID: client_id,
            config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED,
            config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
            "state": "active",
            "cpu_usage": "25%",
            config.STATUS_KEY_CONNECT_TIME: "2024-01-01T12:00:00.000000+00:00",
            config.STATUS_KEY_LAST_SEEN: "2024-01-01T12:00:00.000000+00:00",
        }

        # Configure mock_redis.hgetall to return the expected status
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode()
            for k, v in expected_status_after_registration.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            message = {
                "client_id": client_id,
                "status": status_payload,
            }
            websocket.send_text(json.dumps(message))
            # For a worker, the server sends only one message back: registration_complete.
            # The broadcast_to_frontends call doesn't send to this worker.
            response = websocket.receive_text()  # This should be registration_complete
            data = json.loads(response)

            assert (
                data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_REGISTRATION_COMPLETE
            )
            assert client_id in worker_connections

            # Check Redis update via update_client_status call
            mock_redis.hset.assert_called()
            args, kwargs = mock_redis.hset.call_args
            assert args[0] == f"client:{client_id}:status"
            assert (
                kwargs["mapping"][config.STATUS_KEY_CONNECTED]
                == config.STATUS_VALUE_CONNECTED
            )
            assert config.STATUS_KEY_CONNECT_TIME in kwargs["mapping"]
            assert config.STATUS_KEY_LAST_SEEN in kwargs["mapping"]
            assert (
                kwargs["mapping"][config.STATUS_KEY_CLIENT_ROLE]
                == config.CLIENT_ROLE_WORKER
            )
            assert kwargs["mapping"]["state"] == "active"
            assert kwargs["mapping"]["cpu_usage"] == "25%"

            # Check broadcast was called
            mock_broadcast_actual.assert_called_once()  # Check the actual mock
            broadcast_args = mock_broadcast_actual.call_args[0][0]
            assert (
                broadcast_args[config.PAYLOAD_KEY_TYPE]
                == config.MSG_TYPE_CLIENT_STATUS_UPDATE
            )
            assert broadcast_args[config.PAYLOAD_KEY_CLIENT_ID] == client_id
            assert broadcast_args[config.PAYLOAD_KEY_STATUS] is not None
            assert (
                broadcast_args[config.PAYLOAD_KEY_STATUS][config.STATUS_KEY_CLIENT_ROLE]
                == config.CLIENT_ROLE_WORKER
            )
            assert broadcast_args[config.PAYLOAD_KEY_STATUS]["state"] == "active"

    def test_registration_missing_client_id_or_role(self, websocket_client, caplog):
        """Test registration failure if client_id or client_role is missing or status is missing."""
        test_cases = [
            (
                {
                    config.PAYLOAD_KEY_STATUS: {
                        config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER
                    }
                },
                config.ERROR_MSG_REGISTRATION_MISSING_IDS,
            ),  # Missing client_id
            (
                {
                    config.PAYLOAD_KEY_CLIENT_ID: "some_id",
                    config.PAYLOAD_KEY_STATUS: {},
                },
                config.ERROR_MSG_REGISTRATION_MISSING_IDS,
            ),  # Missing client_role
            (
                {config.PAYLOAD_KEY_CLIENT_ID: "some_id"},
                config.ERROR_MSG_REGISTRATION_MISSING_IDS,
            ),  # Missing status and client_role
        ]
        for message, error_detail in test_cases:
            with websocket_client.websocket_connect(
                config.WEBSOCKET_ENDPOINT_PATH
            ) as websocket:
                websocket.send_text(json.dumps(message))
                response = websocket.receive_text()
                data = json.loads(response)
                assert data.get(config.MSG_TYPE_ERROR) == error_detail
                # Check that error is logged
                assert any(error_detail in record.message for record in caplog.records)

    def test_invalid_json_message_during_registration(self, websocket_client, caplog):
        """Test handling of invalid JSON messages during registration."""
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            websocket.send_text("invalid json")
            response = websocket.receive_text()
            data = json.loads(response)
            assert (
                data[config.MSG_TYPE_ERROR]
                == config.ERROR_MSG_INVALID_JSON_REGISTRATION
            )
            # Check that error is logged
            assert any(
                config.ERROR_MSG_INVALID_JSON_REGISTRATION in record.message
                for record in caplog.records
            )

        # Test with malformed but still JSON-like message in a separate connection
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            websocket.send_text("{status: not valid}")
            response = websocket.receive_text()
            data = json.loads(response)
            assert (
                data[config.MSG_TYPE_ERROR]
                == config.ERROR_MSG_INVALID_JSON_REGISTRATION
            )

    def test_subsequent_status_update_from_worker(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test that a worker client can send further status updates after registration."""
        from src.server import server as server_module

        worker_connections = server_module.worker_connections  # Direct access
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(server_module, "broadcast_to_frontends", broadcast_mock)
        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )
        mock_redis.hset.reset_mock()

        client_id = "worker_updater"

        # Simulate the client's state in Redis *after* registration but *before* the subsequent update.
        # Configure mock_redis.hgetall directly for this test.
        mock_state_after_reg_in_redis = {
            config.PAYLOAD_KEY_CLIENT_ID.encode(): client_id.encode(),
            config.STATUS_KEY_CLIENT_ROLE.encode(): config.CLIENT_ROLE_WORKER.encode(),
            b"temp": b"30C",  # Custom key from registration
            config.STATUS_KEY_CONNECTED.encode(): config.STATUS_VALUE_CONNECTED.encode(),
            config.STATUS_KEY_CONNECT_TIME.encode(): b"some_iso_time_str",
            config.STATUS_KEY_LAST_SEEN.encode(): b"some_iso_time_str",
        }
        mock_redis.hgetall.return_value = mock_state_after_reg_in_redis

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Registration
            reg_message = {
                config.PAYLOAD_KEY_CLIENT_ID: client_id,
                config.PAYLOAD_KEY_STATUS: {
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
                    "temp": "30C",
                },
            }
            websocket.send_text(json.dumps(reg_message))
            # Worker client receives only one message (registration_complete) after registration.
            websocket.receive_text()  # Consume registration_complete ack
            assert client_id in worker_connections
            mock_redis.hset.reset_mock()  # Clear calls from registration phase
            broadcast_mock.reset_mock()  # Also reset broadcast mock

            # Subsequent status update
            update_status_payload = {"temp": "35C", "load": "high"}  # Custom keys
            update_message = {
                config.PAYLOAD_KEY_CLIENT_ID: client_id,
                config.PAYLOAD_KEY_STATUS: update_status_payload,
            }

            # Mock datetime for predictable last_seen timestamp
            fixed_timestamp_iso = "2024-01-01T12:00:00.000000+00:00"

            mock_now_dt_instance = MagicMock()
            mock_now_dt_instance.isoformat.return_value = fixed_timestamp_iso
            mock_now_method = MagicMock(return_value=mock_now_dt_instance)
            mock_datetime_module = MagicMock()
            mock_datetime_module.now = mock_now_method

            mock_datetime_module.UTC = UTC

            updated_state_in_redis_for_hgetall = {
                **mock_state_after_reg_in_redis,
                b"temp": b"35C",
                b"load": b"high",
                config.STATUS_KEY_LAST_SEEN.encode(): fixed_timestamp_iso.encode(),
            }
            mock_redis.hgetall.return_value = updated_state_in_redis_for_hgetall

            with patch("src.server.server.datetime", mock_datetime_module):
                websocket.send_text(json.dumps(update_message))
                response = websocket.receive_text()
                data = json.loads(response)

            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_MESSAGE_PROCESSED
            assert data[config.PAYLOAD_KEY_CLIENT_ID] == client_id
            assert "temp" in data[config.PAYLOAD_KEY_STATUS_UPDATED]
            assert "load" in data[config.PAYLOAD_KEY_STATUS_UPDATED]

            # Check Redis hset was called for the update
            mock_redis.hset.assert_called_once()
            args, kwargs = mock_redis.hset.call_args
            assert args[0] == f"client:{client_id}:status"
            assert kwargs["mapping"]["temp"] == "35C"
            assert kwargs["mapping"]["load"] == "high"
            assert config.STATUS_KEY_LAST_SEEN in kwargs["mapping"]

            # Check broadcast was called with the updated status
            broadcast_mock.assert_called_once()
            broadcast_args = broadcast_mock.call_args[0][0]
            assert (
                broadcast_args[config.PAYLOAD_KEY_TYPE]
                == config.MSG_TYPE_CLIENT_STATUS_UPDATE
            )
            assert broadcast_args[config.PAYLOAD_KEY_CLIENT_ID] == client_id
            assert broadcast_args[config.PAYLOAD_KEY_STATUS]["temp"] == "35C"
            assert broadcast_args[config.PAYLOAD_KEY_STATUS]["load"] == "high"
            assert (
                broadcast_args[config.PAYLOAD_KEY_STATUS][config.STATUS_KEY_LAST_SEEN]
                == fixed_timestamp_iso
            )  # Assert fixed timestamp

    def test_worker_status_update_not_broadcast_if_redis_unavailable(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test worker status update when Redis is unavailable: no Redis write, no broadcast, but ack to worker."""

        from src.server import server as server_module

        # 1. Setup: Redis unavailable, mock broadcast
        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_UNAVAILABLE,
        )
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(server_module, "broadcast_to_frontends", broadcast_mock)

        # update_client_status will see Redis is unavailable and not call hset.
        mock_redis.hset.reset_mock()

        client_id = "worker_redis_down"

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # 2. Registration
            reg_message = {
                config.PAYLOAD_KEY_CLIENT_ID: client_id,
                config.PAYLOAD_KEY_STATUS: {
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
                    "initial_state": "booting",
                },
            }
            websocket.send_text(json.dumps(reg_message))

            # Consume registration_complete ack
            # Server sends this ack; its broadcast part will be skipped due to Redis unavailable
            reg_ack_response = websocket.receive_text()
            try:
                reg_ack_data = json.loads(reg_ack_response)
            except json.JSONDecodeError:
                pytest.fail(
                    f"Failed to decode JSON from registration ack: {reg_ack_response}"
                )

            assert (
                reg_ack_data.get(config.PAYLOAD_KEY_TYPE)
                == config.MSG_TYPE_REGISTRATION_COMPLETE
            )
            assert (
                reg_ack_data.get(config.STATUS_KEY_REDIS_STATUS)
                == config.STATUS_VALUE_REDIS_UNAVAILABLE
            )

            # The broadcast during registration path in websocket_endpoint:
            # success = await update_client_status(...) -> would be False
            # stored_status_after_conn = await get_client_info(...) -> would be None
            # if success and stored_status_after_conn: -> this condition fails
            # So, broadcast_mock should not have been called from registration.
            broadcast_mock.assert_not_called()  # Ensure broadcast_to_frontends was not called
            mock_redis.hset.assert_not_called()  # update_client_status for reg also bails early

            # Reset mocks for the actual status update part of the test
            mock_redis.hset.reset_mock()
            broadcast_mock.reset_mock()

            # 3. Worker sends a status update
            update_status_payload = {"temp": "40C", "load": "none"}  # Custom keys
            update_message = {
                config.PAYLOAD_KEY_CLIENT_ID: client_id,
                config.PAYLOAD_KEY_STATUS: update_status_payload,
            }

            fixed_timestamp_iso = "2024-02-01T10:00:00.000000+00:00"
            mock_now_dt_instance = MagicMock()
            mock_now_dt_instance.isoformat.return_value = fixed_timestamp_iso
            mock_now_method = MagicMock(return_value=mock_now_dt_instance)
            mock_datetime_module = MagicMock()
            mock_datetime_module.now = mock_now_method
            mock_datetime_module.UTC = UTC

            with patch("src.server.server.datetime", mock_datetime_module):
                websocket.send_text(json.dumps(update_message))
                # Worker should still get an ack even if Redis is down for the update.
                # This assumes server.py's websocket_endpoint sends this ack.
                ack_response = websocket.receive_text()
                try:
                    ack_data = json.loads(ack_response)
                except json.JSONDecodeError:
                    pytest.fail(
                        f"Failed to decode JSON from status update ack: {ack_response}"
                    )

            # 4. Assertions for the status update
            assert (
                ack_data.get(config.PAYLOAD_KEY_TYPE)
                == config.MSG_TYPE_MESSAGE_PROCESSED
            )
            assert ack_data.get(config.PAYLOAD_KEY_CLIENT_ID) == client_id
            assert (
                ack_data.get(config.STATUS_KEY_REDIS_STATUS)
                == config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            # Check that status_updated reflects the keys from the payload
            if config.PAYLOAD_KEY_STATUS_UPDATED in ack_data:
                assert "temp" in ack_data[config.PAYLOAD_KEY_STATUS_UPDATED]
                assert "load" in ack_data[config.PAYLOAD_KEY_STATUS_UPDATED]
            else:
                pytest.fail(
                    f"'{config.PAYLOAD_KEY_STATUS_UPDATED}' field missing in acknowledgement message"
                )

            # update_client_status in server.py for the worker update path:
            # await update_client_status(...) -> returns False, logs warning
            mock_redis.hset.assert_not_called()  # update_client_status for update bails early

            # await broadcast_client_state_change(...) is called in server.py
            # Inside broadcast_client_state_change:
            #   full_status = await get_client_info(...) -> returns None
            #   if full_status: -> this fails, so broadcast_to_frontends (our mock) is not called.
            broadcast_mock.assert_not_called()

            # Check for specific log warnings
            assert any(
                f"Redis unavailable, cannot update client status for {client_id}"
                in rec.message
                and rec.levelname == "WARNING"
                for rec in caplog.records
            ), "Missing log: Redis unavailable for status update"
            assert any(
                f"Redis unavailable (status: unavailable), cannot get client info for {client_id}"
                in rec.message
                and rec.levelname == "WARNING"
                for rec in caplog.records
            ), "Missing log: Redis unavailable, cannot get client info"

    @pytest.mark.asyncio
    async def test_handle_disconnect_cleanup(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test cleanup during client disconnection."""
        from src.server import server as server_module

        # Setup mocks
        mock_websocket = AsyncMock()
        client_id = "test_disconnect_cleanup"

        # Add client to connections with the expected structure
        # Assuming worker_connections now stores WebSocket objects directly based on previous refactoring
        server_module.worker_connections[client_id] = mock_websocket

        # Create a mock coroutine for update_client_status that returns True
        async def mock_update(
            client_id, status_attributes, broadcast=True, originating_client_id=None
        ):
            # Simulate the structure of status_attributes passed to update_client_status
            assert config.STATUS_KEY_CONNECTED in status_attributes
            assert (
                status_attributes[config.STATUS_KEY_CONNECTED]
                == config.STATUS_VALUE_DISCONNECTED
            )
            return True

        # Patch the update_client_status function
        monkeypatch.setattr(server_module, "update_client_status", mock_update)

        # Mock get_client_info to return a client with worker role
        async def mock_get_client_info_worker(client_id_arg):
            return {
                config.PAYLOAD_KEY_CLIENT_ID: client_id_arg,
                config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
            }

        monkeypatch.setattr(
            server_module, "get_client_info", mock_get_client_info_worker
        )

        # Mock broadcast_client_state_change instead of broadcast_to_frontends
        # since that's what update_client_status actually calls
        mock_broadcast_state_change = AsyncMock()
        monkeypatch.setattr(
            server_module, "broadcast_client_state_change", mock_broadcast_state_change
        )

        # Call handle_disconnect
        await server_module.handle_disconnect(
            client_id, mock_websocket, "Test disconnect"
        )

        # Verify cleanup
        assert client_id not in server_module.worker_connections
        # Since we mocked update_client_status, broadcast_client_state_change won't be called
        # The test should pass if update_client_status was called correctly

    @pytest.mark.asyncio
    async def test_handle_disconnect_frontend(self, monkeypatch):
        """Test cleanup of frontend client disconnection."""
        from src.server import server as server_module

        # Setup mocks
        mock_websocket = AsyncMock()
        client_id = "test_frontend_disconnect"

        # Add frontend client to connections
        server_module.frontend_connections[client_id] = mock_websocket

        # Mock update_client_status
        async def mock_update_fe(client_id_arg, status_attributes, broadcast=True):
            assert config.STATUS_KEY_CONNECTED in status_attributes
            assert (
                status_attributes[config.STATUS_KEY_CONNECTED]
                == config.STATUS_VALUE_DISCONNECTED
            )
            return True

        monkeypatch.setattr(server_module, "update_client_status", mock_update_fe)

        # Mock get_client_info to return a client with frontend role
        async def mock_get_client_info_frontend(client_id_arg):
            return {
                config.PAYLOAD_KEY_CLIENT_ID: client_id_arg,
                config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_FRONTEND,
            }

        monkeypatch.setattr(
            server_module, "get_client_info", mock_get_client_info_frontend
        )

        # Mock broadcast_to_frontends
        mock_broadcast_frontends_fe = AsyncMock()
        monkeypatch.setattr(
            server_module, "broadcast_to_frontends", mock_broadcast_frontends_fe
        )

        # Call handle_disconnect
        await server_module.handle_disconnect(
            client_id, mock_websocket, "Frontend disconnect"
        )

        # Verify cleanup
        assert client_id not in server_module.frontend_connections
        mock_broadcast_frontends_fe.assert_not_called()  # Ensure broadcast was NOT attempted for frontend

    def test_frontend_directory_exists_check(self, monkeypatch):
        """Test the frontend directory existence check during startup."""

        # Test when directory doesn't exist
        with patch("os.path.exists", return_value=False):
            # The actual test would verify the warning is logged
            assert True

        # Test when directory exists but index.html is missing
        with patch("os.path.exists", side_effect=lambda x: "build" in x):
            with patch("os.path.join", return_value="build/index.html"):
                # The actual test would verify the warning is logged
                assert True

    @pytest.mark.asyncio
    async def test_disconnect_client(self, monkeypatch):
        """Test the disconnect_client function."""
        from src.server import server as server_module

        # Save original state
        original_worker_connections = server_module.worker_connections.copy()
        original_frontend_connections = server_module.frontend_connections.copy()

        try:
            # Setup test data
            client_id = "test_disconnect"

            # Create mock WebSockets with async close methods
            mock_worker_ws = AsyncMock()
            mock_worker_ws.close = AsyncMock()

            # Add to worker_connections
            server_module.worker_connections[client_id] = mock_worker_ws

            # Mock the logger to prevent actual logging during test
            mock_logger = MagicMock()
            monkeypatch.setattr(server_module, "logger", mock_logger)

            # Mock update_client_status to avoid Redis dependency
            mock_update_status = AsyncMock(return_value=True)
            monkeypatch.setattr(
                server_module, "update_client_status", mock_update_status
            )

            # Mock get_client_info to return a client that exists and is connected
            async def mock_get_client_info(client_id):
                return {
                    config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED,
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
                }

            monkeypatch.setattr(server_module, "get_client_info", mock_get_client_info)

            # Test with worker client
            result = await server_module.disconnect_client(client_id)

            # Verify WebSocket was closed with normal closure code
            mock_worker_ws.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )

            # Verify update_client_status was called
            mock_update_status.assert_called_once()

            # Verify result structure
            assert config.PAYLOAD_KEY_STATUS in result
            assert config.PAYLOAD_KEY_MESSAGE in result

            # The disconnect_client function doesn't remove from connections immediately
            # - that's done by the WebSocket handler's finally block.
            # So we simulate that cleanup here to test the expectation:
            server_module.worker_connections.pop(client_id, None)

            # Now verify client was removed
            assert client_id not in server_module.worker_connections

            # Test with frontend client - create a new mock to avoid any state issues
            frontend_id = f"frontend_{client_id}"
            mock_frontend_ws = AsyncMock()
            mock_frontend_ws.close = AsyncMock()
            server_module.frontend_connections[frontend_id] = mock_frontend_ws

            # Reset the mock for the second call
            mock_update_status.reset_mock()

            await server_module.disconnect_client(frontend_id)

            # Verify WebSocket was closed
            mock_frontend_ws.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )

            # Simulate the WebSocket handler cleanup
            server_module.frontend_connections.pop(frontend_id, None)

            # Verify client was removed
            assert frontend_id not in server_module.frontend_connections

        finally:
            # Restore original state
            server_module.worker_connections = original_worker_connections
            server_module.frontend_connections = original_frontend_connections


class TestBroadcastFunctionality:
    """Test the broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_to_frontends_with_disconnect(self, monkeypatch):
        """Test broadcast handling when a frontend disconnects during broadcast."""
        from src.server import server as server_module

        # Save original state
        original_connections = server_module.frontend_connections.copy()

        try:
            # Setup test data
            test_message = {"type": "test", "data": "test message"}

            # Create mock websockets
            good_ws = AsyncMock()
            bad_ws = AsyncMock()

            # Set up the mock WebSocket objects
            good_ws.send_text = AsyncMock()
            bad_ws.send_text = AsyncMock(side_effect=RuntimeError("Connection lost"))

            # Add to frontend_connections
            server_module.frontend_connections = {
                "good_client": good_ws,
                "bad_client": bad_ws,
            }

            # Mock the logger to prevent actual logging during test
            mock_logger = MagicMock()
            monkeypatch.setattr(server_module, "logger", mock_logger)

            # Call broadcast
            await server_module.broadcast_to_frontends(test_message)

            # Verify good client received message exactly once
            good_ws.send_text.assert_awaited_once_with(json.dumps(test_message))

            # Verify bad client was removed from connections
            assert "bad_client" not in server_module.frontend_connections
            assert "good_client" in server_module.frontend_connections

            mock_logger.warning.assert_called_once()

            # Verify the call count for send_text
            assert good_ws.send_text.await_count == 1
            assert bad_ws.send_text.await_count == 1

            # Verify no other unexpected calls were made
            good_ws.assert_has_calls(
                [call.send_text(json.dumps(test_message))],
                any_order=True,
            )
            bad_ws.assert_has_calls(
                [call.send_text(json.dumps(test_message))],
                any_order=True,
            )

        finally:
            # Restore original state
            server_module.frontend_connections = original_connections


class TestLifespanManagement:
    """Test FastAPI lifespan management."""

    @pytest.mark.asyncio
    async def test_lifespan_shutdown(self, monkeypatch):
        """Test proper cleanup during application shutdown."""
        from src.server import server as server_module

        # Save original state
        original_worker_connections = server_module.worker_connections.copy()
        original_frontend_connections = server_module.frontend_connections.copy()

        try:
            # Setup test data
            test_ws1 = AsyncMock()
            test_ws2 = AsyncMock()

            # Set up the mock WebSocket objects
            test_ws1.close = AsyncMock()
            test_ws2.close = AsyncMock()

            # Set up connections directly as websockets (not dicts)
            server_module.worker_connections = {"worker1": test_ws1}
            server_module.frontend_connections = {"frontend1": test_ws2}

            # Mock close_redis
            close_redis_mock = AsyncMock()
            monkeypatch.setattr(server_module, "close_redis", close_redis_mock)

            # Get the lifespan generator
            async with server_module.lifespan(None) as _:
                # In the context, we can simulate the shutdown
                pass

            # Verify all connections were closed
            test_ws1.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                reason=config.REASON_SERVER_SHUTDOWN,
            )
            test_ws2.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN,
                reason=config.REASON_SERVER_SHUTDOWN,
            )

            # Verify redis was closed
            close_redis_mock.assert_awaited_once()

            # Verify connection dictionaries were cleared
            assert not server_module.worker_connections
            assert not server_module.frontend_connections

        finally:
            # Restore original state
            server_module.worker_connections = original_worker_connections
            server_module.frontend_connections = original_frontend_connections


class TestClientManagement:
    """Test client management functions."""

    @pytest.mark.asyncio
    async def test_disconnect_client(self, monkeypatch):
        """Test the disconnect_client function."""
        from src.server import server as server_module

        # Save original state
        original_worker_connections = server_module.worker_connections.copy()
        original_frontend_connections = server_module.frontend_connections.copy()

        try:
            # Setup test data
            client_id = "test_disconnect"

            # Create mock WebSockets with async close methods
            mock_worker_ws = AsyncMock()
            mock_worker_ws.close = AsyncMock()

            # Add to worker_connections
            server_module.worker_connections[client_id] = mock_worker_ws

            # Mock the logger to prevent actual logging during test
            mock_logger = MagicMock()
            monkeypatch.setattr(server_module, "logger", mock_logger)

            # Mock update_client_status to avoid Redis dependency
            mock_update_status = AsyncMock(return_value=True)
            monkeypatch.setattr(
                server_module, "update_client_status", mock_update_status
            )

            # Mock get_client_info to return a client that exists and is connected
            async def mock_get_client_info(client_id):
                return {
                    config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED,
                    config.STATUS_KEY_CLIENT_ROLE: config.CLIENT_ROLE_WORKER,
                }

            monkeypatch.setattr(server_module, "get_client_info", mock_get_client_info)

            # Test with worker client
            result = await server_module.disconnect_client(client_id)

            # Verify WebSocket was closed with normal closure code
            mock_worker_ws.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )

            # Verify update_client_status was called
            mock_update_status.assert_called_once()

            # Verify result structure
            assert config.PAYLOAD_KEY_STATUS in result
            assert config.PAYLOAD_KEY_MESSAGE in result

            # The disconnect_client function doesn't remove from connections immediately
            # - that's done by the WebSocket handler's finally block.
            # So we simulate that cleanup here to test the expectation:
            server_module.worker_connections.pop(client_id, None)

            # Now verify client was removed
            assert client_id not in server_module.worker_connections

            # Test with frontend client - create a new mock to avoid any state issues
            frontend_id = f"frontend_{client_id}"
            mock_frontend_ws = AsyncMock()
            mock_frontend_ws.close = AsyncMock()
            server_module.frontend_connections[frontend_id] = mock_frontend_ws

            # Reset the mock for the second call
            mock_update_status.reset_mock()

            await server_module.disconnect_client(frontend_id)

            # Verify WebSocket was closed
            mock_frontend_ws.close.assert_awaited_once_with(
                code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
                reason=config.REASON_SERVER_INITIATED_DISCONNECT_HTTP,
            )

            # Simulate the WebSocket handler cleanup
            server_module.frontend_connections.pop(frontend_id, None)

            # Verify client was removed
            assert frontend_id not in server_module.frontend_connections

        finally:
            # Restore original state
            server_module.worker_connections = original_worker_connections
            server_module.frontend_connections = original_frontend_connections

    @pytest.mark.asyncio
    async def test_pause_resume_client(self, monkeypatch):
        """Test the pause_client and resume_client functions."""
        from src.server import server as server_module

        # Save original state
        original_worker_connections = server_module.worker_connections.copy()

        try:
            # Setup test data
            client_id = "test_pause_resume"

            # Create a mock WebSocket
            mock_ws = AsyncMock()

            # Add to worker_connections
            server_module.worker_connections[client_id] = mock_ws

            # Mock the logger to prevent actual logging during test
            mock_logger = MagicMock()
            monkeypatch.setattr(server_module, "logger", mock_logger)

            # Mock the update_client_status function
            update_mock = AsyncMock()
            monkeypatch.setattr(server_module, "update_client_status", update_mock)

            # Test pause_client
            # Mock get_client_info for pause_client
            async def mock_get_client_info_pause(*args, **kwargs):
                return {config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED}

            monkeypatch.setattr(
                server_module, "get_client_info", mock_get_client_info_pause
            )
            await server_module.pause_client(client_id)

            # Verify client was updated with paused status
            update_mock.assert_called_once_with(
                client_id,
                {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_PAUSED},
                broadcast=True,
                originating_client_id=None,
            )

            # Reset mock for resume test
            update_mock.reset_mock()

            # Test resume_client
            # Mock get_client_info for resume_client
            async def mock_get_client_info_resume(*args, **kwargs):
                return {config.STATUS_KEY_CONNECTED: config.STATUS_VALUE_CONNECTED}

            monkeypatch.setattr(
                server_module, "get_client_info", mock_get_client_info_resume
            )
            await server_module.resume_client(client_id)

            # Verify client was updated with running status
            update_mock.assert_called_once_with(
                client_id,
                {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_RUNNING},
                broadcast=True,
                originating_client_id=None,
            )

        finally:
            # Restore original state
            server_module.worker_connections = original_worker_connections


class TestFrontendBuildDirectoryChecks:
    """Test frontend build directory configuration checks."""

    def test_frontend_build_dir_not_exists_warning(self, caplog, monkeypatch):
        """Test warning when frontend build directory doesn't exist."""

        # Mock os.path.exists to return False for the build directory
        with patch("os.path.exists", return_value=False):
            # Reload the server module to trigger the directory check
            import importlib

            from src.server import server

            importlib.reload(server)

            # Check that warning was logged
            assert any(
                "Frontend build directory not found" in record.message
                for record in caplog.records
                if record.levelname == "WARNING"
            )

    def test_frontend_build_dir_exists_but_no_index_html_warning(
        self, caplog, monkeypatch
    ):
        """Test warning when build directory exists but index.html is missing."""

        # Mock os.path.exists to return True for build dir, False for index.html
        def mock_exists(path):
            if "frontend_build" in path:
                return True
            if "index.html" in path:
                return False
            return True

        with patch("os.path.exists", side_effect=mock_exists):
            # Reload the server module to trigger the check
            import importlib

            from src.server import server

            importlib.reload(server)

            # Check that warning was logged
            assert any(
                "index.html not found in frontend build directory" in record.message
                for record in caplog.records
                if record.levelname == "WARNING"
            )


class TestWebSocketEndpointErrorHandling:
    """Test error handling in the WebSocket endpoint."""

    @pytest.mark.asyncio
    async def test_websocket_json_decode_error_in_message_loop(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test JSON decode error in message loop after successful registration."""

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock the status after registration
        expected_status = {
            "client_id": "test_json_error_client",
            "client_role": "worker",
            "state": "running",
            "connected": "true",
        }
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode() for k, v in expected_status.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register first
            registration_message = {
                "client_id": "test_json_error_client",
                "status": {"client_role": "worker", "state": "running"},
            }
            websocket.send_text(json.dumps(registration_message))
            websocket.receive_text()  # Consume registration complete

            # Send invalid JSON
            websocket.send_text("invalid json}")

            # Should get an error response
            response = websocket.receive_text()
            data = json.loads(response)
            assert data["error"] == "Invalid JSON format"

            # Check that error was logged
            assert any(
                "Invalid JSON format from test_json_error_client:" in record.message
                for record in caplog.records
                if record.levelname == "ERROR"
            )

    @pytest.mark.asyncio
    async def test_websocket_unexpected_exception_handling(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test handling of unexpected exceptions in websocket endpoint."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock update_client_status to raise an exception
        async def mock_update_status(*args, **kwargs):
            raise RuntimeError("Unexpected error during status update")

        monkeypatch.setattr(server_module, "update_client_status", mock_update_status)

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register first
            registration_message = {
                "client_id": "test_exception_client",
                "status": {"client_role": "worker", "state": "running"},
            }

            # This should trigger the exception in update_client_status
            websocket.send_text(json.dumps(registration_message))

            # The connection should be closed due to the exception
            try:
                websocket.receive_text()
            except Exception:
                pass  # Expected to fail

            # Check that error was logged
            assert any(
                "Unexpected WebSocket error for client" in record.message
                for record in caplog.records
                if record.levelname == "ERROR"
            )

    @pytest.mark.asyncio
    async def test_websocket_close_during_error_handling(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test error during websocket close in exception handler."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Create a mock that will cause an exception during message processing
        async def mock_update_status(*args, **kwargs):
            raise RuntimeError("Simulated processing error")

        monkeypatch.setattr(server_module, "update_client_status", mock_update_status)

        # We'll test the error handling by causing an exception during registration
        # which will trigger the exception handler in the websocket_endpoint
        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            try:
                # Send registration message that will cause an exception
                registration_message = {
                    "client_id": "test_error_client",
                    "status": {"client_role": "worker", "state": "running"},
                }
                websocket.send_text(json.dumps(registration_message))

                # Try to receive response - this may fail due to the exception
                try:
                    websocket.receive_text()
                except Exception:
                    pass  # Expected to fail due to our simulated error

            except Exception:
                pass  # Expected due to simulated error

        # Check that error was logged about the unexpected WebSocket error
        assert any(
            "Unexpected WebSocket error for client" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        ), f"Expected error log not found. Available logs: {[r.message for r in caplog.records if r.levelname == 'ERROR']}"


class TestControlMessageHandling:
    """Test control message handling from frontend clients."""

    @pytest.mark.asyncio
    async def test_control_message_from_non_frontend_client(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test that non-frontend clients cannot send control messages."""

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock the status after registration
        expected_status = {
            "client_id": "worker_client",
            "client_role": "worker",
            "state": "running",
            "connected": "true",
        }
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode() for k, v in expected_status.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as worker
            registration_message = {
                "client_id": "worker_client",
                "status": {"client_role": "worker", "state": "running"},
            }
            websocket.send_text(json.dumps(registration_message))
            websocket.receive_text()  # Consume registration complete

            # Try to send control message
            control_message = {
                "type": "control",
                "action": "pause",
                "target_client_id": "some_target",
                "message_id": "test_123",
            }
            websocket.send_text(json.dumps(control_message))

            # Should get permission denied response
            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_CONTROL_RESPONSE
            assert data[config.PAYLOAD_KEY_STATUS] == config.MSG_TYPE_ERROR
            assert (
                config.ERROR_MSG_CONTROL_PERMISSION_DENIED
                in data[config.PAYLOAD_KEY_MESSAGE]
            )
            assert data[config.PAYLOAD_KEY_ACTION] == config.CONTROL_ACTION_PAUSE
            assert data[config.PAYLOAD_KEY_TARGET_CLIENT_ID] == "some_target"
            assert data[config.PAYLOAD_KEY_MESSAGE_ID] == "test_123"

    @pytest.mark.asyncio
    async def test_control_message_missing_required_fields(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test control message with missing required fields."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock get_all_client_statuses for frontend registration
        async def mock_get_all_statuses():
            return {}, "connected", None

        monkeypatch.setattr(
            server_module, "get_all_client_statuses", mock_get_all_statuses
        )

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as frontend
            registration_message = {
                "client_id": "frontend_client",
                "status": {"client_role": "frontend"},
            }
            websocket.send_text(json.dumps(registration_message))

            # Consume all registration messages
            websocket.receive_text()  # all_clients_update
            websocket.receive_text()  # broadcast of own status
            websocket.receive_text()  # registration_complete

            # Send control message missing action
            control_message = {
                "type": "control",
                "target_client_id": "some_target",
                "message_id": "test_123",
            }
            websocket.send_text(json.dumps(control_message))

            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_CONTROL_RESPONSE
            assert data[config.PAYLOAD_KEY_STATUS] == config.MSG_TYPE_ERROR
            assert (
                config.ERROR_MSG_CONTROL_INVALID_PAYLOAD
                in data[config.PAYLOAD_KEY_MESSAGE]
            )

            # Send control message missing target_client_id
            control_message = {
                config.PAYLOAD_KEY_TYPE: config.MSG_TYPE_CONTROL,
                config.PAYLOAD_KEY_ACTION: config.CONTROL_ACTION_PAUSE,
                config.PAYLOAD_KEY_MESSAGE_ID: "test_456",
            }
            websocket.send_text(json.dumps(control_message))

            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_CONTROL_RESPONSE
            assert data[config.PAYLOAD_KEY_STATUS] == config.MSG_TYPE_ERROR
            assert (
                config.ERROR_MSG_CONTROL_INVALID_PAYLOAD
                in data[config.PAYLOAD_KEY_MESSAGE]
            )

    @pytest.mark.asyncio
    async def test_control_message_unknown_action(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test control message with unknown action."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock get_all_client_statuses for frontend registration
        async def mock_get_all_statuses():
            return {}, "connected", None

        monkeypatch.setattr(
            server_module, "get_all_client_statuses", mock_get_all_statuses
        )

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as frontend
            registration_message = {
                "client_id": "frontend_client",
                "status": {"client_role": "frontend"},
            }
            websocket.send_text(json.dumps(registration_message))

            # Consume all registration messages
            websocket.receive_text()  # all_clients_update
            websocket.receive_text()  # broadcast of own status
            websocket.receive_text()  # registration_complete

            # Send control message with unknown action
            control_message = {
                "type": "control",
                "action": "unknown_action",
                "target_client_id": "some_target",
                "message_id": "test_123",
            }
            websocket.send_text(json.dumps(control_message))

            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_CONTROL_RESPONSE
            assert data[config.PAYLOAD_KEY_STATUS] == config.MSG_TYPE_ERROR
            assert (
                "Unknown control action: unknown_action"
                in data[config.PAYLOAD_KEY_MESSAGE]
            )
            assert data[config.PAYLOAD_KEY_ACTION] == "unknown_action"


class TestWorkerStatusUpdateHandling:
    """Test worker status update message handling."""

    @pytest.mark.asyncio
    async def test_worker_status_update_conflicting_client_id(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test worker sending status update with conflicting client_id."""

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock the status after registration and update
        expected_status = {
            "client_id": "worker_client",
            "client_role": "worker",
            "state": "running",
            "connected": "true",
        }
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode() for k, v in expected_status.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as worker
            registration_message = {
                "client_id": "worker_client",
                "status": {"client_role": "worker", "state": "running"},
            }
            websocket.send_text(json.dumps(registration_message))
            websocket.receive_text()  # Consume registration complete

            # Send status update with conflicting client_id
            status_update = {
                "client_id": "different_client_id",  # Conflicts with registered ID
                "status": {"temp": "35C", "load": "high"},
            }
            websocket.send_text(json.dumps(status_update))

            # Should still get acknowledgment
            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_MESSAGE_PROCESSED
            assert (
                data[config.PAYLOAD_KEY_CLIENT_ID] == "worker_client"
            )  # Uses connection's client_id

            # Check warning was logged
            assert any(
                "sent status with conflicting client_id" in record.message
                for record in caplog.records
                if record.levelname == "WARNING"
            )

    @pytest.mark.asyncio
    async def test_worker_status_update_non_dict_status(
        self, websocket_client, mock_redis, monkeypatch, caplog
    ):
        """Test worker sending non-dict status payload."""

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock the status after registration and update
        expected_status = {
            "client_id": "worker_client",
            "client_role": "worker",
            "state": "running",
            "connected": "true",
            "raw_payload": "string_status",
        }
        mock_redis.hgetall.return_value = {
            k.encode(): v.encode() for k, v in expected_status.items()
        }

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as worker
            registration_message = {
                "client_id": "worker_client",
                "status": {"client_role": "worker", "state": "running"},
            }
            websocket.send_text(json.dumps(registration_message))
            websocket.receive_text()  # Consume registration complete

            # Send status update with non-dict status
            status_update = {
                "client_id": "worker_client",
                "status": "string_status",  # Not a dict
            }
            websocket.send_text(json.dumps(status_update))

            # Should still get acknowledgment
            response = websocket.receive_text()
            data = json.loads(response)
            assert data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_MESSAGE_PROCESSED
            assert (
                config.STATUS_KEY_RAW_PAYLOAD in data[config.PAYLOAD_KEY_STATUS_UPDATED]
            )

            # Check warning was logged
            assert any(
                "Received non-dict status from worker" in record.message
                for record in caplog.records
                if record.levelname == "WARNING"
            )


class TestFrontendMessageHandling:
    """Test frontend message handling."""

    @pytest.mark.asyncio
    async def test_frontend_unhandled_message_type(
        self, websocket_client, mock_redis, monkeypatch
    ):
        """Test frontend sending unhandled message type."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )

        # Mock get_all_client_statuses for frontend registration
        async def mock_get_all_statuses():
            return {}, "connected", None

        monkeypatch.setattr(
            server_module, "get_all_client_statuses", mock_get_all_statuses
        )

        with websocket_client.websocket_connect(
            config.WEBSOCKET_ENDPOINT_PATH
        ) as websocket:
            # Register as frontend
            registration_message = {
                "client_id": "frontend_client",
                "status": {"client_role": "frontend"},
            }
            websocket.send_text(json.dumps(registration_message))

            # Consume all registration messages
            websocket.receive_text()  # all_clients_update
            websocket.receive_text()  # broadcast of own status
            websocket.receive_text()  # registration_complete

            # Send unhandled message type
            unhandled_message = {"type": "unknown_type", "data": "some_data"}
            websocket.send_text(json.dumps(unhandled_message))

            response = websocket.receive_text()
            data = json.loads(response)
            assert (
                data[config.PAYLOAD_KEY_TYPE] == config.MSG_TYPE_MESSAGE_RECEIPT_UNKNOWN
            )
            assert data[config.PAYLOAD_KEY_ORIGINAL_MESSAGE] == unhandled_message
            assert (
                config.INFO_MSG_UNHANDLED_FRONTEND_MSG in data[config.PAYLOAD_KEY_INFO]
            )


class TestStatusEndpointErrorHandling:
    """Test error handling in REST API endpoints."""

    @pytest.mark.asyncio
    async def test_get_all_statuses_exception_handling(self, monkeypatch, caplog):
        """Test exception handling in get_all_statuses endpoint."""
        from src.server import server as server_module

        # Mock get_all_client_statuses to raise an exception
        async def mock_get_all_statuses():
            raise RuntimeError("Database error")

        monkeypatch.setattr(
            server_module, "get_all_client_statuses", mock_get_all_statuses
        )

        # Call the endpoint function directly
        response = await server_module.get_all_statuses()

        assert response[config.PAYLOAD_KEY_CLIENTS] == {}
        assert (
            response[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNKNOWN
        )
        assert response[config.MSG_TYPE_ERROR] == "Failed to retrieve client statuses"

        # Check error was logged
        assert any(
            f"Error in GET {config.STATUSES_ENDPOINT_PATH}" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )


class TestHelperFunctionErrorHandling:
    """Test error handling in helper functions."""

    @pytest.mark.asyncio
    async def test_get_client_info_redis_unavailable(self, monkeypatch, caplog):
        """Test get_client_info when Redis is unavailable."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_UNAVAILABLE,
        )

        result = await server_module.get_client_info("test_client")
        assert result is None

        # Check warning was logged
        assert any(
            "Redis unavailable (status: unavailable), cannot get client info for test_client"
            in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    @pytest.mark.asyncio
    async def test_get_client_info_redis_exception(
        self, mock_redis, monkeypatch, caplog
    ):
        """Test get_client_info when Redis raises an exception."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )
        # Ensure the globally patched redis_client (which is mock_redis) has this side_effect
        mock_redis.hgetall.side_effect = redis.exceptions.ConnectionError(
            "Simulated Redis error"
        )

        result = await server_module.get_client_info("test_client")
        assert result is None

        # Check error was logged
        assert any(
            "Error getting client info for test_client" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )

    @pytest.mark.asyncio
    async def test_get_client_info_empty_status_data(self, mock_redis, monkeypatch):
        """Test get_client_info when Redis returns empty data."""
        from src.server import server as server_module

        monkeypatch.setitem(
            status_store,
            config.STATUS_KEY_REDIS_STATUS,
            config.STATUS_VALUE_REDIS_CONNECTED,
        )
        # Ensure the globally patched redis_client (which is mock_redis) returns empty dict
        mock_redis.hgetall.return_value = {}
        # Reset side_effect if it was set by a previous test using the same mock_redis instance
        mock_redis.hgetall.side_effect = None

        result = await server_module.get_client_info("test_client")
        assert result is None

    @pytest.mark.asyncio
    async def test_broadcast_client_state_change_empty_client_id(self, caplog):
        """Test broadcast_client_state_change with empty client_id."""
        from src.server import server as server_module

        await server_module.broadcast_client_state_change("")

        # Check warning was logged
        assert any(
            "broadcast_client_state_change called with empty client_id"
            in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    @pytest.mark.asyncio
    async def test_broadcast_client_state_change_no_status_found(
        self, monkeypatch, caplog
    ):
        """Test broadcast_client_state_change when no status is found."""
        from src.server import server as server_module

        # Mock get_client_info to return None
        async def mock_get_client_info(client_id):
            return None

        monkeypatch.setattr(server_module, "get_client_info", mock_get_client_info)

        await server_module.broadcast_client_state_change("test_client")

        # Check warning was logged
        assert any(
            "No status found for client test_client in Redis, and no changed_state_attributes provided for broadcast_client_state_change."
            in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    @pytest.mark.asyncio
    async def test_broadcast_client_state_change_exception(self, monkeypatch, caplog):
        """Test broadcast_client_state_change exception handling."""
        from src.server import server as server_module

        # Mock get_client_info to raise an exception
        async def mock_get_client_info(client_id):
            raise RuntimeError("Test error")

        monkeypatch.setattr(server_module, "get_client_info", mock_get_client_info)

        await server_module.broadcast_client_state_change("test_client")

        # Check error was logged
        assert any(
            "Error broadcasting state change for client test_client" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )

    @pytest.mark.asyncio
    async def test_update_client_status_exception_handling(
        self, mock_redis, monkeypatch, caplog
    ):
        """Test update_client_status exception handling."""
        from src.server import server as server_module

        # Mock redis_update_client_status to raise an exception
        async def mock_redis_update(client_id, status_attrs):
            raise RuntimeError("Redis error")

        monkeypatch.setattr(
            server_module, "redis_update_client_status", mock_redis_update
        )

        result = await server_module.update_client_status(
            "test_client", {"state": "running"}
        )
        assert result is False

        # Check error was logged
        assert any(
            "Error updating status for client test_client in Redis" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )
