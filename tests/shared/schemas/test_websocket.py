from datetime import UTC, datetime

from src.shared.schemas.websocket import (
    AllClientStatuses,
    AllClientsUpdateMessage,
    ClientRegistrationMessage,
    ClientStatus,
    ClientStatusUpdateMessage,
    RegistrationCompleteMessage,
)


def test_client_status_creation():
    cs = ClientStatus(client_id="c1", client_role="worker")
    assert cs.client_id == "c1"
    assert cs.client_role == "worker"


def test_enrich_from_registration():
    reg = ClientRegistrationMessage(client_id="c2", status={"client_role": "frontend"})
    cs = ClientStatus.enrich_from_registration(reg)
    assert cs.client_id == "c2"
    assert cs.client_role == "frontend"
    assert cs.connected == "true"
    assert cs.connect_time is not None
    assert cs.last_seen is not None


def test_all_client_statuses_create():
    cs1 = ClientStatus(client_id="c1", client_role="worker")
    cs2 = ClientStatus(client_id="c2", client_role="frontend")
    all_statuses = AllClientStatuses.create(
        {"c1": cs1, "c2": cs2}, redis_status="connected"
    )
    assert all_statuses.total_count == 2
    assert all_statuses.redis_status == "connected"
    assert "c1" in all_statuses.clients
    assert "c2" in all_statuses.clients
    assert all_statuses.timestamp is not None


def test_all_clients_update_message():
    msg = AllClientsUpdateMessage(
        data={
            "clients": {},
            "redis_status": "connected",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    assert msg.type == "all_clients_update"
    assert "clients" in msg.data
    assert "redis_status" in msg.data


def test_client_status_update_message():
    cs = ClientStatus(client_id="c1", client_role="worker")
    msg = ClientStatusUpdateMessage(client_id="c1", status=cs.model_dump())
    assert msg.type == "client_status_update"
    assert msg.client_id == "c1"
    assert isinstance(msg.status, dict)


def test_registration_complete_message():
    msg = RegistrationCompleteMessage(client_id="c1", redis_status="connected")
    assert msg.type == "registration_complete"
    assert msg.client_id == "c1"
    assert msg.redis_status == "connected"
