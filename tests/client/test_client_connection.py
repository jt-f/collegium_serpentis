# tests/client/test_client_connection.py
import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import websockets

from src.client import client
from src.client.client import ClientInitiatedDisconnect


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    return ws


@pytest.fixture
def mock_websocket_connection(mock_websocket):
    """Create a mock WebSocket connection with context manager support."""
    # Set up the websockets.connect() mock to return our mock websocket directly
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_websocket
        yield mock_connect, mock_websocket


@pytest.fixture(autouse=True)
def setup_client():
    """Setup and teardown for client tests."""
    # Save original values
    original = {
        "server_url": client.SERVER_URL,
        "reconnect_delay": client.RECONNECT_DELAY,
        "client_id": client.CLIENT_ID,
        "is_paused": client.is_paused,
        "manual_disconnect_initiated": client.manual_disconnect_initiated,
    }

    # Set test values
    client.SERVER_URL = "ws://fake-server:1234/ws"
    client.RECONNECT_DELAY = 0.01
    client.CLIENT_ID = str(uuid.uuid4())
    client.is_paused = False
    client.manual_disconnect_initiated = False

    yield

    # Restore original values and ensure clean state
    for key, value in original.items():
        setattr(client, key.upper(), value)

    # Ensure manual_disconnect_initiated is reset to prevent test bleed
    client.manual_disconnect_initiated = False


@pytest.mark.asyncio
async def test_connection_establishment(mock_websocket_connection):
    """Test that a connection can be established and initial handshake works."""
    mock_connect, mock_ws = mock_websocket_connection

    # Set up the mock WebSocket to simulate a successful connection
    mock_ws.recv.side_effect = [
        json.dumps({"type": "welcome", "client_id": "test-client"}),
        asyncio.CancelledError,  # To exit the loop
    ]

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    # Give it time to run
    await asyncio.sleep(0.1)

    # Cancel the task and ensure clean shutdown
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verify the connection was attempted
    mock_connect.assert_called_once_with(client.SERVER_URL)

    # Verify at least registration and initial status messages were sent
    # (there might be periodic updates too)
    assert mock_ws.send.call_count >= 2

    # Check the first call (registration message)
    first_call_data = json.loads(mock_ws.send.call_args_list[0][0][0])
    assert first_call_data["client_id"] == client.CLIENT_ID
    assert "status" in first_call_data
    assert first_call_data["status"]["client_role"] == client.CLIENT_ROLE
    assert first_call_data["status"]["client_type"] == client.CLIENT_TYPE
    assert first_call_data["status"]["client_name"] == client.CLIENT_NAME
    assert first_call_data["status"]["client_state"] == "initializing"

    # Check the second call (initial status message)
    second_call_data = json.loads(mock_ws.send.call_args_list[1][0][0])
    assert "client_id" in second_call_data
    assert "status" in second_call_data
    assert second_call_data["status"]["client_name"] == client.CLIENT_NAME
    assert second_call_data["status"]["client_state"] == "running"


