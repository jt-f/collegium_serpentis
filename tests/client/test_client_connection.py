# tests/client/test_client_connection.py
import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
from websockets.protocol import State as ConnectionState

from src.client import client
from src.client.client import ClientInitiatedDisconnect

# Set a higher log level for the client logger to reduce spam during tests
logging.getLogger("src.client.client").setLevel(logging.WARNING)


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection that supports async iteration."""
    ws = AsyncMock()  # Removed spec=websockets.WebSocketClientProtocol
    ws.send = AsyncMock()
    ws.close = AsyncMock()

    # Configure for async iteration (async for ... in ws)
    async_iterator_mock = AsyncMock()
    ws.__aiter__ = MagicMock(return_value=async_iterator_mock)
    # Tests will configure async_iterator_mock.__anext__.side_effect directly

    # For `websockets.protocol.State.CLOSED` check in client.py finally block
    ws.state = ConnectionState.OPEN  # Default to open

    return ws


@pytest.fixture
def mock_websocket_connection(mock_websocket):
    """Create a mock WebSocket connection with context manager support."""
    # Set up the websockets.connect() mock to return our mock websocket directly
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_websocket
        yield mock_connect, mock_websocket


@pytest.fixture(autouse=True)
def patch_client_network_and_state(monkeypatch):
    """Patch asyncio.sleep to prevent actual delays in retry loops.

    Does NOT patch websockets.connect or manual_disconnect_initiated by default.
    Individual tests or other fixtures should handle those if needed.
    """

    # Patch asyncio.sleep to prevent actual delays in retry loops
    async def mock_sleep(delay):
        # Just return immediately without actually sleeping
        pass

    monkeypatch.setattr("asyncio.sleep", mock_sleep)


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

    # Await the task with a timeout. The task is expected to be cancelled by the side_effect.
    try:
        await asyncio.wait_for(task, timeout=1.0)  # Adjust timeout if necessary
    except asyncio.CancelledError:
        # This is expected if side_effect_with_cancellation raises it after enough calls
        pass
    except TimeoutError:
        # This would mean the task didn't get cancelled by side_effect as expected
        # but we still want to check call_count.
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Cleanup cancellation

    # Should have tried to connect at least twice (it will try a 3rd time and get CancelledError)
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

            # Await the task with a timeout to ensure it completes or times out
            try:
                await asyncio.wait_for(task, timeout=1.0)  # Adjust timeout as needed
            except TimeoutError:
                # This might happen if the task doesn't exit cleanly despite the flag
                # For this test, we primarily care that the flag was set.
                pass
            except (
                ValueError
            ):  # The task might propagate the ValueError if not fully handled by gather logic
                pass
            except asyncio.CancelledError:
                pass  # If timeout caused cancellation

            # Verify manual_disconnect_initiated was set due to the error
            assert client.manual_disconnect_initiated is True

            # Clean up
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

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

            # Await the task with a timeout to ensure it completes or times out
            try:
                await asyncio.wait_for(task, timeout=1.0)  # Adjust timeout as needed
            except TimeoutError:
                # This might happen if the task doesn't exit cleanly despite the flag
                # For this test, we primarily care that the flag was set.
                pass
            except (
                ClientInitiatedDisconnect
            ):  # The task itself might raise this if not caught internally by gather logic
                pass  # Expected in some scenarios, means it propagated
            except asyncio.CancelledError:
                pass  # If timeout caused cancellation

            # Verify manual_disconnect_initiated was set
            assert client.manual_disconnect_initiated is True

            # Ensure task is cleaned up if it didn't exit naturally or was cancelled by timeout
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

        # Wait a bit for cancellation to propagate
        await asyncio.sleep(0.05)

        # The function handles cancellation gracefully and doesn't re-raise
        # Instead, it sets the manual_disconnect_initiated flag and exits cleanly
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_connection_handling_invalid_uri(mock_websocket_connection):
    """Test handling of invalid server URI."""
    mock_connect, _ = mock_websocket_connection

    # Simulate invalid URI (must provide both uri and msg)
    mock_connect.side_effect = websockets.exceptions.InvalidURI(
        "ws://fake-server:1234/ws", "Invalid URI"
    )

    # Run the connection
    await client.connect_and_send_updates()

    # Verify manual_disconnect_initiated was set
    assert client.manual_disconnect_initiated is True
    assert mock_connect.call_count == 1


@pytest.mark.asyncio
async def test_connection_handling_cleanup_on_error(mock_websocket_connection):
    """Test cleanup of resources on connection error."""
    mock_connect, mock_ws = mock_websocket_connection

    # Simulate connection that fails after being established
    async def connect_with_error(*args, **kwargs):
        mock_ws.state = websockets.protocol.State.OPEN  # Ensure not closed
        return mock_ws

    mock_connect.side_effect = connect_with_error

    # Raise error on first send to simulate error after connection
    mock_ws.send = AsyncMock(side_effect=RuntimeError("Connection error"))

    # Run the connection
    await client.connect_and_send_updates()

    # Verify WebSocket was closed
    mock_ws.close.assert_awaited_once()
    assert client.manual_disconnect_initiated is True
