import asyncio
import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from websockets import ConnectionClosed
from websockets.frames import Close

# Ensure src.client.client can be imported.
# This might require adjusting PYTHONPATH or test runner configuration
# in a real setup. For now, let's assume it's handled by the test execution
# environment.
from src.client import client

# Store original values to restore after tests
ORIGINAL_SERVER_URL = client.SERVER_URL
ORIGINAL_STATUS_INTERVAL = client.STATUS_INTERVAL
ORIGINAL_RECONNECT_DELAY = client.RECONNECT_DELAY


class TestClientConfiguration(unittest.TestCase):
    """Test configuration and environment variable handling."""

    @patch.dict(os.environ, {"SERVER_URL": "ws://test-server:9999/ws"})
    def test_server_url_from_environment(self):
        """Test that SERVER_URL is read from environment variable."""
        # Reload the module to pick up the environment variable
        import importlib

        importlib.reload(client)
        self.assertEqual(client.SERVER_URL, "ws://test-server:9999/ws")

    @patch.dict(os.environ, {}, clear=True)
    def test_server_url_default_fallback(self):
        """Test that SERVER_URL falls back to localhost when not set in env."""
        import importlib

        importlib.reload(client)
        self.assertEqual(client.SERVER_URL, "ws://localhost:8000/ws")


class TestClientUtilityFunctions(unittest.TestCase):
    """Test utility functions."""

    def test_get_current_status_payload(self):
        """Test status payload generation."""
        status = client.get_current_status_payload()

        self.assertIn("timestamp", status)
        self.assertIn("cpu_usage", status)
        self.assertIn("memory_usage", status)

        # Validate timestamp format
        datetime.fromisoformat(status["timestamp"])

        # Validate metrics are numbers
        self.assertIsInstance(status["cpu_usage"], float)
        self.assertIsInstance(status["memory_usage"], float)
        self.assertGreaterEqual(status["cpu_usage"], 0.0)
        self.assertLessEqual(status["cpu_usage"], 100.0)
        self.assertGreaterEqual(status["memory_usage"], 0.0)
        self.assertLessEqual(status["memory_usage"], 100.0)


class TestClientMessageHandling(unittest.IsolatedAsyncioTestCase):
    """Test WebSocket message sending and receiving."""

    def setUp(self):
        # Override config for testing
        client.SERVER_URL = "ws://fake-server:1234/ws"
        client.STATUS_INTERVAL = 0.1
        client.RECONNECT_DELAY = 0.1
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False  # Reset pause state

    def tearDown(self):
        # Restore original config
        client.SERVER_URL = ORIGINAL_SERVER_URL
        client.STATUS_INTERVAL = ORIGINAL_STATUS_INTERVAL
        client.RECONNECT_DELAY = ORIGINAL_RECONNECT_DELAY

    async def test_send_status_message(self):
        """Test sending status messages with proper format."""
        mock_websocket = AsyncMock()

        status_attributes = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_usage": 45.5,
            "memory_usage": 67.8,
            "client_state": "running",
        }

        await client.send_status_message(mock_websocket, status_attributes)

        mock_websocket.send.assert_called_once()
        sent_data = json.loads(mock_websocket.send.call_args[0][0])

        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertEqual(sent_data["status"], status_attributes)

    async def test_send_full_status_update(self):
        """Test sending full status updates."""
        mock_websocket = AsyncMock()

        await client.send_full_status_update(mock_websocket)

        mock_websocket.send.assert_called_once()
        sent_data = json.loads(mock_websocket.send.call_args[0][0])

        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertIn("status", sent_data)
        self.assertIn("timestamp", sent_data["status"])
        self.assertIn("cpu_usage", sent_data["status"])
        self.assertIn("memory_usage", sent_data["status"])
        self.assertIn("client_state", sent_data["status"])


