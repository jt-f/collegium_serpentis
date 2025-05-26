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
        # Store original config for restoration
        self.original_server_url = client.SERVER_URL
        self.original_status_interval = client.STATUS_INTERVAL
        self.original_reconnect_delay = client.RECONNECT_DELAY
        self.original_client_id = client.CLIENT_ID
        self.original_is_paused = client.is_paused

        # Override config for testing
        client.SERVER_URL = "ws://fake-server:1234/ws"
        client.STATUS_INTERVAL = 0.1
        client.RECONNECT_DELAY = 0.1
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False  # Reset pause state

    def tearDown(self):
        # Restore original config
        client.SERVER_URL = self.original_server_url
        client.STATUS_INTERVAL = self.original_status_interval
        client.RECONNECT_DELAY = self.original_reconnect_delay
        client.CLIENT_ID = self.original_client_id
        client.is_paused = self.original_is_paused

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
        # Store original values for restoration
        self.original_client_id = client.CLIENT_ID
        self.original_is_paused = client.is_paused

        # Set test values
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False
        client.manual_disconnect_initiated = False  # Ensure flag is reset

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

        # Store the iterator class instance
        message_iter = self.create_message_iterator(messages)

        # Mock __aiter__ to return the instance directly, not a method
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

            # Expected print call for unknown command
            expected_msg = (
                f"Client {client.CLIENT_ID}: "
                f"Unknown command received: unknown_command"
            )
            mock_print.assert_any_call(expected_msg)

    async def test_error_processing_message_after_json_decode(self):
        """Test error handling when an exception occurs after JSON decoding
        but during command processing logic."""
        # This message will be successfully decoded by json.loads
        # but we will mock the command handling part to raise an exception.
        # Use a known command like "pause" to ensure send_status_message is called.
        client.is_paused = False  # Ensured by setUp, but explicit for clarity
        problematic_message_dict = {"command": "pause"}  # Changed from "cause_error"
        problematic_message_json = json.dumps(problematic_message_dict)

        mock_websocket = self.mock_websocket_with_messages([problematic_message_json])

        # Mock the part of the code that processes the command after json.loads
        # to simulate an error during command handling.
        # For the "pause" command, send_status_message is called.
        # We patch send_status_message to raise an exception.

        simulated_error_text = (
            "Simulated error during pause command processing"  # Changed
        )

        # We need to patch where 'send_status_message' is LOOKED UP from,
        # which is 'src.client.client.send_status_message'
        with patch(
            "src.client.client.send_status_message",
            side_effect=Exception(simulated_error_text),
        ), patch("builtins.print") as mock_print:

            # We expect listen_for_commands to catch the exception and continue
            # processing (or in this case, finish as it's the only message)
            await client.listen_for_commands(mock_websocket)

            # Check that the specific error message was printed
            expected_error_print = (
                f"Client {client.CLIENT_ID}: Error processing command: "
                f"{simulated_error_text}"
            )
            # Also, the "Received command" print should have occurred
            expected_receipt_print = (
                f"Client {client.CLIENT_ID}: Received command: pause"
            )
            # And the "Paused command received" print
            expected_pause_ack_print = (
                f"Client {client.CLIENT_ID}: Paused command received. "
                f"Halting status updates."
            )

            actual_calls = [call_args[0][0] for call_args in mock_print.call_args_list]
            self.assertIn(expected_receipt_print, actual_calls)
            self.assertIn(expected_pause_ack_print, actual_calls)
            self.assertIn(expected_error_print, actual_calls)

    async def test_non_command_message(self):
        """Test that messages without a 'command' field are handled."""
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
        close_frame = Close(1000, "Normal closure")
        connection_closed = ConnectionClosed(close_frame, None)

        # Create a proper mock for rcvd attribute
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1000
        mock_rcvd.reason = "Normal closure"
        connection_closed.rcvd = mock_rcvd

        mock_websocket = AsyncMock()

        # Create an async iterator that raises the exception
        class AsyncIterRaiser:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        # Configure the mock to use our custom async iterator
        mock_websocket.__aiter__ = lambda self: AsyncIterRaiser()

        # Reset the flag before the test
        client.manual_disconnect_initiated = False

        with self.assertRaises(ConnectionClosed):  # Expect ConnectionClosed
            await client.listen_for_commands(mock_websocket)

        # Check the flag
        self.assertTrue(client.manual_disconnect_initiated)

        # Optionally, check the specific ConnectionClosed exception details
        # self.assertEqual(context.exception.rcvd.code, 1000) # This was in the setup, so it should be true

    async def test_connection_closed_other_code(self):
        """Test handling of connection closed with other codes (should reconnect)."""
        # Mock ConnectionClosed exception with code other than 1000
        close_frame = Close(1001, "Going away")
        connection_closed = ConnectionClosed(close_frame, None)

        # Create a proper mock for rcvd attribute
        mock_rcvd = MagicMock()
        mock_rcvd.code = 1001
        mock_rcvd.reason = "Going away"
        connection_closed.rcvd = mock_rcvd

        mock_websocket = AsyncMock()

        # Create an async iterator that raises the exception
        class AsyncIterRaiser:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise connection_closed

        # Configure the mock to use our custom async iterator
        mock_websocket.__aiter__ = lambda self: AsyncIterRaiser()

        with self.assertRaises(ConnectionClosed):
            await client.listen_for_commands(mock_websocket)

    async def test_client_initiated_disconnect_workflow(self):
        """Test that disconnect command processing works correctly."""
        # This test focuses on the disconnect command processing rather than
        # the full integration test which has async task management complexity

        disconnect_message = {"command": "disconnect"}
        mock_websocket = self.mock_websocket_with_messages(
            [json.dumps(disconnect_message)]
        )
        # Reset the flag before the test
        client.manual_disconnect_initiated = False

        # listen_for_commands should complete without raising an exception here
        await client.listen_for_commands(mock_websocket)

        # Verify that the flag was set
        self.assertTrue(client.manual_disconnect_initiated)

        # Verify that disconnect acknowledgment was sent
        mock_websocket.send.assert_called_once()
        sent_data = json.loads(mock_websocket.send.call_args[0][0])

        # Check the acknowledgment structure
        self.assertEqual(sent_data["status"]["acknowledged_command"], "disconnect")
        self.assertEqual(sent_data["status"]["client_state"], "disconnecting")
        self.assertIn("timestamp", sent_data["status"])


