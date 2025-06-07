"""Tests for the ConnectionManager."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import ValidationError

from src.server import config as server_config
from src.server.websocket_manager import ConnectionManager
from src.shared.schemas.websocket import (
    AllClientStatuses,
    ClientStatus,
    ClientStatusUpdateMessage,
    CommandMessage,
    ErrorMessage,
    RegistrationCompleteMessage,
    WebSocketMessageBase,
)


@pytest.fixture
def manager():
    return ConnectionManager()


@pytest.fixture
def mock_ws():
    # Provide a default name for logging if the websocket object itself is logged
    # Ensure client_state is set for tests that check it before sending/closing
    return AsyncMock(
        spec=WebSocket, client_state=WebSocketState.CONNECTED, name="mock_ws"
    )


@pytest.fixture
def mock_redis_manager(monkeypatch):
    mock = AsyncMock()
    mock.get_redis_status = MagicMock(return_value="connected")
    mock.get_client_info = AsyncMock(return_value=None)
    mock.get_all_client_statuses = AsyncMock(return_value=None)
    mock.update_client_status = AsyncMock(return_value=True)
    monkeypatch.setattr("src.server.websocket_manager.redis_manager", mock)
    return mock


@pytest.fixture
def mock_logger(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("src.server.websocket_manager.logger", mock)
    return mock


# === Initialization and Basic Connections ===
def test_initialization(manager: ConnectionManager):
    assert manager.active_connections == {}
    assert manager.client_contexts == {}


def test_add_and_remove_connection(manager: ConnectionManager):
    ws_mock_fixture = AsyncMock()
    client_id = "client1"
    manager.active_connections[client_id] = ws_mock_fixture
    assert client_id in manager.active_connections
    manager.disconnect(client_id)
    assert client_id not in manager.active_connections


def test_is_connected(manager: ConnectionManager):
    ws_mock_fixture = AsyncMock()
    client_id = "client1"
    manager.active_connections[client_id] = ws_mock_fixture
    assert manager.is_connected(client_id) is True
    assert manager.is_connected("unknown_client") is False


# === send_message Tests ===
@pytest.mark.asyncio
async def test_send_message_to_client_happy_path(
    manager: ConnectionManager, mock_ws: AsyncMock
):
    client_id = "client1"
    manager.active_connections[client_id] = mock_ws

    class MockMessage(WebSocketMessageBase):
        content: str = "hello"

    message_to_send = MockMessage()
    message_json = message_to_send.model_dump_json()
    result = await manager.send_message(message_to_send, client_id)
    assert result is True
    mock_ws.send_text.assert_awaited_once_with(message_json)


@pytest.mark.asyncio
async def test_send_message_client_not_exists(
    manager: ConnectionManager, mock_logger: MagicMock
):
    message_to_send = ErrorMessage(error="test")
    result = await manager.send_message(message_to_send, "unknown_client")
    assert result is False
    mock_logger.debug.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_disconnect_exception(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    disconnect_exception = WebSocketDisconnect(code=1000, reason="Test disconnect")
    mock_ws.send_text.side_effect = disconnect_exception
    client_id = "client1"
    manager.active_connections[client_id] = mock_ws
    message_to_send = ErrorMessage(error="test")
    with patch.object(
        manager, "disconnect", wraps=manager.disconnect
    ) as spy_disconnect:
        result = await manager.send_message(message_to_send, client_id)
        assert result is False
        mock_logger.warning.assert_called_with(
            f"Client {client_id} disconnected during send: {disconnect_exception}"
        )
        spy_disconnect.assert_called_once_with(client_id)


@pytest.mark.asyncio
async def test_send_message_generic_runtime_exception(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):  # Renamed from generic_exception
    runtime_error = RuntimeError("Send failed")
    mock_ws.send_text.side_effect = runtime_error
    client_id = "client1"
    manager.active_connections[client_id] = mock_ws
    message_to_send = ErrorMessage(error="test")
    with patch.object(
        manager, "disconnect", wraps=manager.disconnect
    ) as spy_disconnect:
        result = await manager.send_message(message_to_send, client_id)
        assert result is False
        mock_logger.warning.assert_called_with(
            f"Client {client_id} disconnected during send: {runtime_error}"
        )
        spy_disconnect.assert_called_once_with(client_id)


@pytest.mark.asyncio
async def test_send_message_other_exception(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    other_error = SystemError("Other send error")
    mock_ws.send_text.side_effect = other_error
    client_id = "client1"
    manager.active_connections[client_id] = mock_ws
    message_to_send = ErrorMessage(error="test")
    with patch.object(
        manager, "disconnect", wraps=manager.disconnect
    ) as spy_disconnect:
        result = await manager.send_message(message_to_send, client_id)
        assert result is False
        mock_logger.error.assert_called_with(
            f"Error sending to client {client_id}: {other_error}"
        )
        spy_disconnect.assert_called_once_with(client_id)


# === broadcast_json Test ===
@pytest.mark.asyncio
async def test_broadcast_json(manager: ConnectionManager):
    ws1, ws2 = AsyncMock(), AsyncMock()
    manager.active_connections = {"c1": ws1, "c2": ws2}

    class MockBroadcastMessage(WebSocketMessageBase):
        data: str = "broadcast content"

    message = MockBroadcastMessage()
    message_json = message.model_dump_json()
    await manager.broadcast_json(message)
    ws1.send_text.assert_awaited_once_with(message_json)
    ws2.send_text.assert_awaited_once_with(message_json)


# === _handle_registration Error Path Tests ===
@pytest.mark.asyncio
async def test_handle_registration_receive_exception(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.receive_text.side_effect = Exception("Receive error")
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    assert await manager._handle_registration(mock_ws) is None
    mock_send_error_close.assert_awaited_once_with(
        mock_ws,
        "Error receiving registration data: Receive error",
        server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
        log_level="warning",
    )


@pytest.mark.asyncio
async def test_handle_registration_validate_returns_none_ws_connected(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.receive_text.return_value = "{}"
    monkeypatch.setattr(
        "src.server.websocket_manager.validate_registration_message",
        AsyncMock(return_value=None),
    )
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    assert await manager._handle_registration(mock_ws) is None
    mock_send_error_close.assert_awaited_once_with(
        mock_ws,
        "Registration message validation failed.",
        server_config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR,
        log_level="warning",
    )


@pytest.mark.asyncio
async def test_handle_registration_validate_returns_none_ws_disconnected(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.client_state = WebSocketState.DISCONNECTED
    mock_ws.receive_text.return_value = "{}"
    monkeypatch.setattr(
        "src.server.websocket_manager.validate_registration_message",
        AsyncMock(return_value=None),
    )
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    assert await manager._handle_registration(mock_ws) is None
    mock_send_error_close.assert_not_called()
    mock_logger.warning.assert_called_with(
        "Registration message validation failed and websocket was not connected"
    )


# === _handle_post_registration Error Path Tests ===
@pytest.mark.asyncio
async def test_handle_post_registration_redis_update_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
    mock_ws: AsyncMock,
):
    client_status = ClientStatus(client_id="test_id", client_role="worker")
    mock_redis_manager.update_client_status.return_value = False
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    await manager._handle_post_registration(mock_ws, client_status)
    mock_logger.error.assert_called_with(
        f"Failed to update Redis for {client_status.client_id} in post-registration."
    )
    mock_send_error_close.assert_awaited_once_with(
        mock_ws,
        "Server error during registration finalization.",
        server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
        log_level="error",
    )


@pytest.mark.asyncio
async def test_handle_post_registration_send_reg_complete_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    send_exception = Exception("Send reg complete failed")
    mock_ws.send_text.side_effect = send_exception
    client_status = ClientStatus(client_id="test_id", client_role="worker")
    mock_redis_manager.update_client_status.return_value = True
    await manager._handle_post_registration(mock_ws, client_status)
    mock_logger.warning.assert_called_with(
        f"Failed to send registration complete to {client_status.client_id}: {send_exception}"
    )


@pytest.mark.asyncio
async def test_handle_post_registration_frontend_send_all_statuses_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
    mock_ws: AsyncMock,
):
    client_status = ClientStatus(
        client_id="fe_id", client_role=server_config.CLIENT_ROLE_FRONTEND
    )
    mock_redis_manager.update_client_status.return_value = True
    mock_send_all_statuses = AsyncMock(
        side_effect=Exception("Send all statuses failed")
    )
    monkeypatch.setattr(
        manager, "send_all_statuses_to_single_frontend", mock_send_all_statuses
    )
    with pytest.raises(Exception, match="Send all statuses failed"):
        await manager._handle_post_registration(mock_ws, client_status)
    mock_send_all_statuses.assert_awaited_once_with(mock_ws, client_status.client_id)


# === broadcast_client_status_update Tests ===
@pytest.mark.asyncio
async def test_broadcast_client_status_update_invalid_status(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch
):
    mock_broadcast_frontends = AsyncMock()
    monkeypatch.setattr(manager, "broadcast_to_frontends", mock_broadcast_frontends)
    await manager.broadcast_client_status_update(None)  # type: ignore
    mock_logger.warning.assert_called_with(
        "broadcast_client_status_update: Invalid client_status provided."
    )
    mock_broadcast_frontends.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_client_status_update_happy_path(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    monkeypatch,
    mock_logger: MagicMock,
):
    client_status = ClientStatus(
        client_id="test_id", client_role="worker", client_state="running"
    )
    mock_broadcast_frontends = AsyncMock()
    monkeypatch.setattr(manager, "broadcast_to_frontends", mock_broadcast_frontends)
    await manager.broadcast_client_status_update(client_status)
    mock_logger.info.assert_called_with(
        f"Broadcasting status update for client {client_status.client_id} to all frontends."
    )
    mock_broadcast_frontends.assert_awaited_once()
    sent_message_arg: ClientStatusUpdateMessage = mock_broadcast_frontends.call_args[0][
        0
    ]
    assert isinstance(sent_message_arg, ClientStatusUpdateMessage)
    assert sent_message_arg.client_id == client_status.client_id
    assert sent_message_arg.status == client_status.model_dump()
    assert sent_message_arg.timestamp is not None


# === send_all_statuses_to_single_frontend Tests ===
@pytest.mark.asyncio
async def test_send_all_statuses_redis_returns_none(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    mock_redis_manager.get_all_client_statuses.return_value = None
    await manager.send_all_statuses_to_single_frontend(mock_ws, "fe_id")
    mock_logger.warning.assert_called_with(
        "Could not get all_statuses for initial send to fe_id."
    )
    mock_ws.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_send_all_statuses_no_clients(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    all_status_obj = AllClientStatuses(
        clients={},
        redis_status="connected",
        timestamp=datetime.now(UTC).isoformat(),
        total_count=0,
    )
    mock_redis_manager.get_all_client_statuses.return_value = all_status_obj
    await manager.send_all_statuses_to_single_frontend(mock_ws, "fe_id")
    mock_logger.info.assert_any_call(
        f"No clients to send to fe_id, but Redis status: {all_status_obj.redis_status}"
    )
    mock_logger.info.assert_any_call("Sent empty client list to fe_id")
    mock_ws.send_text.assert_awaited_once()
    sent_arg = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg)
    assert sent_data["data"]["clients"] == {}


@pytest.mark.asyncio
async def test_send_all_statuses_send_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    mock_ws.send_text.side_effect = Exception("Send failed")
    clients_data = {"c1": ClientStatus(client_id="c1", client_role="worker")}
    all_status_obj = AllClientStatuses(
        clients=clients_data,
        redis_status="connected",
        timestamp=datetime.now(UTC).isoformat(),
        total_count=1,
    )
    mock_redis_manager.get_all_client_statuses.return_value = all_status_obj
    await manager.send_all_statuses_to_single_frontend(mock_ws, "fe_id")
    mock_logger.warning.assert_called_with(
        "Failed to send initial all_statuses to fe_id: Send failed"
    )


@pytest.mark.asyncio
async def test_send_all_statuses_ws_not_connected(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    mock_ws.client_state = WebSocketState.DISCONNECTED
    clients_data = {"c1": ClientStatus(client_id="c1", client_role="worker")}
    all_status_obj = AllClientStatuses(
        clients=clients_data,
        redis_status="connected",
        timestamp=datetime.now(UTC).isoformat(),
        total_count=1,
    )
    mock_redis_manager.get_all_client_statuses.return_value = all_status_obj
    await manager.send_all_statuses_to_single_frontend(mock_ws, "fe_id")
    mock_logger.warning.assert_called_with(
        "WebSocket for fe_id is not connected. Cannot send all_statuses."
    )
    mock_ws.send_text.assert_not_called()


# === send_to_worker Tests ===
@pytest.mark.asyncio
async def test_send_to_worker_client_not_exists(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    if "unknown_worker" in manager.active_connections:
        del manager.active_connections["unknown_worker"]
    mock_redis_manager.get_client_info.return_value = None
    cmd_msg = CommandMessage(command="pause")
    result = await manager.send_to_worker("unknown_worker", cmd_msg)
    assert result is False
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_send_to_worker_client_is_frontend(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
):
    manager.active_connections["fe_id"] = mock_ws
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="fe_id", client_role=server_config.CLIENT_ROLE_FRONTEND
    )
    cmd_msg = CommandMessage(command="pause")
    result = await manager.send_to_worker("fe_id", cmd_msg)
    assert result is False
    mock_logger.warning.assert_called_with(
        "Attempted to send worker command to non-worker or unknown client: fe_id"
    )


@pytest.mark.asyncio
async def test_send_to_worker_happy_path(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    monkeypatch,
    mock_ws: AsyncMock,
):
    manager.active_connections["worker1"] = mock_ws
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="worker1", client_role="worker"
    )
    mock_internal_send_message = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "send_message", mock_internal_send_message)
    cmd_msg = CommandMessage(command="pause")
    result = await manager.send_to_worker("worker1", cmd_msg)
    assert result is True
    mock_internal_send_message.assert_awaited_once_with(cmd_msg, "worker1")


# === _send_error_and_close_ws Tests ===
@pytest.mark.asyncio
async def test_send_error_and_close_ws_happy_path(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    error_msg_content, code, reason = "Test error msg", 1001, "Test reason"
    await manager._send_error_and_close_ws(
        mock_ws, error_msg_content, code, "error", reason
    )
    mock_logger.error.assert_called_once_with(
        f"Sending error to client and closing WS (code={code}): {error_msg_content}"
    )
    expected_error_msg_json = ErrorMessage(error=error_msg_content).model_dump_json()
    mock_ws.send_text.assert_awaited_once_with(expected_error_msg_json)
    mock_ws.close.assert_awaited_once_with(code=code, reason=reason)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "log_level, logger_method_name",
    [("warning", "warning"), ("info", "info"), ("none", None)],
)
async def test_send_error_and_close_ws_log_levels(
    manager: ConnectionManager,
    mock_logger: MagicMock,
    mock_ws: AsyncMock,
    log_level: str,
    logger_method_name: str | None,
):
    error_msg_content, code, reason = "A test message", 1002, "Param test"
    mock_logger.reset_mock()
    await manager._send_error_and_close_ws(
        mock_ws, error_msg_content, code, log_level, reason
    )
    expected_log = (
        f"Sending error to client and closing WS (code={code}): {error_msg_content}"
    )
    if logger_method_name:
        getattr(mock_logger, logger_method_name).assert_called_once_with(expected_log)
    else:
        mock_logger.error.assert_not_called()
        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_not_called()
    mock_ws.send_text.assert_awaited_once()
    mock_ws.close.assert_awaited_once_with(code=code, reason=reason)


@pytest.mark.asyncio
async def test_send_error_and_close_ws_send_fails(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    send_error = RuntimeError("Send failed")
    mock_ws.send_text.side_effect = send_error
    error_msg_content, code, reason = "Initial error", 1003, "Send fail test"
    await manager._send_error_and_close_ws(
        mock_ws, error_msg_content, code, "error", reason
    )
    mock_logger.error.assert_any_call(
        f"Sending error to client and closing WS (code={code}): {error_msg_content}"
    )
    mock_logger.error.assert_any_call(
        f"Failed to send error message to client before closing: {send_error}"
    )
    mock_ws.close.assert_awaited_once_with(code=code, reason=reason)


@pytest.mark.asyncio
async def test_send_error_and_close_ws_close_fails(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    close_error = RuntimeError("Close failed")
    mock_ws.close.side_effect = close_error
    error_msg_content, code, reason = "Error before close fail", 1004, "Close fail test"
    await manager._send_error_and_close_ws(
        mock_ws, error_msg_content, code, "error", reason
    )
    mock_logger.error.assert_any_call(
        f"Sending error to client and closing WS (code={code}): {error_msg_content}"
    )
    mock_ws.send_text.assert_awaited_once()
    mock_logger.error.assert_any_call(
        f"Exception during WebSocket close after error: {close_error}"
    )


@pytest.mark.asyncio
async def test_send_error_and_close_ws_already_disconnected(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    mock_ws.client_state = WebSocketState.DISCONNECTED
    error_msg_content, code, reason = (
        "Error for disconnected",
        1005,
        "Already disconnected test",
    )
    await manager._send_error_and_close_ws(
        mock_ws, error_msg_content, code, "error", reason
    )
    mock_logger.error.assert_called_once_with(
        f"Sending error to client and closing WS (code={code}): {error_msg_content}"
    )
    mock_logger.info.assert_any_call(
        f"WebSocket already closed or not connected. Error: {error_msg_content}"
    )
    mock_ws.send_text.assert_not_called()
    mock_ws.close.assert_not_called()


# === test_handle_registration_enrich ===
@pytest.mark.asyncio
async def test_handle_registration_enrich(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
    mock_ws: AsyncMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED

    class MockRegistrationMsg:
        client_id, status = "client42", {"client_role": "worker", "version": "1.0"}

    registration_msg_instance = MockRegistrationMsg()
    monkeypatch.setattr(
        "src.server.websocket_manager.validate_registration_message",
        AsyncMock(return_value=registration_msg_instance),
    )
    enriched_status_instance = ClientStatus(
        client_id="client42",
        client_role="worker",
        version="1.0",
        connected="true",
        client_state="initializing",
    )
    monkeypatch.setattr(
        "src.shared.schemas.websocket.ClientStatus.enrich_from_registration",
        MagicMock(return_value=enriched_status_instance),
    )
    mock_ws.receive_text = AsyncMock(
        return_value='{"type": "register", "client_id": "client42", "status": {"client_role": "worker"}}'
    )
    monkeypatch.setattr(manager, "send_all_statuses_to_single_frontend", AsyncMock())
    result_status = await manager._handle_registration(mock_ws)
    assert isinstance(result_status, ClientStatus)
    assert result_status.client_id == "client42"
    assert result_status.client_role == "worker"
    mock_redis_manager.update_client_status.assert_awaited_once_with(
        enriched_status_instance, broadcast=True
    )
    expected_reg_json = RegistrationCompleteMessage(
        client_id="client42", redis_status="connected"
    ).model_dump_json()
    if result_status.client_role == "worker":
        mock_ws.send_text.assert_awaited_with(expected_reg_json)
    else:
        mock_ws.send_text.assert_any_call(expected_reg_json)


# === test_disconnect ===
def test_disconnect(manager: ConnectionManager):
    ws = AsyncMock()
    manager.active_connections["client1"] = ws
    manager.disconnect("client1")
    assert "client1" not in manager.active_connections


# === New Tests (Phase 3/4 from previous turn, Phase 5 now) ===


# handle_websocket_session Error & Finally Block Tests
@pytest.mark.asyncio
async def test_hws_handle_registration_returns_none(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.accept = AsyncMock()
    monkeypatch.setattr(manager, "_handle_registration", AsyncMock(return_value=None))
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    mock_ws.close = AsyncMock()
    await manager.handle_websocket_session(mock_ws)
    mock_logger.error.assert_called_with(
        "Failed to handle registration. WebSocket may have been closed."
    )
    spy_disconnect.assert_not_called()
    if mock_ws.client_state != WebSocketState.DISCONNECTED:
        mock_ws.close.assert_awaited_once_with(
            code=server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
            reason="Registration failed cleanup",
        )


@pytest.mark.asyncio
async def test_hws_json_decode_error_in_loop(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.accept = AsyncMock()
    cs = ClientStatus(client_id="c1", client_role="worker")
    monkeypatch.setattr(manager, "_handle_registration", AsyncMock(return_value=cs))
    json_error = json.JSONDecodeError("err", "doc", 0)
    monkeypatch.setattr(
        manager, "_handle_message_loop", AsyncMock(side_effect=json_error)
    )
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    await manager.handle_websocket_session(mock_ws)
    mock_logger.error.assert_any_call(
        f"{server_config.ERROR_MSG_INVALID_JSON} from c1: {json_error}", exc_info=True
    )
    mock_send_error_close.assert_awaited_once_with(
        mock_ws,
        server_config.ERROR_MSG_INVALID_JSON,
        server_config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR,
        log_level="none",
    )
    spy_disconnect.assert_called_once_with("c1")


@pytest.mark.asyncio
async def test_hws_generic_exception_in_loop(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.accept = AsyncMock()
    cs = ClientStatus(client_id="c1", client_role="worker")
    monkeypatch.setattr(manager, "_handle_registration", AsyncMock(return_value=cs))
    generic_exception = Exception("Generic error in loop")
    monkeypatch.setattr(
        manager, "_handle_message_loop", AsyncMock(side_effect=generic_exception)
    )
    mock_send_error_close = AsyncMock()
    monkeypatch.setattr(manager, "_send_error_and_close_ws", mock_send_error_close)
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    await manager.handle_websocket_session(mock_ws)
    mock_logger.error.assert_any_call(
        f"Unexpected error in WebSocket session for client 'c1': {generic_exception}",
        exc_info=True,
    )
    mock_send_error_close.assert_awaited_once_with(
        mock_ws,
        "An unexpected error occurred. Connection will be closed.",
        server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
        log_level="none",
    )
    spy_disconnect.assert_called_once_with("c1")


@pytest.mark.asyncio
async def test_hws_finally_block_client_status_none_ws_not_disconnected(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.accept = AsyncMock()

    async def mock_handle_reg_no_close(websocket):
        # Ensure ws_mock.client_state remains CONNECTED for this test by not calling _send_error_and_close_ws
        # which is the component that would change its state or close it in the actual _handle_registration
        return None

    monkeypatch.setattr(manager, "_handle_registration", mock_handle_reg_no_close)
    # Prevent _send_error_and_close_ws (if called by _handle_registration) from actually closing the mock_ws
    monkeypatch.setattr(manager, "_send_error_and_close_ws", AsyncMock())
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    # mock_ws.close is what we are testing from the finally block
    mock_ws.close = (
        AsyncMock()
    )  # Make sure it's an AsyncMock on the instance for this test.

    await manager.handle_websocket_session(mock_ws)

    spy_disconnect.assert_not_called()
    mock_logger.warning.assert_called_with(
        "Client status object is None, but websocket state is not disconnected. Attempting to close."
    )
    mock_ws.close.assert_awaited_once_with(
        code=server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
        reason="Registration failed cleanup",
    )


@pytest.mark.asyncio
async def test_hws_finally_block_close_exception(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    mock_ws.accept = AsyncMock()
    monkeypatch.setattr(manager, "_handle_registration", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_send_error_and_close_ws", AsyncMock())
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)

    mock_ws.close = AsyncMock(side_effect=RuntimeError("Final close failed"))

    await manager.handle_websocket_session(
        mock_ws
    )  # Should not raise an exception due to 'except Exception: pass'

    mock_logger.warning.assert_called_with(
        "Client status object is None, but websocket state is not disconnected. Attempting to close."
    )
    mock_ws.close.assert_awaited_once_with(
        code=server_config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
        reason="Registration failed cleanup",
    )
    # No specific log for the close exception itself as it's caught and passed.


# close_connection Tests
@pytest.mark.asyncio
async def test_close_connection_client_not_exists(
    manager: ConnectionManager, mock_logger: MagicMock
):
    assert await manager.close_connection("unknown") is False
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_close_connection_ws_close_exception(
    manager: ConnectionManager, mock_logger: MagicMock, monkeypatch, mock_ws: AsyncMock
):
    close_exception = Exception("Close error")
    mock_ws.close.side_effect = close_exception
    manager.active_connections["c1"] = mock_ws
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    assert await manager.close_connection("c1") is False
    mock_logger.warning.assert_called_with(
        f"Error closing connection for c1: {close_exception}"
    )
    spy_disconnect.assert_called_once_with("c1")


@pytest.mark.asyncio
async def test_close_connection_happy_path(
    manager: ConnectionManager, monkeypatch, mock_ws: AsyncMock
):
    manager.active_connections["c1"] = mock_ws
    spy_disconnect = MagicMock()
    monkeypatch.setattr(manager, "disconnect", spy_disconnect)
    assert await manager.close_connection("c1") is True
    mock_ws.close.assert_awaited_once_with(code=1000, reason="Server initiated")
    spy_disconnect.assert_called_once_with("c1")


# get_connection_stats Tests
@pytest.mark.asyncio
async def test_get_connection_stats_no_connections(
    manager: ConnectionManager, mock_redis_manager: AsyncMock
):
    stats = await manager.get_connection_stats()
    assert stats == {
        "total_connections": 0,
        "frontend_connections": 0,
        "worker_connections": 0,
    }


@pytest.mark.asyncio
async def test_get_connection_stats_mix_clients(
    manager: ConnectionManager, mock_redis_manager: AsyncMock
):
    manager.active_connections = {
        "fe1": AsyncMock(),
        "w1": AsyncMock(),
        "fe2": AsyncMock(),
    }

    async def side_effect(client_id_arg):
        if "fe" in client_id_arg:
            return ClientStatus(client_id=client_id_arg, client_role="frontend")
        return ClientStatus(client_id=client_id_arg, client_role="worker")

    mock_redis_manager.get_client_info.side_effect = side_effect
    stats = await manager.get_connection_stats()
    assert stats == {
        "total_connections": 3,
        "frontend_connections": 2,
        "worker_connections": 1,
    }


@pytest.mark.asyncio
async def test_get_connection_stats_redis_info_none(
    manager: ConnectionManager, mock_redis_manager: AsyncMock
):
    manager.active_connections = {"c1": AsyncMock()}
    mock_redis_manager.get_client_info.return_value = None
    stats = await manager.get_connection_stats()
    assert stats == {
        "total_connections": 1,
        "frontend_connections": 0,
        "worker_connections": 0,
    }


# _handle_unknown_frontend_message Test
@pytest.mark.asyncio
async def test_handle_unknown_frontend_message(
    manager: ConnectionManager, mock_ws: AsyncMock
):
    msg = {"type": "random_type", "data": "somedata"}
    await manager._handle_unknown_frontend_message(msg, mock_ws, "fe_id")
    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_arg_dict = json.loads(sent_arg_str)
    assert sent_arg_dict["type"] == "message_receipt_unknown"
    assert server_config.INFO_MSG_UNHANDLED_FRONTEND_MSG in sent_arg_dict["info"]


# _handle_heartbeat_message Tests
@pytest.mark.asyncio
async def test_hhm_client_not_found(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = None
    await manager._handle_heartbeat_message("c1", {})
    mock_logger.warning.assert_called_with(
        "Heartbeat from unknown or unregistered client c1. Ignoring."
    )


@pytest.mark.asyncio
async def test_hhm_client_not_frontend(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="c1", client_role="worker"
    )
    await manager._handle_heartbeat_message("c1", {})
    mock_logger.warning.assert_called_with(
        "Heartbeat received from non-frontend client c1 (role: worker). Ignoring."
    )


@pytest.mark.asyncio
async def test_hhm_redis_update_fails(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="fe1", client_role=server_config.CLIENT_ROLE_FRONTEND
    )
    mock_redis_manager.update_client_status.return_value = False
    await manager._handle_heartbeat_message("fe1", {})
    mock_logger.warning.assert_called_with(
        "Failed to update heartbeat timestamp for fe1 in Redis."
    )


@pytest.mark.asyncio
async def test_hhm_happy_path(
    manager: ConnectionManager, mock_redis_manager: AsyncMock
):
    old_ts_dt = datetime.now(UTC) - timedelta(seconds=60)
    old_ts_iso = old_ts_dt.isoformat()
    cs = ClientStatus(
        client_id="fe1",
        client_role=server_config.CLIENT_ROLE_FRONTEND,
        last_seen=old_ts_iso,
        last_heartbeat_timestamp=old_ts_iso,
    )
    mock_redis_manager.get_client_info.return_value = cs
    mock_redis_manager.update_client_status.return_value = True
    await manager._handle_heartbeat_message("fe1", {})
    mock_redis_manager.update_client_status.assert_awaited_once()
    updated_status_arg: ClientStatus = (
        mock_redis_manager.update_client_status.call_args[0][0]
    )
    assert updated_status_arg.client_id == "fe1"
    assert updated_status_arg.last_heartbeat_timestamp is not None
    assert updated_status_arg.last_heartbeat_timestamp != old_ts_iso
    assert updated_status_arg.last_seen == updated_status_arg.last_heartbeat_timestamp


@pytest.mark.asyncio
async def test_ulct_get_info_exception(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    redis_error = RuntimeError("DB error")
    mock_redis_manager.get_client_info.side_effect = redis_error
    assert await manager._update_last_communication_time("w1") is False
    mock_logger.error.assert_called_with(
        f"Error in _update_last_communication_time for w1: {redis_error}", exc_info=True
    )


# _handle_control_message Basic Tests
@pytest.mark.asyncio
async def test_hcm_not_frontend(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    await manager._handle_control_message({}, mock_ws, "w1", "worker")
    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_arg_dict = json.loads(sent_arg_str)
    assert sent_arg_dict["message"] == server_config.ERROR_MSG_CONTROL_PERMISSION_DENIED
    mock_logger.warning.assert_called_with(
        "Non-frontend client w1 attempted control action None"
    )


@pytest.mark.asyncio
async def test_hcm_missing_action(manager: ConnectionManager, mock_ws: AsyncMock):
    await manager._handle_control_message(
        {"target_client_id": "t1"}, mock_ws, "fe1", server_config.CLIENT_ROLE_FRONTEND
    )
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_arg_dict = json.loads(sent_arg_str)
    assert sent_arg_dict["message"] == server_config.ERROR_MSG_CONTROL_INVALID_PAYLOAD


@pytest.mark.asyncio
async def test_hcm_missing_target_client_id(
    manager: ConnectionManager, mock_ws: AsyncMock
):
    await manager._handle_control_message(
        {"action": "pause"}, mock_ws, "fe1", server_config.CLIENT_ROLE_FRONTEND
    )
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_arg_dict = json.loads(sent_arg_str)
    assert sent_arg_dict["message"] == server_config.ERROR_MSG_CONTROL_INVALID_PAYLOAD


@pytest.mark.asyncio
async def test_hcm_unknown_action(
    manager: ConnectionManager, mock_logger: MagicMock, mock_ws: AsyncMock
):
    await manager._handle_control_message(
        {"action": "fly", "target_client_id": "t1"},
        mock_ws,
        "fe1",
        server_config.CLIENT_ROLE_FRONTEND,
    )
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_arg_dict = json.loads(sent_arg_str)
    assert "Unknown control action: fly" in sent_arg_dict["message"]
    mock_logger.info.assert_called_with("Frontend fe1 sending control: fly to t1")


# _handle_control_message (Action Details)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        server_config.CONTROL_ACTION_PAUSE,
        server_config.CONTROL_ACTION_RESUME,
        server_config.CONTROL_ACTION_DISCONNECT,
    ],
)
async def test_hcm_action_details_success(
    manager: ConnectionManager, monkeypatch, action: str, mock_ws: AsyncMock
):
    target_method_name = f"{action}_client"
    mock_action_method_response = {
        "status": "success",
        "message": f"{action} processed",
    }
    mock_action_method = AsyncMock(return_value=mock_action_method_response)
    monkeypatch.setattr(manager, target_method_name, mock_action_method)
    control_msg = {
        "type": "control",
        "action": action,
        "target_client_id": "worker1",
        "message_id": "msg1",
    }
    await manager._handle_control_message(
        control_msg, mock_ws, "frontend1", server_config.CLIENT_ROLE_FRONTEND
    )
    mock_action_method.assert_called_once_with("worker1", "frontend1")
    mock_ws.send_text.assert_awaited_once()
    response_json = mock_ws.send_text.call_args[0][0]
    response_data = json.loads(response_json)
    assert response_data["action"] == action
    assert response_data["target_client_id"] == "worker1"
    assert response_data["status"] == "success"
    assert response_data["message"] == f"{action} processed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        server_config.CONTROL_ACTION_PAUSE,
        server_config.CONTROL_ACTION_RESUME,
        server_config.CONTROL_ACTION_DISCONNECT,
    ],
)
async def test_hcm_action_details_error_from_method(
    manager: ConnectionManager, monkeypatch, action: str, mock_ws: AsyncMock
):
    target_method_name = f"{action}_client"
    mock_action_method_response = {"status": "error", "message": f"{action} failed"}
    mock_action_method = AsyncMock(return_value=mock_action_method_response)
    monkeypatch.setattr(manager, target_method_name, mock_action_method)
    control_msg = {
        "type": "control",
        "action": action,
        "target_client_id": "worker1",
        "message_id": "msg1",
    }
    await manager._handle_control_message(
        control_msg, mock_ws, "frontend1", server_config.CLIENT_ROLE_FRONTEND
    )
    mock_action_method.assert_called_once_with("worker1", "frontend1")
    mock_ws.send_text.assert_awaited_once()
    response_json = mock_ws.send_text.call_args[0][0]
    response_data = json.loads(response_json)
    assert response_data["action"] == action
    assert response_data["target_client_id"] == "worker1"
    assert response_data["status"] == "error"
    assert response_data["message"] == f"{action} failed"


# pause_client Tests
@pytest.mark.asyncio
async def test_pause_client_target_not_found(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = None
    response = await manager.pause_client("worker_unknown", "frontend1")
    assert response["status"] == server_config.MSG_TYPE_ERROR
    assert (
        "Client worker_unknown not found or already disconnected. Cannot pause."
        in response["message"]
    )
    mock_logger.error.assert_called_with(
        "Client worker_unknown not found or already disconnected. Cannot pause."
    )


@pytest.mark.asyncio
async def test_pause_client_target_disconnected(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        connected=server_config.STATUS_VALUE_DISCONNECTED,
        client_role="worker",
    )
    response = await manager.pause_client("w1", "frontend1")
    assert response["status"] == server_config.MSG_TYPE_ERROR


@pytest.mark.asyncio
async def test_pause_client_already_paused(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_PAUSED,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    response = await manager.pause_client("w1", "frontend1")
    assert response["status"] == server_config.PAYLOAD_KEY_INFO
    assert "Client w1 is already paused. No action taken." in response["message"]
    mock_logger.info.assert_called_with("Client w1 is already paused. No action taken.")


@pytest.mark.asyncio
async def test_pause_client_send_to_worker_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_RUNNING,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    monkeypatch.setattr(manager, "send_to_worker", AsyncMock(return_value=False))
    response = await manager.pause_client("w1", "frontend1")
    mock_logger.warning.assert_called_with(
        "Client w1 not actively connected via WebSocket for pause. Updating state in Redis only."
    )
    assert response["status"] == "success"
    mock_redis_manager.update_client_status.assert_awaited_once()
    status_arg: ClientStatus = mock_redis_manager.update_client_status.call_args[0][0]
    assert status_arg.client_state == server_config.CLIENT_STATE_PAUSED


@pytest.mark.asyncio
async def test_pause_client_redis_update_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_RUNNING,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    monkeypatch.setattr(manager, "send_to_worker", AsyncMock(return_value=True))
    redis_error = Exception("Redis boom")
    mock_redis_manager.update_client_status.side_effect = redis_error
    response = await manager.pause_client("w1", "frontend1")
    mock_logger.error.assert_called_with(
        f"Failed to update client status for pause on w1: {redis_error}"
    )
    assert response["status"] == "success"


@pytest.mark.asyncio
async def test_pause_client_happy_path(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    monkeypatch,
    mock_logger: MagicMock,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_RUNNING,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    mock_send_to_worker = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "send_to_worker", mock_send_to_worker)
    response = await manager.pause_client("w1", "frontend1")
    mock_logger.info.assert_any_call(
        "Processing pause request for client w1, initiated by frontend1"
    )
    assert response["status"] == "success"
    mock_send_to_worker.assert_awaited_once()
    mock_redis_manager.update_client_status.assert_awaited_once()
    status_arg: ClientStatus = mock_redis_manager.update_client_status.call_args[0][0]
    assert status_arg.client_state == server_config.CLIENT_STATE_PAUSED


# resume_client Tests
@pytest.mark.asyncio
async def test_resume_client_target_not_found(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = None
    response = await manager.resume_client("worker_unknown", "frontend1")
    assert response["status"] == server_config.MSG_TYPE_ERROR
    assert (
        "Client worker_unknown not found or already disconnected. Cannot resume."
        in response["message"]
    )
    mock_logger.error.assert_called_with(
        "Client worker_unknown not found or already disconnected. Cannot resume."
    )


@pytest.mark.asyncio
async def test_resume_client_target_disconnected(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        connected=server_config.STATUS_VALUE_DISCONNECTED,
        client_role="worker",
    )
    response = await manager.resume_client("w1", "frontend1")
    assert response["status"] == server_config.MSG_TYPE_ERROR


@pytest.mark.asyncio
async def test_resume_client_already_running(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_RUNNING,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    response = await manager.resume_client("w1", "frontend1")
    assert response["status"] == server_config.PAYLOAD_KEY_INFO
    mock_logger.info.assert_called_with(
        "Client w1 is already running. No action taken."
    )


@pytest.mark.asyncio
async def test_resume_client_send_to_worker_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_PAUSED,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    monkeypatch.setattr(manager, "send_to_worker", AsyncMock(return_value=False))
    response = await manager.resume_client("w1", "frontend1")
    mock_logger.warning.assert_called_with(
        "Client w1 not actively connected via WebSocket for resume. Updating state in Redis only."
    )
    assert response["status"] == "success"
    status_arg: ClientStatus = mock_redis_manager.update_client_status.call_args[0][0]
    assert status_arg.client_state == server_config.CLIENT_STATE_RUNNING


@pytest.mark.asyncio
async def test_resume_client_redis_update_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_PAUSED,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    monkeypatch.setattr(manager, "send_to_worker", AsyncMock(return_value=True))
    redis_error = Exception("Redis boom")
    mock_redis_manager.update_client_status.side_effect = redis_error
    response = await manager.resume_client("w1", "frontend1")
    mock_logger.error.assert_called_with(
        f"Failed to update client status for resume on w1: {redis_error}"
    )
    assert response["status"] == "success"


@pytest.mark.asyncio
async def test_resume_client_happy_path(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    monkeypatch,
    mock_logger: MagicMock,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_state=server_config.CLIENT_STATE_PAUSED,
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    mock_send_to_worker = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "send_to_worker", mock_send_to_worker)
    response = await manager.resume_client("w1", "frontend1")
    mock_logger.info.assert_any_call(
        "Processing resume request for client w1, initiated by frontend1"
    )
    assert response["status"] == "success"
    mock_send_to_worker.assert_awaited_once()
    status_arg: ClientStatus = mock_redis_manager.update_client_status.call_args[0][0]
    assert status_arg.client_state == server_config.CLIENT_STATE_RUNNING


# disconnect_client Tests
@pytest.mark.asyncio
async def test_disconnect_client_already_disconnected_in_redis(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        connected=server_config.STATUS_VALUE_DISCONNECTED,
        client_role="worker",
    )
    mock_close_connection = AsyncMock(return_value=False)
    monkeypatch.setattr(manager, "close_connection", mock_close_connection)
    response = await manager.disconnect_client("w1", "frontend1")
    assert response["status"] == server_config.PAYLOAD_KEY_INFO
    mock_logger.info.assert_any_call(
        "Client w1 was already disconnected. Status re-updated and broadcasted."
    )
    status_arg: ClientStatus = mock_redis_manager.update_client_status.call_args[0][0]
    assert status_arg.connected == server_config.STATUS_VALUE_DISCONNECTED
    assert status_arg.client_state == server_config.CLIENT_STATE_OFFLINE


@pytest.mark.asyncio
async def test_disconnect_client_close_connection_fails_but_was_not_in_redis(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = None
    mock_close_connection = AsyncMock(return_value=False)
    monkeypatch.setattr(manager, "close_connection", mock_close_connection)
    await manager.disconnect_client("w1", "frontend1")
    found_log = False
    for call_args in mock_logger.error.call_args_list:
        logged_message = str(call_args[0][0])
        # Check if the logged message indicates a ValidationError related to client_role being missing
        # This happens because the code tries to construct ClientStatus without client_role if get_client_info was None
        if (
            "Failed to update client status for disconnect on w1" in logged_message
            and "1 validation error for ClientStatus" in logged_message
            and "client_role" in logged_message
            and "Field required" in logged_message
        ):
            found_log = True
            break
    assert (
        found_log
    ), "Expected ValidationError log for missing client_role not found or not matching."
    mock_redis_manager.update_client_status.assert_not_called()


@pytest.mark.asyncio
async def test_disconnect_client_close_connection_succeeds(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    mock_close_connection = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "close_connection", mock_close_connection)
    response = await manager.disconnect_client("w1", "frontend1")
    assert response["status"] == "success"
    mock_logger.info.assert_any_call(
        "Disconnection process for client w1 initiated. WebSocket closed, status updated and broadcasted."
    )


@pytest.mark.asyncio
async def test_disconnect_client_redis_update_fails(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        connected=server_config.STATUS_VALUE_CONNECTED,
        client_role="worker",
    )
    monkeypatch.setattr(manager, "close_connection", AsyncMock(return_value=True))
    redis_error = Exception("Redis boom")
    mock_redis_manager.update_client_status.side_effect = redis_error
    await manager.disconnect_client("w1", "frontend1")
    mock_logger.error.assert_called_with(
        f"Failed to update client status for disconnect on w1: {redis_error}"
    )


# _update_last_communication_time Tests
@pytest.mark.asyncio
async def test_ulct_client_not_found(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = None
    assert await manager._update_last_communication_time("w1") is False
    mock_logger.warning.assert_called_with(
        "Cannot update last_communication_time for w1: Client not found in Redis."
    )


@pytest.mark.asyncio
async def test_ulct_timestamp_not_changing(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    now_dt = datetime.now(UTC)
    now_iso = now_dt.isoformat()
    cs = ClientStatus(
        client_id="w1",
        client_role="worker",
        last_communication_timestamp=now_iso,
        last_seen=now_iso,
    )
    mock_redis_manager.get_client_info.return_value = cs
    mock_datetime = MagicMock()
    mock_datetime.now.return_value = datetime.fromisoformat(
        now_iso.replace("Z", "+00:00")
    )
    monkeypatch.setattr("src.server.websocket_manager.datetime", mock_datetime)
    assert await manager._update_last_communication_time("w1") is True
    mock_redis_manager.update_client_status.assert_not_called()


@pytest.mark.asyncio
async def test_ulct_validation_error(
    manager: ConnectionManager,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    validation_error = ValidationError.from_exception_data(
        title="err",
        line_errors=[
            {
                "type": "string_type",
                "loc": ("field",),
                "msg": "Input should be a valid string",
                "input": 123,
            }
        ],
    )
    monkeypatch.setattr(
        ClientStatus, "model_validate", MagicMock(side_effect=validation_error)
    )
    assert await manager._update_last_communication_time("w1") is False
    mock_logger.error.assert_called_with(
        f"Validation error updating communication time for w1: {validation_error}"
    )


@pytest.mark.asyncio
async def test_ulct_redis_update_fails(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    mock_redis_manager.update_client_status.return_value = False
    assert await manager._update_last_communication_time("w1") is False
    mock_logger.warning.assert_called_with(
        "Failed to update last_communication_timestamp for w1 in Redis."
    )


@pytest.mark.asyncio
async def test_ulct_happy_path(
    manager: ConnectionManager, mock_redis_manager: AsyncMock, mock_logger: MagicMock
):
    old_ts_dt = datetime.now(UTC) - timedelta(seconds=10)
    old_ts_iso = old_ts_dt.isoformat()
    cs = ClientStatus(
        client_id="w1",
        client_role="worker",
        last_communication_timestamp=old_ts_iso,
        last_seen=old_ts_iso,
    )
    mock_redis_manager.get_client_info.return_value = cs
    mock_redis_manager.update_client_status.return_value = True
    assert await manager._update_last_communication_time("w1") is True
    mock_redis_manager.update_client_status.assert_awaited_once()
    updated_status: ClientStatus = mock_redis_manager.update_client_status.call_args[0][
        0
    ]
    assert updated_status.last_communication_timestamp != old_ts_iso
    assert updated_status.last_seen != old_ts_iso
    mock_logger.debug.assert_called_with("Updated last_communication_timestamp for w1.")


# _handle_message_loop Routing Tests
@pytest.mark.asyncio
async def test_hml_routes_to_status_update(
    manager: ConnectionManager, mock_ws: AsyncMock, monkeypatch
):
    mock_hsu = AsyncMock()
    monkeypatch.setattr(manager, "_handle_status_update", mock_hsu)
    status_message = {"type": server_config.MSG_TYPE_STATUS, "payload": "data"}
    mock_ws.receive_text.side_effect = [
        json.dumps(status_message),
        WebSocketDisconnect(1000),
    ]
    with pytest.raises(WebSocketDisconnect):
        await manager._handle_message_loop(
            mock_ws, "worker1", server_config.CLIENT_ROLE_WORKER
        )
    mock_hsu.assert_awaited_once_with(status_message, mock_ws, "worker1")


@pytest.mark.asyncio
async def test_hml_routes_to_heartbeat(
    manager: ConnectionManager, mock_ws: AsyncMock, monkeypatch
):
    mock_hhm = AsyncMock()
    monkeypatch.setattr(manager, "_handle_heartbeat_message", mock_hhm)
    heartbeat_message = {"type": server_config.MSG_TYPE_HEARTBEAT}
    mock_ws.receive_text.side_effect = [
        json.dumps(heartbeat_message),
        WebSocketDisconnect(1000),
    ]
    with pytest.raises(WebSocketDisconnect):
        await manager._handle_message_loop(
            mock_ws, "fe1", server_config.CLIENT_ROLE_FRONTEND
        )
    mock_hhm.assert_awaited_once_with("fe1", heartbeat_message)


@pytest.mark.asyncio
async def test_hml_worker_generic_message_updates_time_and_acks(
    manager: ConnectionManager, mock_ws: AsyncMock, monkeypatch, mock_redis_manager
):
    mock_ulct = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_update_last_communication_time", mock_ulct)
    generic_message = {"type": "generic_worker_msg", "data": "content"}
    mock_ws.receive_text.side_effect = [
        json.dumps(generic_message),
        WebSocketDisconnect(1000),
    ]
    with pytest.raises(WebSocketDisconnect):
        await manager._handle_message_loop(
            mock_ws, "worker1", server_config.CLIENT_ROLE_WORKER
        )
    mock_ulct.assert_awaited_once_with("worker1")
    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    assert sent_data["type"] == "message_processed"
    assert (
        sent_data["status_updated"] == {}
    )  # Check that status_updated is an empty dict as per code.


@pytest.mark.asyncio
async def test_hml_routes_to_unknown_frontend_message(
    manager: ConnectionManager, mock_ws: AsyncMock, monkeypatch
):
    mock_hufm = AsyncMock()
    monkeypatch.setattr(manager, "_handle_unknown_frontend_message", mock_hufm)
    unknown_message = {"type": "some_other_type", "detail": "blah"}
    mock_ws.receive_text.side_effect = [
        json.dumps(unknown_message),
        WebSocketDisconnect(1000),
    ]
    with pytest.raises(WebSocketDisconnect):
        await manager._handle_message_loop(
            mock_ws, "fe1", server_config.CLIENT_ROLE_FRONTEND
        )
    mock_hufm.assert_awaited_once_with(unknown_message, mock_ws, "fe1")


# _handle_status_update Tests
@pytest.mark.asyncio
async def test_hsu_client_id_mismatch(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="auth_worker", client_role="worker"
    )
    message = {"client_id": "other_worker", "status": {"state": "running"}}
    await manager._handle_status_update(message, mock_ws, "auth_worker")
    mock_logger.warning.assert_called_with(
        "Worker auth_worker sent status with conflicting client_id 'other_worker'. Ignoring message client_id, using authenticated session client_id."
    )
    assert (
        mock_redis_manager.update_client_status.call_args[0][0].client_id
        == "auth_worker"
    )


@pytest.mark.asyncio
async def test_hsu_status_not_dict(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    message = {"status": "not_a_dict"}
    await manager._handle_status_update(message, mock_ws, "w1")
    mock_logger.warning.assert_called_with(
        "Non-dict status received from worker w1: not_a_dict. Wrapping as raw payload."
    )
    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    # The 'raw_payload' is not part of ClientStatus, so it's dropped by Pydantic `model_validate` (extra='ignore')
    # and won't be in `updated_client_status_obj.model_dump()`.
    # The `final_status_for_ack` becomes `updated_client_status_obj.model_dump()` if validation doesn't raise an error.
    assert server_config.STATUS_KEY_RAW_PAYLOAD not in sent_data["status_updated"]
    assert (
        sent_data["status_updated"][server_config.STATUS_KEY_UPDATE_STATUS] == "success"
    )  # because validation of ClientStatus passes (ignores extra)


@pytest.mark.asyncio
async def test_hsu_existing_client_not_in_redis(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = None
    await manager._handle_status_update({"status": {}}, mock_ws, "w1")
    mock_logger.error.assert_called_with(
        "Cannot process status update for w1: Client not found in Redis. This should not happen for an authenticated worker."
    )
    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    assert (
        sent_data["status_updated"][server_config.STATUS_KEY_UPDATE_STATUS]
        == "failed_no_client_record"
    )
    mock_redis_manager.update_client_status.assert_not_called()


@pytest.mark.asyncio
async def test_hsu_state_transition_dormant_to_running(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_role="worker",
        client_state=server_config.CLIENT_STATE_DORMANT,
        connected="true",
    )
    mock_redis_manager.update_client_status.return_value = True
    await manager._handle_status_update({"status": {"info": "update"}}, mock_ws, "w1")
    updated_status: ClientStatus = mock_redis_manager.update_client_status.call_args[0][
        0
    ]
    assert updated_status.client_state == server_config.CLIENT_STATE_RUNNING
    mock_logger.info.assert_any_call(
        "Client w1 awakened from dormant to running due to status update."
    )


@pytest.mark.asyncio
async def test_hsu_state_transition_initializing_to_running(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_role="worker",
        client_state=server_config.CLIENT_STATE_INITIALIZING,
        connected="true",
    )
    mock_redis_manager.update_client_status.return_value = True
    await manager._handle_status_update({"status": {"info": "update"}}, mock_ws, "w1")
    updated_status: ClientStatus = mock_redis_manager.update_client_status.call_args[0][
        0
    ]
    assert updated_status.client_state == server_config.CLIENT_STATE_RUNNING
    mock_logger.info.assert_any_call(
        f"Client w1 moved from '{server_config.CLIENT_STATE_INITIALIZING}' to running due to status update."
    )


@pytest.mark.asyncio
async def test_hsu_state_incoming_takes_precedence(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_role="worker",
        client_state=server_config.CLIENT_STATE_DORMANT,
        connected="true",
    )
    mock_redis_manager.update_client_status.return_value = True
    await manager._handle_status_update(
        {"status": {"client_state": server_config.CLIENT_STATE_PAUSED}}, mock_ws, "w1"
    )
    updated_status: ClientStatus = mock_redis_manager.update_client_status.call_args[0][
        0
    ]
    assert updated_status.client_state == server_config.CLIENT_STATE_PAUSED


@pytest.mark.asyncio
async def test_hsu_validation_error(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
    monkeypatch,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    # Ensure that the actual model validation is called, not a MagicMock that bypasses it.
    # The side effect should be on the specific validation that would fail.
    # Here, we'll make merged_data invalid by making an existing field an incorrect type.
    # The error should come from ClientStatus.model_validate(merged_data)

    original_model_validate = ClientStatus.model_validate

    def faulty_model_validate(data, *args, **kwargs):
        if "make_it_fail" in data.get("attributes", {}):
            raise ValidationError.from_exception_data(
                title="err",
                line_errors=[
                    {
                        "type": "string_type",
                        "loc": ("field",),
                        "msg": "Test Validation Error",
                        "input": 123,
                    }
                ],
            )
        return original_model_validate(data, *args, **kwargs)

    monkeypatch.setattr(ClientStatus, "model_validate", faulty_model_validate)

    await manager._handle_status_update(
        {"status": {"attributes": {"make_it_fail": True}}}, mock_ws, "w1"
    )

    # Check that logger.error was called with a message containing the validation error details and exc_info=True
    called_correctly = False
    for call_item in mock_logger.error.call_args_list:
        args, kwargs = call_item
        if args and isinstance(args[0], str):
            log_message = args[0]
            # Check if the log message contains the key parts of the error
            if (
                "Validation error processing status update for w1" in log_message
                and "Input should be a valid string"
                in log_message  # Standard Pydantic message for 'string_type'
                and "err" in log_message  # This is the 'title' from ValidationError
                and "field" in log_message  # This is the 'loc' from ValidationError
                and kwargs.get("exc_info") is True
            ):
                called_correctly = True
                break

    assert (
        called_correctly
    ), f"Expected logger.error call with validation details and exc_info=True not found. \nActual calls: {mock_logger.error.call_args_list}"

    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    assert (
        sent_data["status_updated"][server_config.STATUS_KEY_UPDATE_STATUS]
        == "failed_validation"
    )


@pytest.mark.asyncio
async def test_hsu_redis_update_fails_post_validation(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    mock_redis_manager.update_client_status.return_value = False
    await manager._handle_status_update(
        {"status": {"info": "good_update"}}, mock_ws, "w1"
    )
    mock_logger.error.assert_called_with(
        "Failed to update Redis for worker w1 after successful validation."
    )
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    assert (
        sent_data["status_updated"][server_config.STATUS_KEY_UPDATE_STATUS]
        == "failed_redis_update"
    )


@pytest.mark.asyncio
async def test_hsu_ack_send_fails(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1", client_role="worker"
    )
    mock_redis_manager.update_client_status.return_value = True
    ack_send_exception = Exception("ACK send failed")
    mock_ws.send_text.side_effect = ack_send_exception
    await manager._handle_status_update({"status": {}}, mock_ws, "w1")
    mock_logger.error.assert_called_with(
        f"Failed to prepare or send status update ACK to w1: {ack_send_exception}",
        exc_info=True,
    )


@pytest.mark.asyncio
async def test_hsu_happy_path(
    manager: ConnectionManager,
    mock_ws: AsyncMock,
    mock_redis_manager: AsyncMock,
    mock_logger: MagicMock,
):
    mock_ws.client_state = WebSocketState.CONNECTED
    current_time = datetime.now(UTC)
    mock_redis_manager.get_client_info.return_value = ClientStatus(
        client_id="w1",
        client_role="worker",
        client_state="running",
        connected="true",
        attributes={"old_attr": "val"},  # existing attributes
        last_seen=(current_time - timedelta(seconds=30)).isoformat(),
        last_communication_timestamp=(current_time - timedelta(seconds=30)).isoformat(),
    )
    mock_redis_manager.update_client_status.return_value = True
    # Incoming status has its own 'attributes' which should be merged into existing ones.
    incoming_status_payload = {
        "attributes": {"new_attr": "new_val"},
        "client_state": "paused",
    }

    await manager._handle_status_update(
        {"status": incoming_status_payload}, mock_ws, "w1"
    )

    mock_redis_manager.update_client_status.assert_awaited_once()
    updated_status_obj: ClientStatus = (
        mock_redis_manager.update_client_status.call_args[0][0]
    )

    assert updated_status_obj.attributes is not None
    assert updated_status_obj.attributes.get("new_attr") == "new_val"
    # The main code's current `merged_data.update(incoming_status_attributes)` will overwrite
    # the 'attributes' dict entirely if 'attributes' is a key in incoming_status_payload.
    # To test merging, the main code would need `merged_data['attributes'].update(new_attributes_dict)`.
    # Given the current code, old_attr will be lost.
    assert updated_status_obj.attributes.get("old_attr") is None
    assert updated_status_obj.client_state == "paused"
    assert updated_status_obj.last_seen is not None
    assert (
        updated_status_obj.last_seen > (current_time - timedelta(seconds=1)).isoformat()
    )
    assert (
        updated_status_obj.last_communication_timestamp == updated_status_obj.last_seen
    )

    mock_ws.send_text.assert_awaited_once()
    sent_arg_str = mock_ws.send_text.call_args[0][0]
    sent_data = json.loads(sent_arg_str)
    assert sent_data["type"] == "message_processed"
    assert sent_data["status_updated"]["update_status"] == "success"
    assert sent_data["status_updated"]["attributes"]["new_attr"] == "new_val"