class TestCommandListener(unittest.IsolatedAsyncioTestCase):
    """Test command message processing."""

    def setUp(self):
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False

    def create_message_iterator(self, messages):
        """Helper to create an async iterator that yields messages."""

        class AsyncIter:
            def __init__(self, messages):
                self.messages = iter(messages)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    raise StopAsyncIteration

        return AsyncIter(messages)

    def mock_websocket_with_messages(self, messages):
        """Creates a mock websocket that iterates through the given messages."""
        mock_ws = AsyncMock()

        # Store the iterator class instance
        message_iter = self.create_message_iterator(messages)

        # Mock __aiter__ to return the instance directly, not a method
        mock_ws.__aiter__ = lambda self: message_iter

        return mock_ws

    async def test_server_acknowledgment_message(self):
        """Test that server acknowledgment messages are handled correctly."""
        ack_message = {
            "result": "message_processed",
            "client_id": client.CLIENT_ID,
            "status_updated": ["timestamp", "cpu_usage"],
            "redis_status": "connected",
        }

        mock_websocket = self.mock_websocket_with_messages([json.dumps(ack_message)])

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            msg = f"Client {client.CLIENT_ID}: " f"Status update acknowledged by server"
            mock_print.assert_called_with(msg)

    async def test_pause_command(self):
        """Test pause command processing."""
        pause_message = {"command": "pause"}

        mock_websocket = self.mock_websocket_with_messages([json.dumps(pause_message)])

        await client.listen_for_commands(mock_websocket)

        self.assertTrue(client.is_paused)
        mock_websocket.send.assert_called_once()

        # Verify acknowledgment message
        sent_data = json.loads(mock_websocket.send.call_args[0][0])
        self.assertEqual(sent_data["status"]["client_state"], "paused")
        self.assertEqual(sent_data["status"]["acknowledged_command"], "pause")

    async def test_resume_command(self):
        """Test resume command processing."""
        client.is_paused = True  # Start in paused state
        resume_message = {"command": "resume"}

        mock_websocket = self.mock_websocket_with_messages([json.dumps(resume_message)])

        await client.listen_for_commands(mock_websocket)

        self.assertFalse(client.is_paused)
        mock_websocket.send.assert_called_once()

        # Verify acknowledgment message
        sent_data = json.loads(mock_websocket.send.call_args[0][0])
        self.assertEqual(sent_data["status"]["client_state"], "running")
        self.assertEqual(sent_data["status"]["acknowledged_command"], "resume")

    async def test_pause_command_when_already_paused(self):
        """Test pause command when client is already paused."""
        client.is_paused = True
        pause_message = {"command": "pause"}

        mock_websocket = self.mock_websocket_with_messages([json.dumps(pause_message)])

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            message = (
                f"Client {client.CLIENT_ID}: Already paused. " f"Pause command ignored."
            )
            mock_print.assert_called_with(message)

        # Should not send acknowledgment when already paused
        mock_websocket.send.assert_not_called()

    async def test_resume_command_when_already_running(self):
        """Test resume command when client is already running."""
        client.is_paused = False
        resume_message = {"command": "resume"}

        mock_websocket = self.mock_websocket_with_messages([json.dumps(resume_message)])

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            message = (
                f"Client {client.CLIENT_ID}: Already running. "
                f"Resume command ignored."
            )
            mock_print.assert_called_with(message)

        # Should not send acknowledgment when already running
        mock_websocket.send.assert_not_called()

    async def test_unknown_command(self):
        """Test handling of unknown commands."""
        unknown_message = {"command": "unknown_command"}

        mock_websocket = self.mock_websocket_with_messages(
            [json.dumps(unknown_message)]
        )

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            message = (
                f"Client {client.CLIENT_ID}: "
                f"Unknown command received: unknown_command"
            )
            mock_print.assert_called_with(message)

    async def test_non_command_message(self):
        """Test handling of non-command messages."""
        non_command_message = {"some_field": "some_value"}

        mock_websocket = self.mock_websocket_with_messages(
            [json.dumps(non_command_message)]
        )

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            message = (
                f"Client {client.CLIENT_ID}: Received non-command msg: "
                f"{non_command_message}"
            )
            mock_print.assert_called_with(message)

    async def test_invalid_json_handling(self):
        """Test handling of invalid JSON messages."""
        mock_websocket = self.mock_websocket_with_messages(["invalid json"])

        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            msg = f"Client {client.CLIENT_ID}: " f"Received invalid JSON: invalid json"
            mock_print.assert_called_with(msg)

    async def test_server_disconnect_normal_closure(self):
        """Test handling of server disconnect (code 1000)."""
        # Mock ConnectionClosed exception with code 1000
        close_frame = Close(1000, "")
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1000
        mock_rcvd.reason = ""
        connection_closed = ConnectionClosed(close_frame, None)
        connection_closed.rcvd = mock_rcvd

        # Create an async iterator that raises the exception
        class AsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__ = lambda self: AsyncIter()

        with self.assertRaises(SystemExit) as context:
            await client.listen_for_commands(mock_websocket)

        shutdown_msg = "Server disconnected client - shutting down"
        self.assertEqual(str(context.exception), shutdown_msg)

    async def test_connection_closed_other_code(self):
        """Test handling of connection closed with other codes (should reconnect)."""
        # Mock ConnectionClosed exception with code other than 1000
        close_frame = Close(1001, "Going away")
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1001
        mock_rcvd.reason = "Going away"
        connection_closed = ConnectionClosed(close_frame, None)
        connection_closed.rcvd = mock_rcvd

        # Create an async iterator that raises the exception
        class AsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        mock_websocket = AsyncMock()
        mock_websocket.__aiter__ = lambda self: AsyncIter()

        with self.assertRaises(ConnectionClosed):
            await client.listen_for_commands(mock_websocket)


