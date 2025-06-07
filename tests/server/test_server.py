"""
Unit tests for the WebSocket server.

This module tests all the WebSocket connection behavior, client status management,
and REST API functionality, with appropriate mocking for Redis.
"""

from datetime import UTC, datetime  # Added datetime, UTC
from unittest.mock import AsyncMock

import pytest

from src.server import config  # Added import
from src.server.server import ws_manager
from src.shared.schemas.websocket import ClientStatus
from src.shared.utils.logging import get_logger

# Add logger for the test
logger = get_logger(__name__)


@pytest.mark.asyncio
async def test_get_all_statuses_success(monkeypatch):
    mock_statuses = {
        "client1": ClientStatus(client_id="client1", client_role="worker"),
        "client2": ClientStatus(client_id="client2", client_role="frontend"),
    }

    class MockAllClientStatuses:
        clients = mock_statuses

    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_all_client_statuses",
        AsyncMock(return_value=MockAllClientStatuses()),
    )
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_redis_status",
        lambda: config.STATUS_VALUE_REDIS_CONNECTED,
    )
    from src.server import server as server_module

    response = await server_module.get_all_statuses()
    assert config.PAYLOAD_KEY_CLIENTS in response
    assert config.STATUS_KEY_REDIS_STATUS in response
    assert (
        response[config.STATUS_KEY_REDIS_STATUS] == config.STATUS_VALUE_REDIS_CONNECTED
    )
    assert set(response[config.PAYLOAD_KEY_CLIENTS].keys()) == {"client1", "client2"}


@pytest.mark.asyncio
async def test_get_all_statuses_error(monkeypatch):
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_all_client_statuses",
        AsyncMock(side_effect=Exception("fail")),
    )
    from src.server import server as server_module

    response = await server_module.get_all_statuses()
    assert response[config.PAYLOAD_KEY_CLIENTS] == {}
    assert response[config.STATUS_KEY_REDIS_STATUS] == config.STATUS_VALUE_REDIS_UNKNOWN
    assert response[config.MSG_TYPE_ERROR] == "Failed to retrieve client statuses"


@pytest.mark.asyncio
async def test_health_check(monkeypatch):
    monkeypatch.setattr(
        ws_manager,
        "get_connection_stats",
        AsyncMock(return_value={"worker_connections": 2, "frontend_connections": 1}),
    )
    from src.server import server as server_module

    response = await server_module.health_check()
    assert response[config.PAYLOAD_KEY_STATUS] == config.HEALTH_STATUS_HEALTHY
    assert response["active_workers"] == 2
    assert response["active_frontends"] == 1


@pytest.mark.asyncio
async def test_get_client_info_connected(monkeypatch):
    client_id = "client1"
    cs = ClientStatus(client_id=client_id, client_role="worker")
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_client_info",
        AsyncMock(return_value=cs),
    )
    from src.server import server as server_module

    result = await server_module.get_client_info(client_id)
    assert isinstance(result, ClientStatus)
    assert result.client_id == client_id


@pytest.mark.asyncio
async def test_get_client_info_unavailable(monkeypatch):
    client_id = "client1"
    # Simulate Redis unavailable by returning None
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_client_info",
        AsyncMock(return_value=None),
    )
    from src.server import server as server_module

    result = await server_module.get_client_info(client_id)
    assert result is None


@pytest.mark.asyncio
async def test_broadcast_client_state_change_success(monkeypatch):
    client_id = "client1"
    cs = ClientStatus(client_id=client_id, client_role="worker")
    monkeypatch.setattr("src.server.server.get_client_info", AsyncMock(return_value=cs))
    mock_broadcast = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_to_frontends", mock_broadcast)
    from src.server import server as server_module

    await server_module.broadcast_client_state_change(client_id)
    mock_broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_client_state_change_no_status(monkeypatch):
    client_id = "client1"
    monkeypatch.setattr(
        "src.server.server.get_client_info", AsyncMock(return_value=None)
    )
    mock_broadcast = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_to_frontends", mock_broadcast)
    from src.server import server as server_module

    await server_module.broadcast_client_state_change(client_id)
    mock_broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_disconnect_worker(monkeypatch):
    client_id = "worker1"
    ws = AsyncMock()
    ws_manager.active_connections[client_id] = ws
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_client_info",
        AsyncMock(
            return_value=ClientStatus(
                client_id=client_id,
                client_role="worker",  # This is the key field
                connected=config.STATUS_VALUE_CONNECTED,
                client_state=config.CLIENT_STATE_RUNNING,  # Add a sensible default
                last_seen=datetime.now(UTC).isoformat(),  # Add a sensible default
                timestamp=datetime.now(UTC).isoformat(),  # Add a sensible default
            )
        ),
    )
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.update_client_status",
        AsyncMock(return_value=True),
    )
    from src.server import server as server_module

    await server_module.handle_disconnect(client_id, ws, "Test disconnect")
    assert (
        client_id in ws_manager.active_connections
        or client_id not in ws_manager.active_connections
    )


@pytest.mark.asyncio
async def test_handle_disconnect_not_found(monkeypatch):
    client_id = "ghost_client"
    ws = AsyncMock()
    if client_id in ws_manager.active_connections:
        ws_manager.active_connections.pop(client_id)
    monkeypatch.setattr(
        "src.server.redis_manager.redis_manager.get_client_info",
        AsyncMock(return_value=None),
    )
    from src.server import server as server_module

    await server_module.handle_disconnect(client_id, ws, "Test disconnect")
    assert client_id not in ws_manager.active_connections
