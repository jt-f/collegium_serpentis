import asyncio
import json
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import websockets

# Ensure src.client.client can be imported.
# This might require adjusting PYTHONPATH or
# test runner configuration in a real setup.
# For now, let's assume it's handled by the test execution environment.
from src.client import client

# Store original values to restore after tests
ORIGINAL_SERVER_URL = client.SERVER_URL
ORIGINAL_STATUS_INTERVAL = client.STATUS_INTERVAL
ORIGINAL_RECONNECT_DELAY = client.RECONNECT_DELAY


class TestClientWebSocket(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Override config for testing to speed up tests
        client.SERVER_URL = "ws://fake-server:1234"
        client.STATUS_INTERVAL = 0.1  # Faster updates for testing
        client.RECONNECT_DELAY = 0.1  # Faster reconnects for testing
        client.CLIENT_ID = str(uuid.uuid4())  # Ensure a fresh client ID for each test

    def tearDown(self):
        # Restore original config
        client.SERVER_URL = ORIGINAL_SERVER_URL
        client.STATUS_INTERVAL = ORIGINAL_STATUS_INTERVAL
        client.RECONNECT_DELAY = ORIGINAL_RECONNECT_DELAY

    @patch("src.client.client.websockets.connect")
    async def test_successful_connection_and_status_send(self, mock_connect):
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()
        mock_websocket.recv = AsyncMock(
            return_value=json.dumps(
                {"status": "registered", "client_id": client.CLIENT_ID}
            )
        )  # Mock server registration confirmation
        mock_connect.return_value.__aenter__.return_value = mock_websocket

        # Run the client for a short period to allow a few status updates
        # We need to run connect_and_send_updates in a task and then cancel it
        connect_task = asyncio.create_task(client.connect_and_send_updates())
        await asyncio.sleep(
            client.STATUS_INTERVAL * 2.5
        )  # Allow for at least two updates
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            print(
                """Client task successfully cancelled for
                test_successful_connection_and_status_send"""
            )

        mock_connect.assert_called_once_with(client.SERVER_URL)
        self.assertTrue(
            mock_websocket.send.call_count >= 2
        )  # Check if at least two status updates were sent

        # Check the format of the first sent message
        sent_data_str = mock_websocket.send.call_args_list[0][0][0]
        sent_data = json.loads(sent_data_str)

        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertIn("status", sent_data)
        self.assertIn("timestamp", sent_data["status"])
        self.assertIn("cpu_usage", sent_data["status"])
        self.assertIn("memory_usage", sent_data["status"])
        datetime.fromisoformat(
            sent_data["status"]["timestamp"]
        )  # Validate timestamp format

    async def test_status_update_format(self):
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()

        # Call send_status_update directly
        await client.send_status_update(mock_websocket)

        mock_websocket.send.assert_called_once()
        sent_data_str = mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_data_str)

        self.assertEqual(sent_data["client_id"], client.CLIENT_ID)
        self.assertIn("status", sent_data)
        self.assertIsInstance(sent_data["status"], dict)
        self.assertIn("timestamp", sent_data["status"])
        self.assertIsInstance(sent_data["status"]["timestamp"], str)
        # Check if timestamp is valid ISO 8601
        try:
            datetime.fromisoformat(
                sent_data["status"]["timestamp"].replace("Z", "+00:00")
            )
        except ValueError:
            self.fail("Timestamp is not in valid ISO 8601 format")

        self.assertIn("cpu_usage", sent_data["status"])
        self.assertIsInstance(sent_data["status"]["cpu_usage"], float)
        self.assertIn("memory_usage", sent_data["status"])
        self.assertIsInstance(sent_data["status"]["memory_usage"], float)

    @patch("src.client.client.websockets.connect")
    async def test_connection_refused_and_reconnect(self, mock_connect):
        # Mock connect to raise ConnectionRefusedError on first call, then succeed
        mock_successful_websocket = AsyncMock()
        mock_successful_websocket.send = AsyncMock()
        # Mock a registration confirmation to allow the loop
        #  to proceed after successful connection
        mock_successful_websocket.recv = AsyncMock(
            return_value=json.dumps(
                {"status": "registered", "client_id": client.CLIENT_ID}
            )
        )

        mock_connect.side_effect = [
            ConnectionRefusedError("Test connection refused"),
            MagicMock(
                __aenter__=AsyncMock(return_value=mock_successful_websocket)
            ),  # Successful connection
        ]

        connect_task = asyncio.create_task(client.connect_and_send_updates())
        # Allow time for one failed attempt, delay, and one successful
        #  connection + one update
        await asyncio.sleep(client.RECONNECT_DELAY * 1.5 + client.STATUS_INTERVAL * 1.5)
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            print(
                """Client task successfully cancelled for
                test_connection_refused_and_reconnect"""
            )

        self.assertEqual(mock_connect.call_count, 2)  # Should try to connect twice
        # Ensure the first call was to the original SERVER_URL
        mock_connect.assert_any_call(client.SERVER_URL)
        # After successful connection, send should have been called
        self.assertTrue(mock_successful_websocket.send.called)

    @patch("src.client.client.websockets.connect")
    async def test_connection_closed_and_reconnect(self, mock_connect):
        mock_websocket_initial = AsyncMock()
        # Simulate ConnectionClosedError when send is called
        mock_websocket_initial.send = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosedError(None, None)
        )
        mock_websocket_initial.recv = AsyncMock(
            return_value=json.dumps(
                {"status": "registered", "client_id": client.CLIENT_ID}
            )
        )

        mock_websocket_reconnect = AsyncMock()
        mock_websocket_reconnect.send = AsyncMock()
        mock_websocket_reconnect.recv = AsyncMock(
            return_value=json.dumps(
                {"status": "registered", "client_id": client.CLIENT_ID}
            )
        )

        # First connection is successful, then send fails,
        # then reconnects successfully
        mock_connect.side_effect = [
            MagicMock(__aenter__=AsyncMock(return_value=mock_websocket_initial)),
            MagicMock(__aenter__=AsyncMock(return_value=mock_websocket_reconnect)),
        ]

        connect_task = asyncio.create_task(client.connect_and_send_updates())
        # Allow time for:
        # 1. First connection
        # 2. First send attempt (fails, triggers reconnect delay)
        # 3. Reconnect delay
        # 4. Second connection
        # 5. Second send attempt (should succeed)
        await asyncio.sleep(
            client.STATUS_INTERVAL
            + client.RECONNECT_DELAY
            + client.STATUS_INTERVAL * 1.5
        )
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            print(
                """Client task successfully cancelled for
                test_connection_closed_and_reconnect"""
            )
        # connect, send fails, reconnect
        self.assertEqual(mock_connect.call_count, 2)
        # First send was attempted
        self.assertTrue(mock_websocket_initial.send.called)
        self.assertTrue(
            mock_websocket_reconnect.send.called
        )  # Second send was successful after reconnect

    @patch("src.client.client.websockets.connect")
    async def test_generic_exception_and_reconnect(self, mock_connect):
        # Mock connect to raise a generic Exception on first call, then succeed
        mock_successful_websocket = AsyncMock()
        mock_successful_websocket.send = AsyncMock()
        mock_successful_websocket.recv = AsyncMock(
            return_value=json.dumps(
                {"status": "registered", "client_id": client.CLIENT_ID}
            )
        )

        mock_connect.side_effect = [
            Exception("Test generic error"),
            MagicMock(__aenter__=AsyncMock(return_value=mock_successful_websocket)),
        ]

        connect_task = asyncio.create_task(client.connect_and_send_updates())
        await asyncio.sleep(client.RECONNECT_DELAY * 1.5 + client.STATUS_INTERVAL * 1.5)
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            print(
                """Client task successfully cancelled for
                test_generic_exception_and_reconnect"""
            )

        self.assertEqual(mock_connect.call_count, 2)
        mock_connect.assert_any_call(client.SERVER_URL)
        self.assertTrue(mock_successful_websocket.send.called)

    # The client currently doesn't have specific logic for "processing" a
    # registration confirmation beyond
    # the server sending one. The client's main loop is to send status.
    # This test will verify that if a message is received (e.g. a
    # registration confirmation),
    # it does not break the client's operation of sending status updates.
    @patch("src.client.client.websockets.connect")
    async def test_receives_message_does_not_break_sending(self, mock_connect):
        mock_websocket = AsyncMock()
        mock_websocket.send = AsyncMock()

        # Simulate server sending a message (e.g., registration confirmation)
        # then the client trying to receive (which it doesn't explicitly do in
        #  its main loop after connect)
        # For this test, we'll assume the `connect` context manager might
        # involve a `recv`
        # or that a server might send an unsolicited message.
        # The key is that the send loop continues.
        async def mock_recv_then_block():
            await asyncio.sleep(0.01)  # give other tasks a chance to run
            return json.dumps(
                {"message": "some_server_message", "client_id": client.CLIENT_ID}
            )

        mock_websocket.recv = AsyncMock(side_effect=mock_recv_then_block)
        mock_connect.return_value.__aenter__.return_value = mock_websocket

        connect_task = asyncio.create_task(client.connect_and_send_updates())
        await asyncio.sleep(
            client.STATUS_INTERVAL * 2.5
        )  # Allow for a couple of updates
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            print(
                """Client task successfully cancelled for
                test_receives_message_does_not_break_sending"""
            )

        mock_connect.assert_called_once_with(client.SERVER_URL)
        self.assertTrue(mock_websocket.send.call_count >= 2)
        # mock_websocket.recv.assert_called() # This would depend on if the
        # client explicitly recvs


if __name__ == "__main__":
    unittest.main()