class TestPeriodicStatusSender(unittest.IsolatedAsyncioTestCase):
    """Test periodic status update functionality."""

    def setUp(self):
        # Store original values for restoration
        self.original_client_id = client.CLIENT_ID
        self.original_status_interval = client.STATUS_INTERVAL
        self.original_is_paused = client.is_paused

        # Set test values
        client.CLIENT_ID = str(uuid.uuid4())
        client.STATUS_INTERVAL = 0.05  # Very fast for testing
        client.is_paused = False
        client.manual_disconnect_initiated = False  # Ensure flag is reset

    def tearDown(self):
        client.CLIENT_ID = self.original_client_id
        client.STATUS_INTERVAL = self.original_status_interval
        client.is_paused = self.original_is_paused

    @patch("src.client.client.send_full_status_update", new_callable=AsyncMock)
    async def test_sends_status_when_not_paused(self, mock_send_full_status_update):
        """Test that status updates are sent when client is not paused."""
        mock_websocket = AsyncMock()

        # Run for a short time and cancel
        task = asyncio.create_task(
            client.send_status_update_periodically(mock_websocket)
        )
        await asyncio.sleep(client.STATUS_INTERVAL * 2.5)  # Allow for ~2 updates
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
        # Store original values for restoration
        self.original_server_url = client.SERVER_URL
        self.original_reconnect_delay = client.RECONNECT_DELAY
        self.original_client_id = client.CLIENT_ID
        self.original_is_paused = client.is_paused

        # Set test values
        client.SERVER_URL = "ws://fake-server:1234/ws"
        client.RECONNECT_DELAY = 0.01  # Very fast for testing
        client.CLIENT_ID = str(uuid.uuid4())
        client.is_paused = False  # Ensure reset
        client.manual_disconnect_initiated = False  # Ensure flag is reset

    def tearDown(self):
        client.SERVER_URL = self.original_server_url
        client.RECONNECT_DELAY = self.original_reconnect_delay
        client.CLIENT_ID = self.original_client_id
        client.is_paused = self.original_is_paused

    @patch("src.client.client.websockets.connect")
    async def test_successful_connection_and_initial_status(self, mock_connect):
        """Test successful connection and initial status message."""
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()

        # Mock the async context manager
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_websocket)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        # Create a flag to control when the main function should exit
        connection_established = asyncio.Event()

        with patch("asyncio.create_task") as mock_create_task:
            # Mock listener and sender tasks to set the event when "created"
            listener_task_mock = AsyncMock()
            sender_task_mock = AsyncMock()

            # Make wait wait for the event and then raise an exception to exit
            async def mock_wait(*tasks, return_when):
                connection_established.set()  # Signal that connection was established
                await asyncio.sleep(0.01)  # Brief delay to allow processing
                # Simulate one task completing and raising the disconnect
                # For simplicity, we'll assume the listener task is the one to complete/raise
                # In a real scenario, one of the tasks in *tasks would raise this.
                # We need to return a (done, pending) tuple similar to asyncio.wait
                # Let's assume listener_task_mock is the one that 'completes'
                # and causes the disconnect for testing purposes.
                # We'll need to ensure listener_task_mock is one of the tasks passed.
                for task_arg_tuple in tasks:
                    for _ in task_arg_tuple:  # Renamed t to _
                        # This is a simplification. In reality, one task would complete.
                        # For this test, we are forcing an exit.
                        pass  # Iterate through, but we will raise to exit
                raise client.ClientInitiatedDisconnect("Test exit from mock_wait")

            def create_task_side_effect(coro):
                if "listen_for_commands" in str(coro):
                    return listener_task_mock
                elif "send_status_update_periodically" in str(coro):
                    return sender_task_mock
                return AsyncMock()

            mock_create_task.side_effect = create_task_side_effect

            with patch("asyncio.wait", side_effect=mock_wait):  # Patched asyncio.wait
                # Run connect_and_send_updates until it exits
                await client.connect_and_send_updates()

        # Wait for connection to be established
        await connection_established.wait()

        mock_connect.assert_called_with(client.SERVER_URL)
        self.assertTrue(mock_websocket.send.called)

        # Verify initial status message format from the first call
        first_call_args = mock_websocket.send.call_args_list[0][0]
        sent_data = json.loads(first_call_args[0])
        assert sent_data["client_id"] == client.CLIENT_ID
        assert sent_data["status"]["client_state"] == "running"
        # Client does not send 'connected_at', server adds it.
        # Assertions should match what client sends.
        assert "client_name" in sent_data["status"]
        assert "client_role" in sent_data["status"]
        assert "client_type" in sent_data["status"]
        assert "timestamp" in sent_data["status"]  # from get_current_status_payload

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
            asyncio.CancelledError(
                "Simulated cancellation to stop test"
            ),  # Second attempt, cancel to stop test
        ]

        with self.assertRaises(
            asyncio.CancelledError
        ):  # Expect CancelledError from 2nd attempt
            await client.connect_and_send_updates()

        self.assertEqual(mock_connect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
