import asyncio
import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
        # Restore for other tests
        importlib.reload(client)

    @patch.dict(os.environ, {}, clear=True)
    def test_server_url_default_fallback(self):
        """Test that SERVER_URL falls back to localhost when not set in env."""
        import importlib

        importlib.reload(client)
        self.assertEqual(client.SERVER_URL, "ws://localhost:8000/ws")
        # Restore for other tests
        importlib.reload(client)


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
        self.original_client_id = client.CLIENT_ID
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
        client.CLIENT_ID = self.original_client_id

    async def test_send_status_message(self):
        """Test sending status messages with proper format."""
        mock_websocket = AsyncMock()

        status_attributes = {
            "timestamp": datetime.now(UTC).isoformat(),
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
        self.original_client_id = client.CLIENT_ID
        self.original_is_paused = client.is_paused
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False

    def tearDown(self):
        client.CLIENT_ID = self.original_client_id
        client.is_paused = self.original_is_paused

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
                    raise StopAsyncIteration from None

        return AsyncIter(messages)

    def mock_websocket_with_messages(self, messages):
        """Creates a mock websocket that iterates through the given messages."""
        mock_ws = AsyncMock()
        message_iter = self.create_message_iterator(messages)
        mock_ws.__aiter__ = lambda self: message_iter

        # Mock recv to pull from the iterator
        async def recv_side_effect():
            try:
                return await message_iter.__anext__()
            except StopAsyncIteration:
                # Keep connection open for other tasks if iterator is exhausted
                await asyncio.sleep(3600)  # Sleep for a long time

        mock_ws.recv.side_effect = recv_side_effect
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
            # Run listen_for_commands for a short duration or until message processed
            try:
                await asyncio.wait_for(
                    client.listen_for_commands(mock_websocket), timeout=0.1
                )
            except TimeoutError:
                pass  # Expected if the loop continues due to recv sleeping
            msg = f"Client {client.CLIENT_ID}: " f"Status update acknowledged by server"
            mock_print.assert_any_call(msg)  # Use assert_any_call if other prints occur

    async def test_pause_command(self):
        """Test pause command processing."""
        pause_message = {"command": "pause"}
        mock_websocket = self.mock_websocket_with_messages([json.dumps(pause_message)])
        await client.listen_for_commands(mock_websocket)
        self.assertTrue(client.is_paused)
        mock_websocket.send.assert_called_once()
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
            mock_print.assert_any_call(message)
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
            mock_print.assert_any_call(message)
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
            mock_print.assert_any_call(message)

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
            mock_print.assert_any_call(message)

    async def test_invalid_json_handling(self):
        """Test handling of invalid JSON messages."""
        mock_websocket = self.mock_websocket_with_messages(["invalid json"])
        with patch("builtins.print") as mock_print:
            await client.listen_for_commands(mock_websocket)
            msg = f"Client {client.CLIENT_ID}: " f"Received invalid JSON: invalid json"
            mock_print.assert_any_call(msg)

    async def test_server_disconnect_normal_closure(self):
        """Test handling of server disconnect (code 1000)."""
        close_frame = Close(1000, "Normal server disconnect")
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1000
        mock_rcvd.reason = "Normal server disconnect"
        connection_closed = ConnectionClosed(close_frame, None)
        connection_closed.rcvd = mock_rcvd

        mock_websocket = AsyncMock()

        # Create an async iterator that raises ConnectionClosed
        class AsyncIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        mock_websocket.__aiter__ = lambda self: AsyncIterator()

        with self.assertRaises(client.ClientInitiatedDisconnect) as context:
            await client.listen_for_commands(mock_websocket)

        # Check the message of the raised ClientInitiatedDisconnect
        self.assertEqual(
            str(context.exception), "Server disconnected client - shutting down"
        )

    async def test_connection_closed_other_code(self):
        """Test handling of connection closed with other codes (should reconnect)."""
        close_frame = Close(1001, "Going away")
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1001
        mock_rcvd.reason = "Going away"
        connection_closed = ConnectionClosed(close_frame, None)
        connection_closed.rcvd = mock_rcvd

        mock_websocket = AsyncMock()

        # Create an async iterator that raises ConnectionClosed
        class AsyncIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        mock_websocket.__aiter__ = lambda self: AsyncIterator()

        with self.assertRaises(ConnectionClosed):
            await client.listen_for_commands(mock_websocket)

    @patch("src.client.client.websockets.connect")
    async def test_client_initiated_disconnect_workflow(self, mock_connect):
        """Test the full client disconnect flow initiated by server command."""
        # This test focuses on the disconnect command handling
        # The full integration is complex due to asyncio.gather() exception handling

        disconnect_message = {"command": "disconnect"}
        mock_websocket = self.mock_websocket_with_messages(
            [json.dumps(disconnect_message)]
        )

        with self.assertRaises(client.ClientInitiatedDisconnect) as context:
            await client.listen_for_commands(mock_websocket)

        self.assertEqual(str(context.exception), "Server requested disconnect.")
        mock_websocket.send.assert_called_once()
        sent_data = json.loads(mock_websocket.send.call_args[0][0])
        self.assertEqual(sent_data["status"]["client_state"], "disconnecting")
        self.assertEqual(sent_data["status"]["acknowledged_command"], "disconnect")
        self.assertIn("timestamp", sent_data["status"])


class TestPeriodicStatusSender(unittest.IsolatedAsyncioTestCase):
    """Test periodic status update functionality."""

    def setUp(self):
        self.original_client_id = client.CLIENT_ID
        self.original_status_interval = client.STATUS_INTERVAL
        self.original_is_paused = client.is_paused

        client.CLIENT_ID = str(uuid.uuid4())
        client.STATUS_INTERVAL = 0.01  # Very fast for testing
        client.is_paused = False

    def tearDown(self):
        client.CLIENT_ID = self.original_client_id
        client.STATUS_INTERVAL = self.original_status_interval
        client.is_paused = self.original_is_paused

    @patch("src.client.client.send_full_status_update", new_callable=AsyncMock)
    async def test_sends_status_when_not_paused(self, mock_send_full_status_update):
        """Test that status updates are sent when client is not paused."""
        mock_websocket = AsyncMock()
        client.is_paused = False

        task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket)
        )
        await asyncio.sleep(
            client.STATUS_INTERVAL * 3.1
        )  # Allow for ~3 updates with buffer
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.assertGreaterEqual(mock_send_full_status_update.call_count, 2)
        mock_send_full_status_update.assert_any_call(mock_websocket)

    @patch("src.client.client.send_full_status_update", new_callable=AsyncMock)
    async def test_skips_status_when_paused(self, mock_send_full_status_update):
        """Test that status updates are skipped when client is paused."""
        mock_websocket = AsyncMock()
        client.is_paused = True

        task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket)
        )
        await asyncio.sleep(client.STATUS_INTERVAL * 2.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_send_full_status_update.assert_not_called()

    @patch("src.client.client.send_full_status_update", new_callable=AsyncMock)
    async def test_pause_resume_flow_impacts_sender(self, mock_send_full_status_update):
        """Test that pause and resume commands correctly control periodic updates."""
        mock_websocket_sender = AsyncMock()  # For send_status_update_periodically

        # Setup listener to handle commands
        command_queue = asyncio.Queue()

        async def listen_side_effect(ws):
            while True:
                msg_json = await command_queue.get()
                if msg_json is None:  # Sentinel to stop
                    break
                await client.listen_for_commands(
                    ws
                )  # This is not ideal, should mock internal logic
                # But for now, let's use the real function with a controlled input
                # Actually, no, this will run an infinite loop.
                # We need to manually set client.is_paused based on command.

        # Simplified: directly manipulate is_paused and check send_full_status_update
        client.is_paused = False
        sender_task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket_sender)
        )

        await asyncio.sleep(client.STATUS_INTERVAL * 1.5)  # Should send once or twice
        self.assertGreaterEqual(mock_send_full_status_update.call_count, 1)
        initial_calls = mock_send_full_status_update.call_count

        # Simulate PAUSE
        client.is_paused = True
        await asyncio.sleep(
            client.STATUS_INTERVAL * 2.5
        )  # Should not send during this time
        self.assertEqual(
            mock_send_full_status_update.call_count, initial_calls
        )  # No new calls

        # Simulate RESUME
        client.is_paused = False
        await asyncio.sleep(client.STATUS_INTERVAL * 1.5)  # Should send again
        self.assertGreaterEqual(
            mock_send_full_status_update.call_count, initial_calls + 1
        )

        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass


