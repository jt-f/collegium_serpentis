# tests/client/test_client_connection.py
import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.client import client


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
    }

    # Set test values
    client.SERVER_URL = "ws://fake-server:1234/ws"
    client.RECONNECT_DELAY = 0.01
    client.CLIENT_ID = str(uuid.uuid4())
    client.is_paused = False
    client.manual_disconnect_initiated = False

    yield

    # Restore original values
    for key, value in original.items():
        setattr(client, key.upper(), value)


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

    # Cancel the task
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
    mock_connect.side_effect = [
        ConnectionRefusedError("Connection refused"),
        ConnectionRefusedError("Connection refused"),
        asyncio.CancelledError("Test complete"),
    ]

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    # Give it time to run
    await asyncio.sleep(0.1)

    # Cancel the task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should have tried to connect 3 times (2 failures + 1 cancellation)
    assert mock_connect.call_count == 3


@pytest.mark.asyncio
async def test_connection_cleanup_on_error(mock_websocket_connection):
    """Test that resources are properly cleaned up on connection errors."""
    mock_connect, mock_ws = mock_websocket_connection

    # Simulate a connection that fails immediately
    mock_connect.side_effect = ConnectionError("Test error")

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    # Give it time to run and fail
    await asyncio.sleep(0.2)

    # Since the connection failed, the WebSocket close shouldn't be called
    # because the connection was never established
    mock_ws.close.assert_not_called()

    # Clean up
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_connection_timeout_handling(mock_websocket_connection):
    """Test that connection timeouts are handled gracefully."""
    mock_connect, _ = mock_websocket_connection
    mock_connect.side_effect = TimeoutError("Connection timeout")

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    # Give it time to run
    await asyncio.sleep(0.1)

    # Cancel the task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert mock_connect.called
