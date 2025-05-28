# tests/client/test_client.py
import asyncio
import importlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.client import client


class TestClientConfiguration:
    """Test client configuration and environment variables."""

    def test_server_url_from_environment(self):
        """Test SERVER_URL is read from environment variable."""
        original_server_url = client.SERVER_URL
        try:
            with patch.dict(os.environ, {"SERVER_URL": "ws://test:8000/ws"}):
                importlib.reload(client)
                assert client.SERVER_URL == "ws://test:8000/ws"
        finally:
            # Environment is restored by with statement exiting
            importlib.reload(client)  # Reload to pick up restored environment
            client.SERVER_URL = original_server_url  # Explicitly restore original value

    def test_default_server_url(self):
        """Test default SERVER_URL when not set in environment."""
        original_server_url = client.SERVER_URL
        # Store and remove SERVER_URL if it exists, to ensure a clean environment for the test
        server_url_env_backup = os.environ.pop("SERVER_URL", None)
        try:
            # Ensure SERVER_URL is not in environ for this part of the test
            assert "SERVER_URL" not in os.environ
            importlib.reload(client)
            assert client.SERVER_URL == "ws://localhost:8000/ws"
        finally:
            # Restore SERVER_URL environment variable if it was backed up
            if server_url_env_backup is not None:
                os.environ["SERVER_URL"] = server_url_env_backup
            else:  # If it wasn't there, ensure it's not there after test
                os.environ.pop("SERVER_URL", None)
            importlib.reload(client)  # Reload to pick up restored environment
            client.SERVER_URL = original_server_url  # Explicitly restore original value


class TestClientUtilityFunctions:
    """Test utility functions."""

    def test_get_current_status_payload(self):
        """Test status payload generation."""
        status = client.get_current_status_payload()
        assert "timestamp" in status
        assert "cpu_usage" in status
        assert "memory_usage" in status
        assert 0 <= status["cpu_usage"] <= 100
        assert 0 <= status["memory_usage"] <= 100

    @pytest.mark.asyncio
    async def test_send_status_message(self):
        """Test sending status messages, including timestamp in status."""
        mock_ws = AsyncMock()

        # Use get_current_status_payload to ensure timestamp is present
        status_payload_base = client.get_current_status_payload()
        # Override specific values for predictable testing
        status_payload = {
            **status_payload_base,
            "cpu_usage": 50,
            "memory_usage": 30,
        }

        await client.send_status_message(mock_ws, status_payload)

        mock_ws.send.assert_awaited_once()
        sent_data_str = mock_ws.send.await_args[0][0]
        sent_data = json.loads(sent_data_str)

        assert "client_id" in sent_data  # Ensure client_id is present
        # Verify the entire status payload is correctly nested
        assert sent_data["status"] == status_payload
        # Check for timestamp within the "status" dictionary
        assert "timestamp" in sent_data["status"]
        # Verify specific overridden values
        assert sent_data["status"]["cpu_usage"] == 50
        assert sent_data["status"]["memory_usage"] == 30


class TestClientMessageHandling:
    """Test message handling functionality."""

    @pytest.mark.asyncio
    async def test_send_status_message(self):
        """Test sending status messages, including timestamp in status."""
        mock_ws = AsyncMock()

        # Use get_current_status_payload to ensure timestamp is present
        status_payload_base = client.get_current_status_payload()
        # Override specific values for predictable testing
        status_payload = {
            **status_payload_base,
            "cpu_usage": 50,
            "memory_usage": 30,
        }

        await client.send_status_message(mock_ws, status_payload)

        mock_ws.send.assert_awaited_once()
        sent_data_str = mock_ws.send.await_args[0][0]
        sent_data = json.loads(sent_data_str)

        assert "client_id" in sent_data  # Ensure client_id is present
        # Verify the entire status payload is correctly nested
        assert sent_data["status"] == status_payload
        # Check for timestamp within the "status" dictionary
        assert "timestamp" in sent_data["status"]
        # Verify specific overridden values
        assert sent_data["status"]["cpu_usage"] == 50
        assert sent_data["status"]["memory_usage"] == 30


class TestCommandListener:
    """Test command message processing."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket connection for command listening."""
        ws = AsyncMock()  # The main websocket object passed to listen_for_commands

        # This is the async iterator object that __aiter__ will return.
        # Its __anext__ method will be configured in each test.
        async_iterator_mock = AsyncMock()

        # __aiter__ itself is a synchronous method that returns an async iterator.
        # So, ws.__aiter__ should be a MagicMock, not AsyncMock.
        ws.__aiter__ = MagicMock(return_value=async_iterator_mock)

        # ws.recv is not directly used by `async for`, but can be set up if other code calls it.
        ws.recv = AsyncMock()

        ws.send = AsyncMock()
        ws.close = AsyncMock()
        # For AsyncMock, __aenter__ and __aexit__ are often not needed if not used with `async with`
        # but good to have if the SUT does use it that way with the object.
        ws.__aenter__ = AsyncMock(return_value=ws)
        ws.__aexit__ = AsyncMock(return_value=None)
        return ws

    @pytest.mark.asyncio
    async def test_pause_command(self, mock_websocket):
        """Test processing pause command."""
        pause_payload = json.dumps({"command": "pause", "client_id": "test-client"})

        # mock_websocket is `ws`. ws.__aiter__() returns `async_iterator_mock`.
        # We configure async_iterator_mock.__anext__.
        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            pause_payload,
            StopAsyncIteration,  # Stop after one message
        ]

        client.is_paused = False
        # We expect listen_for_commands to consume one message and update state
        await client.listen_for_commands(mock_websocket)
        assert client.is_paused is True
        # Check acknowledgment
        mock_websocket.send.assert_awaited_once()
        sent_data = json.loads(mock_websocket.send.await_args[0][0])
        assert sent_data["status"]["client_state"] == "paused"
        assert sent_data["status"]["acknowledged_command"] == "pause"

    @pytest.mark.asyncio
    async def test_resume_command(self, mock_websocket):
        """Test processing resume command."""
        resume_payload = json.dumps({"command": "resume", "client_id": "test-client"})

        # Configure the mock_websocket's iterator for this test
        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            resume_payload,
            StopAsyncIteration,  # Stop after one message
        ]

        client.is_paused = True
        await client.listen_for_commands(mock_websocket)
        assert client.is_paused is False
        # Check acknowledgment
        mock_websocket.send.assert_awaited_once()
        sent_data = json.loads(mock_websocket.send.await_args[0][0])
        assert sent_data["status"]["client_state"] == "running"
        assert sent_data["status"]["acknowledged_command"] == "resume"


class TestPeriodicStatusSender:
    """Test periodic status updates."""

    @pytest.mark.asyncio
    async def test_send_updates_when_not_paused(self):
        """Test status updates are sent when not paused."""

        # Ensure clean state for this test
        client.manual_disconnect_initiated = False
        client.is_paused = False

        mock_ws = AsyncMock()
        # client.is_paused is already set to False above

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            with pytest.raises(asyncio.CancelledError):
                await client.send_status_update_periodically(mock_ws)

        assert mock_ws.send.await_count > 0

    @pytest.mark.asyncio
    async def test_skip_updates_when_paused(self):
        """Test status updates are skipped when paused."""

        # Ensure clean state for this test
        client.manual_disconnect_initiated = False
        client.is_paused = True  # Set specifically for this test

        mock_ws = AsyncMock()
        # client.is_paused is already set to True above

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            with pytest.raises(asyncio.CancelledError):
                await client.send_status_update_periodically(mock_ws)

        mock_ws.send.assert_not_awaited()
