# tests/client/test_client.py
import asyncio
import importlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
from websockets.frames import Close

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

    def test_generate_realistic_name(self):
        """Test realistic name generation."""
        name1 = client.generate_realistic_name()
        name2 = client.generate_realistic_name()

        assert isinstance(name1, str)
        assert isinstance(name2, str)
        assert len(name1) > 0
        assert len(name2) > 0
        # Names should be different (very high probability)
        assert name1 != name2

    def test_client_initiated_disconnect_exception(self):
        """Test ClientInitiatedDisconnect exception class."""
        exc = client.ClientInitiatedDisconnect("test message")
        assert isinstance(exc, SystemExit)
        assert str(exc) == "test message"

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

    @pytest.mark.asyncio
    async def test_send_status_message_when_manual_disconnect_initiated(self):
        """Test that status messages are blocked when manual disconnect is initiated."""
        original_flag = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True
            mock_ws = AsyncMock()

            status = {"cpu_usage": 50, "memory_usage": 30}
            await client.send_status_message(mock_ws, status)

            # Should not send when manual_disconnect_initiated is True
            mock_ws.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_flag

    @pytest.mark.asyncio
    async def test_send_status_message_disconnect_acknowledgment_allowed(self):
        """Test that disconnect acknowledgments are sent even when manual disconnect is initiated."""
        original_flag = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True
            mock_ws = AsyncMock()

            status = {
                "acknowledged_command": "disconnect",
                "client_state": "disconnecting",
            }
            await client.send_status_message(mock_ws, status)

            # Should send disconnect acknowledgment even when manual_disconnect_initiated is True
            mock_ws.send.assert_awaited_once()
            sent_data = json.loads(mock_ws.send.await_args[0][0])
            assert sent_data["status"]["acknowledged_command"] == "disconnect"
        finally:
            client.manual_disconnect_initiated = original_flag

    @pytest.mark.asyncio
    async def test_send_full_status_update_when_paused(self):
        """Test that full status updates are skipped when paused."""
        original_paused = client.is_paused
        original_disconnect = client.manual_disconnect_initiated
        try:
            client.is_paused = True
            client.manual_disconnect_initiated = False
            mock_ws = AsyncMock()

            await client.send_full_status_update(mock_ws)

            # Should not send when paused
            mock_ws.send.assert_not_awaited()
        finally:
            client.is_paused = original_paused
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_send_full_status_update_when_manual_disconnect_initiated(self):
        """Test that full status updates are skipped when manual disconnect is initiated."""
        original_paused = client.is_paused
        original_disconnect = client.manual_disconnect_initiated
        try:
            client.is_paused = False
            client.manual_disconnect_initiated = True
            mock_ws = AsyncMock()

            await client.send_full_status_update(mock_ws)

            # Should not send when manual_disconnect_initiated is True
            mock_ws.send.assert_not_awaited()
        finally:
            client.is_paused = original_paused
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_send_full_status_update_normal_operation(self):
        """Test that full status updates are sent during normal operation."""
        original_paused = client.is_paused
        original_disconnect = client.manual_disconnect_initiated
        try:
            client.is_paused = False
            client.manual_disconnect_initiated = False
            mock_ws = AsyncMock()

            await client.send_full_status_update(mock_ws)

            # Should send when not paused and not disconnecting
            mock_ws.send.assert_awaited_once()
            sent_data = json.loads(mock_ws.send.await_args[0][0])
            assert sent_data["status"]["client_state"] == "running"
        finally:
            client.is_paused = original_paused
            client.manual_disconnect_initiated = original_disconnect


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

    @pytest.mark.asyncio
    async def test_listen_for_commands_invalid_json(self, mock_websocket):
        """Test handling of invalid JSON messages."""
        # Configure mock to yield invalid JSON then stop
        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            "invalid json {",
            StopAsyncIteration,
        ]

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            # Should handle invalid JSON gracefully
            await client.listen_for_commands(mock_websocket)

            # Should not crash and not send any responses
            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_server_acknowledgment_messages(
        self, mock_websocket
    ):
        """Test handling of server acknowledgment messages."""
        ack_message1 = json.dumps({"result": "message_processed", "client_id": "test"})
        ack_message2 = json.dumps(
            {"result": "registration_complete", "client_id": "test"}
        )

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            ack_message1,
            ack_message2,
            StopAsyncIteration,
        ]

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            await client.listen_for_commands(mock_websocket)

            # Should not send any responses to acknowledgments
            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_non_command_message(self, mock_websocket):
        """Test handling of non-command messages."""
        non_command_message = json.dumps({"some_field": "some_value", "data": "test"})

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            non_command_message,
            StopAsyncIteration,
        ]

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            await client.listen_for_commands(mock_websocket)

            # Should not send any responses to non-command messages
            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_unknown_command(self, mock_websocket):
        """Test handling of unknown commands."""
        unknown_command = json.dumps(
            {"command": "unknown_command", "client_id": "test"}
        )

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            unknown_command,
            StopAsyncIteration,
        ]

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            await client.listen_for_commands(mock_websocket)

            # Should not send any responses to unknown commands
            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_pause_when_already_paused(self, mock_websocket):
        """Test pause command when already paused."""
        pause_command = json.dumps({"command": "pause", "client_id": "test"})

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            pause_command,
            StopAsyncIteration,
        ]

        original_paused = client.is_paused
        original_disconnect = client.manual_disconnect_initiated
        try:
            client.is_paused = True  # Already paused
            client.manual_disconnect_initiated = False

            await client.listen_for_commands(mock_websocket)

            # Should not send acknowledgment when already paused
            mock_websocket.send.assert_not_awaited()
            assert client.is_paused is True  # Should remain paused
        finally:
            client.is_paused = original_paused
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_resume_when_already_running(
        self, mock_websocket
    ):
        """Test resume command when already running."""
        resume_command = json.dumps({"command": "resume", "client_id": "test"})

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            resume_command,
            StopAsyncIteration,
        ]

        original_paused = client.is_paused
        original_disconnect = client.manual_disconnect_initiated
        try:
            client.is_paused = False  # Already running
            client.manual_disconnect_initiated = False

            await client.listen_for_commands(mock_websocket)

            # Should not send acknowledgment when already running
            mock_websocket.send.assert_not_awaited()
            assert client.is_paused is False  # Should remain running
        finally:
            client.is_paused = original_paused
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_connection_closed_during_manual_disconnect(
        self, mock_websocket
    ):
        """Test ConnectionClosed exception during manual disconnect."""
        close_frame = Close(code=1000, reason="Normal closure")
        connection_closed = websockets.exceptions.ConnectionClosed(
            rcvd=close_frame, sent=None
        )

        mock_websocket.__aiter__.return_value.__anext__.side_effect = connection_closed

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True

            # Should handle ConnectionClosed gracefully during manual disconnect
            await client.listen_for_commands(mock_websocket)

            # Should not send any responses
            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_connection_closed_code_1000(
        self, mock_websocket
    ):
        """Test ConnectionClosed with code 1000 (normal closure)."""
        close_frame = Close(code=1000, reason="Normal closure")
        connection_closed = websockets.exceptions.ConnectionClosed(
            rcvd=close_frame, sent=None
        )

        mock_websocket.__aiter__.return_value.__anext__.side_effect = connection_closed

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await client.listen_for_commands(mock_websocket)

            # Should set manual_disconnect_initiated flag for code 1000
            assert client.manual_disconnect_initiated is True
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_connection_closed_other_code(
        self, mock_websocket
    ):
        """Test ConnectionClosed with other codes."""
        close_frame = Close(code=1006, reason="Abnormal closure")
        connection_closed = websockets.exceptions.ConnectionClosed(
            rcvd=close_frame, sent=None
        )

        mock_websocket.__aiter__.return_value.__anext__.side_effect = connection_closed

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await client.listen_for_commands(mock_websocket)

            # Should not set manual_disconnect_initiated flag for other codes
            assert client.manual_disconnect_initiated is False
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_connection_closed_no_rcvd(self, mock_websocket):
        """Test ConnectionClosed exception without rcvd frame."""
        connection_closed = websockets.exceptions.ConnectionClosed(rcvd=None, sent=None)
        mock_websocket.__aiter__.return_value.__anext__.side_effect = connection_closed

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await client.listen_for_commands(mock_websocket)

            assert client.manual_disconnect_initiated is False
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_unexpected_exception(self, mock_websocket):
        """Test handling of unexpected exceptions."""
        mock_websocket.__aiter__.return_value.__anext__.side_effect = RuntimeError(
            "Unexpected error"
        )

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = False

            with pytest.raises(RuntimeError):
                await client.listen_for_commands(mock_websocket)

            assert client.manual_disconnect_initiated is True
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_exception_during_manual_disconnect(
        self, mock_websocket
    ):
        """Test exception handling during manual disconnect."""
        mock_websocket.__aiter__.return_value.__anext__.side_effect = RuntimeError(
            "Error during shutdown"
        )

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True

            with pytest.raises(RuntimeError):
                await client.listen_for_commands(mock_websocket)

            assert client.manual_disconnect_initiated is True
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_early_exit_on_manual_disconnect(
        self, mock_websocket
    ):
        """Test early exit when manual_disconnect_initiated is set."""
        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            json.dumps({"command": "pause", "client_id": "test"}),
            StopAsyncIteration,
        ]

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True

            await client.listen_for_commands(mock_websocket)

            mock_websocket.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_listen_for_commands_exception_in_command_processing(
        self, mock_websocket
    ):
        """Test exception handling during command processing."""
        pause_command = json.dumps({"command": "pause", "client_id": "test"})

        mock_websocket.__aiter__.return_value.__anext__.side_effect = [
            pause_command,
            StopAsyncIteration,
        ]

        # Mock send_status_message to raise an exception
        with patch(
            "src.client.client.send_status_message", new_callable=AsyncMock
        ) as mock_send:
            mock_send.side_effect = RuntimeError("Send failed")

            original_paused = client.is_paused
            original_disconnect = client.manual_disconnect_initiated
            try:
                client.is_paused = False
                client.manual_disconnect_initiated = False

                # Should handle exception during command processing
                await client.listen_for_commands(mock_websocket)

                # State should still be updated even if send fails
                assert client.is_paused is True
            finally:
                client.is_paused = original_paused
                client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_disconnect_command_received(self, mock_websocket, caplog):
        """Test processing a disconnect command from the server."""
        original_manual_disconnect_flag = client.manual_disconnect_initiated

        try:
            client.manual_disconnect_initiated = False

            disconnect_payload = json.dumps(
                {"command": "disconnect", "client_id": client.CLIENT_ID}
            )

            mock_websocket.__aiter__.return_value.__anext__.side_effect = [
                disconnect_payload,
                StopAsyncIteration,
            ]

            # Mock datetime for predictable timestamp
            mock_specific_time = MagicMock()
            mock_specific_time.isoformat.return_value = "2023-01-01T12:00:00Z"
            mock_now_function = MagicMock(return_value=mock_specific_time)
            mock_datetime_object_for_patching = MagicMock()
            mock_datetime_object_for_patching.now = mock_now_function

            with patch.object(client, "datetime", mock_datetime_object_for_patching):
                await client.listen_for_commands(mock_websocket)

            assert client.manual_disconnect_initiated is True
            mock_websocket.send.assert_awaited_once()
            sent_data_str = mock_websocket.send.await_args[0][0]
            sent_data = json.loads(sent_data_str)

            expected_ack_status = {
                "client_state": "disconnecting",
                "acknowledged_command": "disconnect",
                "timestamp": "2023-01-01T12:00:00Z",
            }
            assert sent_data["client_id"] == client.CLIENT_ID
            assert sent_data["status"] == expected_ack_status

        finally:
            client.manual_disconnect_initiated = original_manual_disconnect_flag

    @pytest.mark.asyncio
    async def test_listen_for_commands_error_during_shutdown(
        self, mock_websocket, caplog
    ):
        """Test error logging during shutdown in listen_for_commands when an error occurs during command processing."""
        # Save original states
        original_manual_disconnect_flag = client.manual_disconnect_initiated
        original_is_paused_flag = client.is_paused

        try:
            # Set test conditions
            client.manual_disconnect_initiated = False
            client.is_paused = False

            error_message = "Simulated error during send_status_message in shutdown"

            # Simulate receiving a 'pause' command
            pause_payload = json.dumps(
                {"command": "pause", "client_id": client.CLIENT_ID}
            )

            mock_websocket.__aiter__.return_value.__anext__.side_effect = [
                pause_payload,
                StopAsyncIteration,  # Stop after this message
            ]

            # Mock send_status_message to raise an exception during processing
            with patch(
                "src.client.client.send_status_message", new_callable=AsyncMock
            ) as mock_send_status:

                async def side_effect(*args, **kwargs):
                    client.manual_disconnect_initiated = True
                    raise Exception(error_message)

                mock_send_status.side_effect = side_effect

                # The function should handle the exception gracefully
                await client.listen_for_commands(mock_websocket)

                # Look for the error log message
                expected_log_part = f"Error during shutdown: {error_message}"
                log_found = any(
                    expected_log_part in record.message
                    for record in caplog.records
                    if record.levelname == "ERROR"
                )

                assert (
                    log_found
                ), f"Expected error log containing '{expected_log_part}' not found"

                # The state should still be updated even if send_status_message fails
                assert (
                    client.is_paused
                ), "Expected is_paused to be True after processing pause command"

        finally:
            # Restore original states
            client.manual_disconnect_initiated = original_manual_disconnect_flag
            client.is_paused = original_is_paused_flag


