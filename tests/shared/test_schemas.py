"""Tests for WebSocket message schemas."""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.shared.schemas.websocket import (
    ChatMessage,
    ChatAckMessage,
    ClientMessage,
    ServerMessage,
)


class TestChatMessage:
    """Tests for ChatMessage schema."""

    def test_chat_message_valid(self):
        """Test valid chat message creation."""
        message = ChatMessage(
            client_id="frontend1",
            message="Hello world",
            timestamp="2024-01-01T10:00:00Z"
        )
        
        assert message.type == "chat"
        assert message.client_id == "frontend1"
        assert message.message == "Hello world"
        assert message.timestamp == "2024-01-01T10:00:00Z"

    def test_chat_message_auto_timestamp(self):
        """Test automatic timestamp generation."""
        with patch('src.shared.schemas.websocket.datetime') as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = "2024-01-01T12:00:00Z"
            mock_datetime.UTC = UTC
            
            message = ChatMessage(
                client_id="frontend1",
                message="Hello world"
            )
            
            assert message.timestamp == "2024-01-01T12:00:00Z"

    def test_chat_message_missing_required_fields(self):
        """Test validation error when required fields are missing."""
        with pytest.raises(ValidationError) as exc_info:
            ChatMessage(message="Hello world")  # Missing client_id
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "missing"
        assert errors[0]["loc"] == ("client_id",)

        with pytest.raises(ValidationError) as exc_info:
            ChatMessage(client_id="frontend1")  # Missing message
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "missing"
        assert errors[0]["loc"] == ("message",)

    def test_chat_message_empty_message_allowed(self):
        """Test that empty message content is allowed."""
        message = ChatMessage(
            client_id="frontend1",
            message=""
        )
        
        assert message.message == ""

    def test_chat_message_serialization(self):
        """Test message serialization to JSON."""
        message = ChatMessage(
            client_id="frontend1",
            message="Hello world",
            timestamp="2024-01-01T10:00:00Z"
        )
        
        json_str = message.model_dump_json()
        data = json.loads(json_str)
        
        assert data["type"] == "chat"
        assert data["client_id"] == "frontend1"
        assert data["message"] == "Hello world"
        assert data["timestamp"] == "2024-01-01T10:00:00Z"

    def test_chat_message_type_literal(self):
        """Test that type field is correctly set as literal."""
        message = ChatMessage(
            client_id="frontend1",
            message="Hello world"
        )
        
        # Type should be automatically set to "chat"
        assert message.type == "chat"
        
        # Should not be able to change it via model_dump and reload
        data = message.model_dump()
        data["type"] = "other"
        
        # Creating new instance should fail with validation error
        with pytest.raises(ValidationError) as exc_info:
            ChatMessage.model_validate(data)
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "literal_error"
        assert errors[0]["loc"] == ("type",)


class TestChatAckMessage:
    """Tests for ChatAckMessage schema."""

    def test_chat_ack_message_valid(self):
        """Test valid chat acknowledgment message creation."""
        message = ChatAckMessage(
            client_id="frontend1",
            original_message="Hello world",
            message_id="msg123",
            timestamp="2024-01-01T10:00:00Z",
            redis_status="connected"
        )
        
        assert message.type == "chat_ack"
        assert message.client_id == "frontend1"
        assert message.original_message == "Hello world"
        assert message.message_id == "msg123"
        assert message.timestamp == "2024-01-01T10:00:00Z"
        assert message.redis_status == "connected"

    def test_chat_ack_message_optional_message_id(self):
        """Test that message_id is optional."""
        message = ChatAckMessage(
            client_id="frontend1",
            original_message="Hello world",
            timestamp="2024-01-01T10:00:00Z",
            redis_status="connected"
        )
        
        assert message.message_id is None

    def test_chat_ack_message_missing_required_fields(self):
        """Test validation error when required fields are missing."""
        with pytest.raises(ValidationError) as exc_info:
            ChatAckMessage(
                original_message="Hello world",
                timestamp="2024-01-01T10:00:00Z",
                redis_status="connected"
            )  # Missing client_id
        
        errors = exc_info.value.errors()
        assert any(error["loc"] == ("client_id",) for error in errors)

    def test_chat_ack_message_serialization(self):
        """Test message serialization to JSON."""
        message = ChatAckMessage(
            client_id="frontend1",
            original_message="Hello world",
            message_id="msg123",
            timestamp="2024-01-01T10:00:00Z",
            redis_status="connected"
        )
        
        json_str = message.model_dump_json()
        data = json.loads(json_str)
        
        assert data["type"] == "chat_ack"
        assert data["client_id"] == "frontend1"
        assert data["original_message"] == "Hello world"
        assert data["message_id"] == "msg123"
        assert data["timestamp"] == "2024-01-01T10:00:00Z"
        assert data["redis_status"] == "connected"


class TestMessageUnions:
    """Tests for message type unions."""

    def test_client_message_includes_chat(self):
        """Test that ChatMessage is included in ClientMessage union."""
        chat_message = ChatMessage(
            client_id="frontend1",
            message="Hello world"
        )
        
        # Should be able to use as ClientMessage
        client_msg: ClientMessage = chat_message
        assert client_msg.type == "chat"

    def test_server_message_includes_chat_ack(self):
        """Test that ChatAckMessage is included in ServerMessage union."""
        chat_ack = ChatAckMessage(
            client_id="frontend1",
            original_message="Hello world",
            timestamp="2024-01-01T10:00:00Z",
            redis_status="connected"
        )
        
        # Should be able to use as ServerMessage
        server_msg: ServerMessage = chat_ack
        assert server_msg.type == "chat_ack"

    def test_chat_message_from_dict(self):
        """Test creating ChatMessage from dictionary."""
        data = {
            "type": "chat",
            "client_id": "frontend1",
            "message": "Hello world",
            "timestamp": "2024-01-01T10:00:00Z"
        }
        
        message = ChatMessage.model_validate(data)
        assert message.type == "chat"
        assert message.client_id == "frontend1"
        assert message.message == "Hello world"

    def test_chat_ack_from_dict(self):
        """Test creating ChatAckMessage from dictionary."""
        data = {
            "type": "chat_ack",
            "client_id": "frontend1",
            "original_message": "Hello world",
            "timestamp": "2024-01-01T10:00:00Z",
            "redis_status": "connected"
        }
        
        message = ChatAckMessage.model_validate(data)
        assert message.type == "chat_ack"
        assert message.client_id == "frontend1"
        assert message.original_message == "Hello world" 