import pytest
from pydantic import ValidationError
import uuid
from src.shared.models.commands import CommandName, CommandPayload, FrontendCommand

# Test CommandName Enum
def test_command_name_enum_values():
    assert CommandName.PAUSE.value == "PAUSE"
    assert CommandName.RESUME.value == "RESUME"
    assert CommandName.DISCONNECT.value == "DISCONNECT"

# Tests for CommandPayload Success Cases
def test_command_payload_all_fields_valid():
    client_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.PAUSE,
        message_id=message_id,
        data={"reason": "user_request"}
    )
    assert payload.client_id == client_id
    assert payload.command_name == CommandName.PAUSE
    assert payload.message_id == message_id
    assert payload.data == {"reason": "user_request"}

def test_command_payload_auto_generates_message_id():
    client_id = str(uuid.uuid4())
    payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.RESUME
    )
    assert payload.client_id == client_id
    assert payload.command_name == CommandName.RESUME
    assert isinstance(uuid.UUID(payload.message_id, version=4), uuid.UUID) # Check if valid UUIDv4
    assert payload.data is None

def test_command_payload_no_data():
    client_id = str(uuid.uuid4())
    payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.DISCONNECT
    )
    assert payload.client_id == client_id
    assert payload.command_name == CommandName.DISCONNECT
    assert payload.data is None

def test_command_payload_with_valid_data():
    client_id = str(uuid.uuid4())
    sample_data = {"key": "value", "number": 123}
    payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.PAUSE,
        data=sample_data
    )
    assert payload.client_id == client_id
    assert payload.command_name == CommandName.PAUSE
    assert payload.data == sample_data

# Tests for CommandPayload Validation Errors
def test_command_payload_missing_client_id():
    with pytest.raises(ValidationError) as excinfo:
        CommandPayload(command_name=CommandName.PAUSE)
    assert "client_id" in str(excinfo.value).lower() # check field name in error

def test_command_payload_missing_command_name():
    with pytest.raises(ValidationError) as excinfo:
        CommandPayload(client_id=str(uuid.uuid4()))
    assert "command_name" in str(excinfo.value).lower()

def test_command_payload_invalid_command_name():
    with pytest.raises(ValidationError) as excinfo:
        CommandPayload(client_id=str(uuid.uuid4()), command_name="INVALID_COMMAND")
    assert "command_name" in str(excinfo.value).lower() # Pydantic v2 error messages are more structured

def test_command_payload_invalid_client_id_type():
    with pytest.raises(ValidationError) as excinfo:
        CommandPayload(client_id=12345, command_name=CommandName.PAUSE)
    assert "client_id" in str(excinfo.value).lower()
    assert "string_type" in str(excinfo.value).lower()


def test_command_payload_invalid_data_type():
    # Pydantic v2 is more flexible with dict-like types for Optional[dict].
    # A direct list is a clearer violation for Optional[dict] if not None.
    with pytest.raises(ValidationError) as excinfo:
        CommandPayload(client_id=str(uuid.uuid4()), command_name=CommandName.PAUSE, data=["not", "a", "dict"])
    assert "data" in str(excinfo.value).lower()
    assert "dictionary_type" in str(excinfo.value).lower() or "is_instance_of" in str(excinfo.value).lower()


# Tests for FrontendCommand Success Cases
def test_frontend_command_valid_payload():
    client_id = str(uuid.uuid4())
    cmd_payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.PAUSE
    )
    frontend_cmd = FrontendCommand(payload=cmd_payload)
    assert frontend_cmd.type == "command"
    assert frontend_cmd.payload == cmd_payload
    assert frontend_cmd.payload.client_id == client_id

def test_frontend_command_type_defaults_to_command():
    client_id = str(uuid.uuid4())
    cmd_payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.RESUME
    )
    # Explicitly pass type to ensure it's not just the default behavior of FrontendCommand model
    frontend_cmd = FrontendCommand(payload=cmd_payload, type="command")
    assert frontend_cmd.type == "command"


# Tests for FrontendCommand Validation Errors
def test_frontend_command_missing_payload():
    with pytest.raises(ValidationError) as excinfo:
        FrontendCommand() # Missing payload
    assert "payload" in str(excinfo.value).lower()

def test_frontend_command_invalid_payload_type():
    # Pass a dict that doesn't conform to CommandPayload structure
    with pytest.raises(ValidationError) as excinfo:
        FrontendCommand(payload={"invalid_key": "invalid_value"})
    # Pydantic v2 will try to coerce dict to CommandPayload, error will be about missing fields in CommandPayload
    assert "payload.client_id" in str(excinfo.value).lower() or "payload.command_name" in str(excinfo.value).lower()


def test_frontend_command_invalid_type_field_value():
    client_id = str(uuid.uuid4())
    cmd_payload = CommandPayload(
        client_id=client_id,
        command_name=CommandName.DISCONNECT
    )
    with pytest.raises(ValidationError) as excinfo:
        FrontendCommand(payload=cmd_payload, type="other_type")
    assert "type" in str(excinfo.value).lower()
    assert "literal_error" in str(excinfo.value).lower() # For Literal type mismatch
    assert "'command'" in str(excinfo.value) # Error message should indicate expected literal "command"