class TestConnectionManagement(unittest.IsolatedAsyncioTestCase):
    """Test connection management and reconnection logic."""

    def setUp(self):
        self.original_server_url = client.SERVER_URL
        self.original_reconnect_delay = client.RECONNECT_DELAY
        self.original_client_id = client.CLIENT_ID

        client.SERVER_URL = "ws://fake-server:1234/ws"
        client.RECONNECT_DELAY = 0.01  # Very fast for testing
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False  # Ensure reset

    def tearDown(self):
        client.SERVER_URL = self.original_server_url
        client.RECONNECT_DELAY = self.original_reconnect_delay
        client.CLIENT_ID = self.original_client_id
        client.is_paused = False

    @patch("src.client.client.websockets.connect")
    async def test_successful_connection_and_initial_status(self, mock_connect):
        """Test successful connection and initial status message."""
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_websocket)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock the tasks and asyncio.sleep to control the flow
        with patch("src.client.client.listen_for_commands") as mock_listen, patch(
            "src.client.client.send_status_update_periodically"
        ) as mock_sender, patch("asyncio.sleep") as mock_sleep:

            # Create a ConnectionClosed exception that simulates connection loss
            from websockets.exceptions import ConnectionClosed
            from websockets.frames import Close

            # Simulate connection being closed, which should trigger reconnect logic
            connection_closed = ConnectionClosed(Close(1001, "Connection lost"), None)

            # First call: one task raises ConnectionClosed, triggering retry
            # Second call: mock_sleep raises exception to exit loop
            mock_listen.side_effect = connection_closed
            mock_sender.side_effect = AsyncMock()  # Let this one run normally
            mock_sleep.side_effect = SystemExit("Test exit")

            # Run the connection function until SystemExit
            with self.assertRaises(SystemExit):
                await client.connect_and_send_updates()

        # Verify connection was attempted
        mock_connect.assert_called_with(client.SERVER_URL)

        # Verify initial status message was sent
        self.assertTrue(mock_websocket.send.called)
        first_call_args = mock_websocket.send.call_args_list[0][0]
        sent_data = json.loads(first_call_args[0])
        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertEqual(sent_data["status"]["client_state"], "running")
        self.assertIn("connected_at", sent_data["status"])

        # Verify both task functions were called
        mock_listen.assert_called_with(mock_websocket)
        mock_sender.assert_called_with(mock_websocket)

        # Verify sleep was called after exception (retry mechanism)
        mock_sleep.assert_called_with(client.RECONNECT_DELAY)

    @patch("src.client.client.websockets.connect")
    async def test_system_exit_stops_reconnection(self, mock_connect):
        """Test that SystemExit (like ClientInitiatedDisconnect) stops the reconnection loop."""
        mock_connect.side_effect = client.ClientInitiatedDisconnect("Test disconnect")

        await client.connect_and_send_updates()  # Should catch and exit
        mock_connect.assert_called_once()

    @patch("src.client.client.websockets.connect")
    @patch("asyncio.sleep", new_callable=AsyncMock)  # Speed up retry delay
    async def test_connection_error_triggers_reconnect(self, mock_sleep, mock_connect):
        """Test that connection errors trigger reconnection attempts."""
        mock_connect.side_effect = [
            ConnectionRefusedError("Connection refused"),  # First attempt fails
            asyncio.CancelledError(),  # Second attempt, cancel to stop test
        ]

        with self.assertRaises(
            asyncio.CancelledError
        ):  # Expect CancelledError from 2nd attempt
            await client.connect_and_send_updates()

        self.assertEqual(mock_connect.call_count, 2)
        mock_sleep.assert_called_once_with(client.RECONNECT_DELAY)


if __name__ == "__main__":
    unittest.main()
