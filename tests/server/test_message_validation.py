import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket
from pydantic import ValidationError

from src.server import config
from src.server.message_validation import (
    merge_and_validate_status_update,
    validate_client_status_json,
    validate_heartbeat_update,
    validate_registration_message,
)
from src.shared.schemas.websocket import ClientRegistrationMessage, ClientStatus


@pytest.mark.asyncio
async def test_validate_registration_message_valid():
    websocket = AsyncMock(spec=WebSocket)
    reg = ClientRegistrationMessage(client_id="c1", status={"client_role": "worker"})
    data = reg.model_dump_json()
    result = await validate_registration_message(data, websocket)
    assert isinstance(result, ClientRegistrationMessage)
    assert result.client_id == "c1"


@pytest.mark.asyncio
async def test_validate_registration_message_invalid_json():
    websocket = AsyncMock(spec=WebSocket)
    data = "{bad json"
    result = await validate_registration_message(data, websocket)
    assert result is None
    websocket.send_text.assert_awaited()
    websocket.close.assert_awaited()


@pytest.mark.asyncio
async def test_validate_registration_message_invalid_schema():
    websocket = AsyncMock(spec=WebSocket)
    # Missing client_id
    data = json.dumps({"status": {"client_role": "worker"}})
    result = await validate_registration_message(data, websocket)
    assert result is None
    websocket.send_text.assert_awaited()
    websocket.close.assert_awaited()


def test_validate_client_status_json_valid():
    cs = ClientStatus(client_id="c1", client_role="worker")
    data = cs.model_dump_json()
    result = validate_client_status_json(data)
    assert isinstance(result, ClientStatus)
    assert result.client_id == "c1"


def test_validate_client_status_json_invalid():
    # Missing client_id
    data = json.dumps({"client_role": "worker"})
    result = validate_client_status_json(data)
    assert result is None


def test_validate_client_status_json_malformed():
    data = "{bad json"
    result = validate_client_status_json(data)
    assert result is None


def test_merge_and_validate_status_update_basic():
    cs = ClientStatus(client_id="c1", client_role="worker", client_state="dormant")
    incoming = {"temp": "30C"}
    merged = merge_and_validate_status_update(cs, incoming, "c1")
    assert isinstance(merged, ClientStatus)
    # Extra fields like 'temp' are not preserved unless the model allows extras
    assert merged.client_id == "c1"
    assert merged.client_state == config.CLIENT_STATE_RUNNING  # dormant -> running


def test_merge_and_validate_status_update_initializing():
    cs = ClientStatus(client_id="c1", client_role="worker", client_state="initializing")
    incoming = {"foo": "bar"}
    merged = merge_and_validate_status_update(cs, incoming, "c1")
    assert isinstance(merged, ClientStatus)
    assert merged.client_state == config.CLIENT_STATE_RUNNING


def test_merge_and_validate_status_update_with_client_state():
    cs = ClientStatus(client_id="c1", client_role="worker", client_state="dormant")
    incoming = {"client_state": "paused"}
    merged = merge_and_validate_status_update(cs, incoming, "c1")
    assert isinstance(merged, ClientStatus)
    assert merged.client_state == "paused"


def test_merge_and_validate_status_update_validation_error(monkeypatch):
    cs = ClientStatus(client_id="c1", client_role="worker")
    # Use a field that will cause validation to fail (e.g., wrong type for client_role)
    incoming = {"client_role": 12345}
    merged = merge_and_validate_status_update(cs, incoming, "c1")
    assert merged is None


def test_validate_heartbeat_update_valid():
    cs = ClientStatus(client_id="c1", client_role="frontend")
    updated = validate_heartbeat_update(cs, "c1")
    assert isinstance(updated, ClientStatus)
    assert updated.client_id == "c1"
    assert hasattr(updated, "last_heartbeat_timestamp")
    assert hasattr(updated, "last_seen")


def test_validate_heartbeat_update_validation_error(monkeypatch):
    cs = ClientStatus(client_id="c1", client_role="frontend")
    # Patch model_validate to raise error
    monkeypatch.setattr(
        type(cs),
        "model_validate",
        staticmethod(
            lambda data: (_ for _ in ()).throw(ValidationError([], ClientStatus))
        ),
    )
    updated = validate_heartbeat_update(cs, "c1")
    assert updated is None
