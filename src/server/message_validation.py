import json
from datetime import UTC, datetime

from fastapi import WebSocket
from pydantic import ValidationError

from src.server import config
from src.shared.schemas.websocket import ClientRegistrationMessage, ClientStatus
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)


# Validate the registration message using Pydantic schema
async def validate_registration_message(
    initial_data: str, websocket: WebSocket
) -> ClientRegistrationMessage | None:
    try:
        registration_msg = ClientRegistrationMessage.model_validate_json(initial_data)
        logger.info(
            f"Registration message validated successfully for client {registration_msg.client_id}"
        )
        return registration_msg
    except json.JSONDecodeError:
        error_msg = config.ERROR_MSG_INVALID_JSON_REGISTRATION
        logger.warning(
            f"JSON parsing failed during registration: {error_msg}",
            data=initial_data,
        )
        await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
        await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD)
        return
    except ValidationError as e:
        # Check if this is a JSON parsing error (indicated by error type 'json_invalid')
        if any(error.get("type") == "json_invalid" for error in e.errors()):
            error_msg = config.ERROR_MSG_INVALID_JSON_REGISTRATION
        else:
            error_msg = f"Invalid registration message format: {e}"
        logger.warning(
            f"Registration validation failed: {error_msg}", data=initial_data
        )
        await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
        await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD)
        return
    except Exception as e:
        error_msg = f"Unexpected error during registration validation: {e}"
        logger.error(f"Registration validation error: {error_msg}", exc_info=True)
        await websocket.send_text(json.dumps({config.MSG_TYPE_ERROR: error_msg}))
        await websocket.close(code=config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR)
        return


def validate_client_status_json(json_data: str | bytes) -> ClientStatus | None:
    try:
        return ClientStatus.model_validate_json(json_data)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(
            f"Failed to parse/validate ClientStatus from JSON: {e}", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(f"Unexpected error parsing ClientStatus: {e}", exc_info=True)
        return None


def merge_and_validate_status_update(
    existing_status: ClientStatus, incoming_status: dict, client_id: str
) -> ClientStatus | None:
    merged_data = existing_status.model_dump()
    merged_data.update(incoming_status)
    now_iso = datetime.now(UTC).isoformat()
    merged_data["client_id"] = client_id
    merged_data[config.STATUS_KEY_LAST_SEEN] = now_iso
    merged_data["last_communication_timestamp"] = now_iso
    merged_data["connected"] = config.STATUS_VALUE_CONNECTED
    current_server_state = merged_data.get(config.STATUS_KEY_CLIENT_STATE)
    incoming_client_state = incoming_status.get(config.STATUS_KEY_CLIENT_STATE)
    if not incoming_client_state:
        if current_server_state == config.CLIENT_STATE_DORMANT:
            merged_data[config.STATUS_KEY_CLIENT_STATE] = config.CLIENT_STATE_RUNNING
            logger.info(
                f"Client {client_id} awakened from dormant to running due to status update."
            )
        elif (
            current_server_state == config.CLIENT_STATE_INITIALIZING
            or current_server_state is None
        ):
            merged_data[config.STATUS_KEY_CLIENT_STATE] = config.CLIENT_STATE_RUNNING
            logger.info(
                f"Client {client_id} moved from '{current_server_state}' to running due to status update."
            )
    try:
        return ClientStatus.model_validate(merged_data)
    except ValidationError as ve:
        logger.error(
            f"Validation error merging status update for {client_id}: {ve}",
            exc_info=True,
        )
        return None
    except Exception as e:
        logger.error(
            f"Unexpected error merging status update for {client_id}: {e}",
            exc_info=True,
        )
        return None


def validate_heartbeat_update(
    existing_status: ClientStatus, client_id: str
) -> ClientStatus | None:
    now_iso = datetime.now(UTC).isoformat()
    update_data = {"last_heartbeat_timestamp": now_iso, "last_seen": now_iso}
    updated_status_data = existing_status.model_dump()
    updated_status_data.update(update_data)
    if "client_id" not in updated_status_data:
        updated_status_data["client_id"] = client_id
    try:
        return ClientStatus.model_validate(updated_status_data)
    except ValidationError as ve:
        logger.error(
            f"Validation error updating heartbeat for {client_id}: {ve}", exc_info=True
        )
        return None
    except Exception as e:
        logger.error(
            f"Unexpected error updating heartbeat for {client_id}: {e}", exc_info=True
        )
        return None