@pytest.mark.asyncio
async def test_connection_retry_on_failure(mock_websocket_connection):
    """Test that connection retries on failure with exponential backoff."""
    mock_connect, _ = mock_websocket_connection

    # Create a cancellation event that we can trigger after some attempts
    call_count = 0

    def side_effect_with_cancellation(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ConnectionRefusedError("Connection refused")
        else:
            # After 2 attempts, trigger cancellation
            raise asyncio.CancelledError("Test complete")

    mock_connect.side_effect = side_effect_with_cancellation

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    # Give it time to make a few attempts
    await asyncio.sleep(0.1)

    # Ensure the task is properly cleaned up
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should have tried to connect at least twice
    assert call_count >= 2


@pytest.mark.asyncio
async def test_connection_cleanup_on_error(mock_websocket_connection):
    """Test that resources are properly cleaned up on connection errors."""
    mock_connect, mock_ws = mock_websocket_connection

    # Simulate a connection that fails immediately
    mock_connect.side_effect = ConnectionError("Test error")

    # Run the connection in the background with timeout to prevent infinite loops
    async def run_with_timeout():
        task = asyncio.create_task(client.connect_and_send_updates())
        try:
            await asyncio.wait_for(task, timeout=0.2)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await run_with_timeout()

    # Since the connection failed, the WebSocket close shouldn't be called
    # because the connection was never established
    mock_ws.close.assert_not_called()


@pytest.mark.asyncio
async def test_connection_timeout_handling():
    """Test that connection timeouts are handled gracefully."""
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        # Simulate timeout on connection
        mock_connect.side_effect = TimeoutError("Connection timed out")

        # Run the client with a short timeout
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Set up sleep to raise CancelledError after first call to exit loop
            mock_sleep.side_effect = [None, asyncio.CancelledError("Test complete")]

            try:
                await client.connect_and_send_updates()
            except asyncio.CancelledError:
                pass

        # Verify connection was attempted
        assert mock_connect.call_count >= 1
        # Verify sleep was called with the reconnect delay
        assert any(
            call.args[0] == client.RECONNECT_DELAY for call in mock_sleep.call_args_list
        )


class TestConnectAndSendUpdates:
    """Tests for the connect_and_send_updates function."""

    @pytest.mark.asyncio
    async def test_task_result_handling(self, mock_websocket_connection, caplog):
        """Test handling of task results in connect_and_send_updates."""
        mock_connect, mock_websocket = mock_websocket_connection

        # Set up the websocket connection to succeed initially
        call_count = 0

        async def connect_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_websocket
            else:
                # On subsequent calls, raise CancelledError to exit
                raise asyncio.CancelledError("Test cleanup")

        mock_connect.side_effect = connect_side_effect

        # Mock gather to simulate task completion with error
        with patch("asyncio.gather") as mock_gather:
            mock_gather.return_value = [None, ValueError("Test error")]

            task = asyncio.create_task(client.connect_and_send_updates())

            # Let it run briefly
            await asyncio.sleep(0.1)

            # Clean up
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Verify manual_disconnect_initiated was set due to the error
            assert client.manual_disconnect_initiated is True

    @pytest.mark.asyncio
    async def test_client_initiated_disconnect(self, mock_websocket_connection):
        """Test handling of ClientInitiatedDisconnect in tasks."""
        mock_connect, mock_websocket = mock_websocket_connection

        call_count = 0

        async def connect_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_websocket
            else:
                raise asyncio.CancelledError("Test cleanup")

        mock_connect.side_effect = connect_side_effect

        # Mock gather to simulate ClientInitiatedDisconnect
        with patch("asyncio.gather") as mock_gather:
            mock_gather.return_value = [ClientInitiatedDisconnect(), None]

            task = asyncio.create_task(client.connect_and_send_updates())

            # Let it run briefly
            await asyncio.sleep(0.1)

            # Verify manual_disconnect_initiated was set
            assert client.manual_disconnect_initiated is True

            # Clean up
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_connection_closed_handling(self, mock_websocket_connection):
        """Test handling of ConnectionClosed with different close codes."""
        mock_connect, mock_websocket = mock_websocket_connection

        # Save original client ID to verify it changes
        original_client_id = client.CLIENT_ID

        # Simulate connection closed with code 4008 (duplicate client ID)
        closed_exc = websockets.exceptions.ConnectionClosed(
            rcvd=websockets.frames.Close(4008, "Duplicate client ID"), sent=None
        )

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # First two calls get 4008 error
                raise closed_exc
            else:  # Third call gets cancelled to exit loop
                raise asyncio.CancelledError("Test cleanup")

        mock_connect.side_effect = side_effect

        task = asyncio.create_task(client.connect_and_send_updates())

        # Let the task run briefly to process the 4008 error
        await asyncio.sleep(0.1)

        # Verify client ID was regenerated
        assert client.CLIENT_ID != original_client_id

        # Clean up - ensure task is cancelled and awaited
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancellation_handling(self, mock_websocket_connection):
        """Test that cancellation is handled gracefully."""
        mock_connect, mock_websocket = mock_websocket_connection

        # Set up a slow connection to ensure we can cancel it
        async def slow_connect(*args, **kwargs):
            await asyncio.sleep(1.0)  # This should be cancelled before completing
            return mock_websocket

        mock_connect.side_effect = slow_connect

        # Create a task that will be cancelled
        task = asyncio.create_task(client.connect_and_send_updates())

        # Let it start
        await asyncio.sleep(0.1)

        # Cancel the task
        task.cancel()

        # The function handles cancellation gracefully and doesn't re-raise
        # Instead, it sets the manual_disconnect_initiated flag and exits cleanly
        try:
            await task
        except asyncio.CancelledError:
            # This should not happen as the function handles cancellation internally
            pass

        # Verify manual_disconnect_initiated was set
        assert client.manual_disconnect_initiated is True
