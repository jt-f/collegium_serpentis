from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.websocket_manager import ConnectionManager
from src.shared.schemas.websocket import ClientStatus


@pytest.fixture
def connection_manager():
    return ConnectionManager()


def test_initialization(connection_manager):
    assert isinstance(connection_manager.active_connections, dict)
    assert isinstance(connection_manager.client_contexts, dict)


def test_add_and_remove_connection(connection_manager):
    ws = MagicMock()
    connection_manager.active_connections["client1"] = ws
    assert "client1" in connection_manager.active_connections
    connection_manager.disconnect("client1")
    assert "client1" not in connection_manager.active_connections


@pytest.mark.asyncio
async def test_send_message_to_client(connection_manager):
    ws = AsyncMock()
    connection_manager.active_connections["client1"] = ws
    message = MagicMock()
    message.model_dump_json.return_value = '{"msg": "hi"}'
    await connection_manager.send_message(message, "client1")
    assert ws.send_text.called


@pytest.mark.asyncio
async def test_broadcast_json(connection_manager):
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    connection_manager.active_connections["c1"] = ws1
    connection_manager.active_connections["c2"] = ws2
    message = MagicMock()
    message.model_dump_json.return_value = '{"msg": "hi"}'
    await connection_manager.broadcast_json(message)
    assert ws1.send_text.called
    assert ws2.send_text.called


@pytest.mark.asyncio
async def test_handle_registration_enrich(monkeypatch, connection_manager):
    ws = AsyncMock()
    # Simulate registration message
    registration_msg = MagicMock()
    registration_msg.status = {"client_role": "worker"}
    registration_msg.client_id = "client42"
    monkeypatch.setattr(
        "src.server.websocket_manager.validate_registration_message",
        AsyncMock(return_value=registration_msg),
    )
    monkeypatch.setattr(
        "src.shared.schemas.websocket.ClientStatus.enrich_from_registration",
        lambda msg: ClientStatus(client_id=msg.client_id, client_role="worker"),
    )
    ws.receive_text = AsyncMock(
        return_value='{"client_id": "client42", "status": {"client_role": "worker"}}'
    )
    result = await connection_manager._handle_registration(ws)
    assert isinstance(result, ClientStatus)
    assert result.client_id == "client42"


def test_disconnect(connection_manager):
    ws = MagicMock()
    connection_manager.active_connections["client1"] = ws
    connection_manager.disconnect("client1")
    assert "client1" not in connection_manager.active_connections