class TestPeriodicStatusSender(unittest.IsolatedAsyncioTestCase):
    """Test periodic status update functionality."""

    def setUp(self):
        client.CLIENT_ID = str(uuid.uuid4())
        client.STATUS_INTERVAL = 0.05  # Very fast for testing
        client.is_paused = False

    async def test_sends_status_when_not_paused(self):
        """Test that status updates are sent when client is not paused."""
        mock_websocket = AsyncMock()

        # Run for a short time and cancel
        task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket)
        )
        await asyncio.sleep(0.12)  # Allow for 2+ updates
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        self.assertGreaterEqual(mock_websocket.send.call_count, 2)

    async def test_skips_status_when_paused(self):
        """Test that status updates are skipped when client is paused."""
        client.is_paused = True
        mock_websocket = AsyncMock()

        # Run for a short time and cancel
        task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket)
        )
        await asyncio.sleep(0.12)  # Would normally allow for 2+ updates
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should not send any status updates when paused
        mock_websocket.send.assert_not_called()


class TestConnectionManagement(unittest.IsolatedAsyncioTestCase):
    """Test connection management and reconnection logic."""

    def setUp(self):
        client.SERVER_URL = "ws://fake-server:1234/ws"
        client.RECONNECT_DELAY = 0.01  # Very fast for testing
        client.CLIENT_ID = str(uuid.uuid4())

    @patch("src.client.client.websockets.connect")
    async def test_successful_connection_and_initial_status(self, mock_connect):
        """Test successful connection and initial status message."""
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()

        # Mock the async context manager
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_websocket)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock the gather to return immediately to avoid infinite loop
        with patch("asyncio.gather", new_callable=AsyncMock) as mock_gather:
            mock_gather.side_effect = asyncio.CancelledError()

            task = asyncio.create_task(client.connect_and_send_updates())
            await asyncio.sleep(0.01)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_connect.assert_called_with(client.SERVER_URL)

        # Check that send was called at least once (instead of exactly once)
        self.assertTrue(mock_websocket.send.called)

        # Verify initial status message format from the first call
        first_call_args = mock_websocket.send.call_args_list[0][0]
        sent_data = json.loads(first_call_args[0])
        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertEqual(sent_data["status"]["client_state"], "running")
        self.assertIn("connected_at", sent_data["status"])

    @patch("src.client.client.websockets.connect")
    async def test_system_exit_stops_reconnection(self, mock_connect):
        """Test that SystemExit stops the reconnection loop."""
        # Mock connect to raise SystemExit
        mock_connect.side_effect = SystemExit(
            "Server disconnected client - shutting down"
        )

        # This should return without infinite loop
        await client.connect_and_send_updates()

        mock_connect.assert_called_once()

    @patch("src.client.client.websockets.connect")
    async def test_connection_error_triggers_reconnect(self, mock_connect):
        """Test that connection errors trigger reconnection attempts."""
        mock_connect.side_effect = [
            ConnectionRefusedError("Connection refused"),
            asyncio.CancelledError(),  # Stop the loop on second attempt
        ]

        with self.assertRaises(asyncio.CancelledError):
            await client.connect_and_send_updates()

        self.assertEqual(mock_connect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