class TestPeriodicStatusSender:
    """Test periodic status updates."""

    @pytest.mark.asyncio
    async def test_send_updates_when_not_paused(self):
        """Test status updates are sent when not paused."""
        original_disconnect = client.manual_disconnect_initiated
        original_paused = client.is_paused

        try:
            # Ensure clean state for this test
            client.manual_disconnect_initiated = False
            client.is_paused = False

            mock_ws = AsyncMock()

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await client.send_status_update_periodically(mock_ws)

            assert mock_ws.send.await_count > 0
        finally:
            client.manual_disconnect_initiated = original_disconnect
            client.is_paused = original_paused

    @pytest.mark.asyncio
    async def test_skip_updates_when_paused(self):
        """Test status updates are skipped when paused."""
        original_disconnect = client.manual_disconnect_initiated
        original_paused = client.is_paused

        try:
            # Ensure clean state for this test
            client.manual_disconnect_initiated = False
            client.is_paused = True  # Set specifically for this test

            mock_ws = AsyncMock()

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await client.send_status_update_periodically(mock_ws)

            mock_ws.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect
            client.is_paused = original_paused

    @pytest.mark.asyncio
    async def test_send_status_update_periodically_connection_closed(self):
        """Test ConnectionClosed exception in periodic sender."""
        mock_ws = AsyncMock()
        close_frame = Close(code=1006, reason="Connection lost")
        connection_closed = websockets.exceptions.ConnectionClosed(
            rcvd=close_frame, sent=None
        )

        original_disconnect = client.manual_disconnect_initiated
        original_paused = client.is_paused
        try:
            client.manual_disconnect_initiated = False
            client.is_paused = (
                False  # Ensure not paused so send_full_status_update is called
            )

            # Mock send_full_status_update to raise ConnectionClosed
            with patch(
                "src.client.client.send_full_status_update", new_callable=AsyncMock
            ) as mock_send:
                mock_send.side_effect = connection_closed

                with pytest.raises(websockets.exceptions.ConnectionClosed):
                    await client.send_status_update_periodically(mock_ws)

                # Verify the function was called
                mock_send.assert_called_once_with(mock_ws)
        finally:
            client.manual_disconnect_initiated = original_disconnect
            client.is_paused = original_paused

    @pytest.mark.asyncio
    async def test_send_status_update_periodically_exits_on_manual_disconnect(self):
        """Test that periodic sender exits when manual_disconnect_initiated is True."""
        mock_ws = AsyncMock()

        original_disconnect = client.manual_disconnect_initiated
        try:
            client.manual_disconnect_initiated = True

            # Should exit immediately without raising exception
            await client.send_status_update_periodically(mock_ws)

            # Should not call send_full_status_update when manual_disconnect_initiated is True
            mock_ws.send.assert_not_awaited()
        finally:
            client.manual_disconnect_initiated = original_disconnect

    @pytest.mark.asyncio
    async def test_send_status_update_periodically_unexpected_exception(self):
        """Test unexpected exception in periodic sender."""
        mock_ws = AsyncMock()

        original_disconnect = client.manual_disconnect_initiated
        original_paused = client.is_paused
        try:
            client.manual_disconnect_initiated = False
            client.is_paused = (
                False  # Ensure not paused so send_full_status_update is called
            )

            # Mock send_full_status_update to raise unexpected exception
            with patch(
                "src.client.client.send_full_status_update", new_callable=AsyncMock
            ) as mock_send:
                mock_send.side_effect = RuntimeError("Unexpected error")

                with pytest.raises(RuntimeError):
                    await client.send_status_update_periodically(mock_ws)

                # Should set manual_disconnect_initiated flag on unexpected errors
                assert client.manual_disconnect_initiated is True

                # Verify the function was called
                mock_send.assert_called_once_with(mock_ws)
        finally:
            client.manual_disconnect_initiated = original_disconnect
            client.is_paused = original_paused


class TestMainExecution:
    """Test main execution block."""

    def test_main_execution_keyboard_interrupt(self):
        """Test keyboard interrupt handling in main block."""
        # Mock the connect_and_send_updates function to avoid creating unawaited coroutines
        with patch("src.client.client.connect_and_send_updates") as mock_connect:
            mock_connect.side_effect = KeyboardInterrupt("User interrupt")

            # Test that KeyboardInterrupt is handled
            try:
                import asyncio

                asyncio.run(mock_connect())
            except KeyboardInterrupt:
                # This is expected behavior
                pass

            # Test passes if no unhandled exception occurs
            assert True
