from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from src.server import config  # Import server config


# Base message structure
class WebSocketMessageBase(BaseModel):
    """Base class for all WebSocket messages."""

    pass


# === CLIENT-TO-SERVER MESSAGES ===


class ClientRegistrationMessage(WebSocketMessageBase):
    """Initial registration message sent by clients (both worker and frontend)."""

    client_id: str
    status: dict[str, Any]


class WorkerStatusUpdateMessage(WebSocketMessageBase):
    """Status update message sent by worker clients."""

    client_id: str
    status: dict[str, Any]


class ControlMessage(WebSocketMessageBase):
    """Control command message sent by frontend clients."""

    type: Literal["control"] = "control"
    action: Literal["pause", "resume", "disconnect"]
    target_client_id: str
    message_id: str | None = None


class ChatMessage(WebSocketMessageBase):
    """Chat message sent by frontend clients."""

    type: Literal["chat"] = "chat"
    client_id: str
    message: str
    timestamp: str | None = None

    def model_post_init(self, __context: dict[str, Any] | None = None) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC).isoformat()


# === SERVER-TO-CLIENT MESSAGES ===


class AllClientsUpdateMessage(WebSocketMessageBase):
    """Sent to frontends with all client statuses."""

    type: Literal["all_clients_update"] = "all_clients_update"
    data: dict[str, Any]


class ClientStatusUpdateMessage(WebSocketMessageBase):
    """Broadcast message about individual client status changes."""

    type: Literal["client_status_update"] = "client_status_update"
    client_id: str
    status: dict[str, Any]
    timestamp: str | None = None
    originating_client_id: str | None = None


class RegistrationCompleteMessage(WebSocketMessageBase):
    """Acknowledgment of successful client registration."""

    type: Literal["registration_complete"] = "registration_complete"
    client_id: str
    redis_status: str


class MessageProcessedMessage(WebSocketMessageBase):
    """Acknowledgment of processed worker status update."""

    type: Literal["message_processed"] = "message_processed"
    client_id: str
    status_updated: dict[str, Any]
    redis_status: str


class ControlResponseMessage(WebSocketMessageBase):
    """Response to control commands."""

    type: Literal["control_response"] = "control_response"
    action: str
    target_client_id: str
    message_id: str | None = None
    status: str
    message: str
    client_id: str | None = None
    redis_status: str | None = None


class ChatAckMessage(WebSocketMessageBase):
    """Acknowledgment of received chat message."""

    type: Literal["chat_ack"] = "chat_ack"
    client_id: str
    original_message: str
    message_id: str | None = None
    timestamp: str
    redis_status: str


class ClientDisconnectedMessage(WebSocketMessageBase):
    """Notification that a client has disconnected."""

    type: Literal["client_disconnected"] = "client_disconnected"
    client_id: str


class ErrorMessage(WebSocketMessageBase):
    """Error message."""

    error: str


class MessageReceiptUnknownMessage(WebSocketMessageBase):
    """Response for unknown/unhandled message types."""

    type: Literal["message_receipt_unknown"] = "message_receipt_unknown"
    original_message: dict[str, Any]
    info: str


# === COMMAND MESSAGES (Server-to-Worker) ===


class CommandMessage(WebSocketMessageBase):
    """Command sent from server to worker clients."""

    command: Literal["pause", "resume", "disconnect"]


# === COMMON STATUS STRUCTURES ===


class ClientStatus(BaseModel):
    """Standard client status structure, now a plain Pydantic model."""

    client_id: str
    client_role: Literal["frontend", "worker"]
    client_type: str | None = None
    client_name: str | None = None
    client_state: (
        Literal["initializing", "running", "paused", "offline", "dormant"] | None
    ) = None
    connected: Literal["true", "false"] | None = None
    connect_time: str | None = None
    disconnect_time: str | None = None
    last_seen: str | None = None
    last_communication_timestamp: str | None = None
    last_heartbeat_timestamp: str | None = None
    status_detail: str | None = None
    timestamp: str | None = None
    cpu_usage: float | None = None
    memory_usage: float | None = None
    redis_status: Literal["connected", "unavailable", "unknown"] | None = None
    attributes: dict[str, Any] | None = None
    hostname: str | None = None
    ip_address: str | None = None

    @classmethod
    def enrich_from_registration(
        cls,
        registration_msg: "ClientRegistrationMessage",
    ) -> "ClientStatus":
        """Enrich a ClientRegistrationMessage to a full ClientStatus instance."""
        current_time_iso = datetime.now(UTC).isoformat()

        enriched_attributes = (
            registration_msg.status.copy() if registration_msg.status else {}
        )

        enriched_attributes["client_id"] = registration_msg.client_id

        role_value_from_msg = enriched_attributes.get(config.STATUS_KEY_CLIENT_ROLE)
        enriched_attributes["client_role"] = role_value_from_msg
        if (
            config.STATUS_KEY_CLIENT_ROLE != "client_role"
            and config.STATUS_KEY_CLIENT_ROLE in enriched_attributes
        ):
            enriched_attributes.pop(config.STATUS_KEY_CLIENT_ROLE)

        enriched_attributes["connected"] = config.STATUS_VALUE_CONNECTED
        enriched_attributes["connect_time"] = current_time_iso
        enriched_attributes["last_seen"] = current_time_iso
        enriched_attributes["last_communication_timestamp"] = current_time_iso
        if enriched_attributes.get("client_role") == "frontend":
            enriched_attributes["last_heartbeat_timestamp"] = current_time_iso
        enriched_attributes.pop(config.STATUS_KEY_DISCONNECT_TIME, None)

        enriched_attributes.setdefault("attributes", None)
        enriched_attributes.setdefault("hostname", None)
        enriched_attributes.setdefault("ip_address", None)

        return cls.model_validate(enriched_attributes)


class AllClientStatuses(BaseModel):
    """Container for all client statuses with metadata."""

    clients: dict[str, ClientStatus]
    total_count: int
    redis_status: str
    timestamp: str

    @classmethod
    def create(
        cls, clients_dict: dict[str, ClientStatus], redis_status: str
    ) -> "AllClientStatuses":
        """Factory method to create AllClientStatuses with automatic metadata."""
        return cls(
            clients=clients_dict,
            total_count=len(clients_dict),
            redis_status=redis_status,
            timestamp=datetime.now().isoformat(),
        )


# === TYPE UNIONS ===

# Messages that can be sent from client to server
ClientMessage = (
    ClientRegistrationMessage
    | WorkerStatusUpdateMessage
    | ControlMessage
    | ChatMessage
)

# Messages that can be sent from server to client
ServerMessage = (
    AllClientsUpdateMessage
    | ClientStatusUpdateMessage
    | RegistrationCompleteMessage
    | MessageProcessedMessage
    | ControlResponseMessage
    | ChatAckMessage
    | ClientDisconnectedMessage
    | ErrorMessage
    | MessageReceiptUnknownMessage
    | CommandMessage
)

# All WebSocket messages
WebSocketMessage = ClientMessage | ServerMessage
