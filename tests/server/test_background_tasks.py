import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server import background_tasks, config
from src.shared.schemas.websocket import ClientStatus


def test_periodic_client_cleanup_task_handles_no_clients(monkeypatch):
    ws_manager = MagicMock()
    monkeypatch.setattr(
        background_tasks.redis_manager,
        "get_all_client_statuses",
        AsyncMock(return_value=None),
    )

    # Run one iteration only
    async def fake_sleep(_):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(background_tasks.periodic_client_cleanup_task(ws_manager))


def test_handle_frontend_client_cleanup_removes_stale(monkeypatch):
    ws_manager = MagicMock()
    # Ensure the mock websocket and its close method are AsyncMocks
    mock_ws_frontend = AsyncMock()
    mock_ws_frontend.close = AsyncMock()
    ws_manager.active_connections = {"frontend1": mock_ws_frontend}
    now = datetime.now(UTC)
    old_heartbeat = (
        now
        - timedelta(seconds=background_tasks.FRONTEND_HEARTBEAT_TIMEOUT_SECONDS + 10)
    ).isoformat()
    client_status = ClientStatus(
        client_id="frontend1",
        client_role="frontend",
        last_heartbeat_timestamp=old_heartbeat,
    )
    client_status.connected = config.STATUS_VALUE_CONNECTED
    client_status.client_state = config.CLIENT_STATE_RUNNING
    monkeypatch.setattr(
        background_tasks.redis_manager,
        "update_client_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        background_tasks.redis_manager,
        "delete_client_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    asyncio.run(
        background_tasks._handle_frontend_client_cleanup(
            "frontend1", client_status, now, ws_manager, ws_manager.active_connections
        )
    )
    background_tasks.redis_manager.update_client_status.assert_called()
    background_tasks.redis_manager.delete_client_status.assert_called()


def test_handle_worker_client_cleanup_dormant_and_disconnect(monkeypatch):
    ws_manager = MagicMock()
    # Ensure the mock websocket and its close method are AsyncMocks
    mock_ws_worker = AsyncMock()
    mock_ws_worker.close = AsyncMock()
    ws_manager.active_connections = {"worker1": mock_ws_worker}
    now = datetime.now(UTC)
    # Dormant
    last_comm_dormant = (
        now - timedelta(minutes=background_tasks.PYTHON_CLIENT_DORMANT_MINUTES + 1)
    ).isoformat()
    client_status = ClientStatus(
        client_id="worker1",
        client_role="worker",
        last_communication_timestamp=last_comm_dormant,
        client_state=config.CLIENT_STATE_RUNNING,
    )
    client_status.connected = config.STATUS_VALUE_CONNECTED
    monkeypatch.setattr(
        background_tasks.redis_manager,
        "update_client_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        background_tasks.redis_manager,
        "delete_client_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    asyncio.run(
        background_tasks._handle_worker_client_cleanup(
            "worker1", client_status, now, ws_manager, ws_manager.active_connections
        )
    )
    background_tasks.redis_manager.update_client_status.assert_called()
    # Disconnect
    last_comm_disconnect = (
        now - timedelta(minutes=background_tasks.PYTHON_CLIENT_DISCONNECT_MINUTES + 1)
    ).isoformat()
    client_status2 = ClientStatus(
        client_id="worker1",
        client_role="worker",
        last_communication_timestamp=last_comm_disconnect,
        client_state=config.CLIENT_STATE_RUNNING,
    )
    client_status2.connected = config.STATUS_VALUE_CONNECTED
    asyncio.run(
        background_tasks._handle_worker_client_cleanup(
            "worker1", client_status2, now, ws_manager, ws_manager.active_connections
        )
    )
    background_tasks.redis_manager.update_client_status.assert_called()
    background_tasks.redis_manager.delete_client_status.assert_called()
