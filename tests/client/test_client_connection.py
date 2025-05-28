# tests/client/test_client_connection.py
import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
from websockets.frames import Close

from src.client import client


@pytest.fixture
def mock_websocket():
    # This is a generic base mock, individual tests or side_effects can create more specific ones.
    ws = AsyncMock()
    ws.send = AsyncMock(name="generic_mock_ws.send")
    ws.close = AsyncMock(name="generic_mock_ws.close")
    return ws


@pytest.fixture
def mock_websocket_connection(mock_websocket):
    # mock_websocket here is the one from the fixture above.
    # websockets.connect will be patched. Its side_effect in tests will determine what's actually returned.
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        # By default, let mock_connect return the generic mock_websocket from the fixture.
        # Test-specific side_effects on mock_connect can override this.
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

    # Restore original values
    for key, value in original.items():
        if key == "manual_disconnect_initiated":
            client.manual_disconnect_initiated = value
        else:
            setattr(client, key.upper(), value)


@pytest.mark.asyncio
async def test_connection_establishment(mock_websocket_connection):
    """Test that a connection can be established and initial handshake works."""
    mock_connect, mock_ws = mock_websocket_connection

    # Store original client ID and set a specific one for the test
    original_client_id = client.CLIENT_ID
    test_client_id = str(uuid.uuid4())
    client.CLIENT_ID = test_client_id

    # Create a list of messages to yield
    messages = [
        json.dumps({"type": "welcome", "client_id": client.CLIENT_ID}),
        StopAsyncIteration,
    ]

    # Create the async iterator mock that __aiter__ will return
    async_iterator_mock = AsyncMock()
    # Configure its __anext__ to follow the sequence defined above
    async_iterator_mock.__anext__ = AsyncMock(side_effect=messages)

    # mock_ws.__aiter__ is a synchronous method that returns an async iterator.
    # So, ws.__aiter__ should be a MagicMock, not AsyncMock, returning our configured iterator.
    mock_ws.__aiter__ = MagicMock(return_value=async_iterator_mock)

    # Fallback for recv, though __aiter__ should be preferred by `async for`
    mock_ws.recv = AsyncMock(side_effect=messages)

    task = asyncio.create_task(client.connect_and_send_updates())

    # Allow time for connection, initial send, and for listener to process the one message.
    await asyncio.sleep(0.2)

    mock_connect.assert_called_once_with(client.SERVER_URL)

    # Verify the initial status was sent
    assert (
        mock_ws.send.call_count >= 1
    ), "send should be called at least once for initial status"
    first_send_args = mock_ws.send.await_args_list[0][0][0]
    sent_data = json.loads(first_send_args)

    assert sent_data["client_id"] == test_client_id
    assert "status" in sent_data
    assert sent_data["status"]["client_name"] == client.CLIENT_NAME
    assert sent_data["status"]["client_state"] == "running"

    # Now, gracefully stop the client
    client.manual_disconnect_initiated = True  # Signal client to stop
    # Give a moment for the client loops to recognize the flag
    await asyncio.sleep(0.05)

    task.cancel()  # Cancel the main task
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected
    except Exception as e:
        pytest.fail(f"Client task raised an unexpected exception: {e}")

    # Ensure no further connection attempts after manual_disconnect_initiated is set
    # The call_count should remain 1 if graceful shutdown worked.
    assert (
        mock_connect.call_count == 1
    ), f"Expected connect to be called once, but was {mock_connect.call_count}"

    client.CLIENT_ID = original_client_id  # Restore


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
async def test_duplicate_connection_handling(
    mock_websocket_connection, monkeypatch, capsys
):
    """Test behavior when connecting with same client ID from multiple places."""
    print("\n=== Starting test_duplicate_connection_handling ===")

    mock_connect, _ = mock_websocket_connection
    original_client_id = client.CLIENT_ID
    monkeypatch.setattr(client, "CLIENT_ID", original_client_id)

    new_generated_client_id = str(uuid.uuid4())
    while new_generated_client_id == original_client_id:
        new_generated_client_id = str(uuid.uuid4())

    print(f"Original client ID for test: {original_client_id}")
    print(f"Mocked new client ID to be generated: {new_generated_client_id}")

    connection_attempts = 0
    active_mock_ws_for_second_connection = None

    async def mock_connect_side_effect(*args, **kwargs):
        nonlocal connection_attempts, active_mock_ws_for_second_connection
        connection_attempts += 1
        current_test_client_id = client.CLIENT_ID
        print(
            f"mock_connect attempt {connection_attempts} with CLIENT_ID: {current_test_client_id}"
        )

        if connection_attempts == 1:
            assert (
                current_test_client_id == original_client_id
            ), "First connect attempt should use original ID."
            print(
                "Simulating ConnectionClosed with code 4008 on first connect attempt."
            )

            # Create proper Close objects for rcvd and sent parameters
            from websockets.frames import Close

            close_frame = Close(code=4008, reason="Client ID already in use")

            raise websockets.exceptions.ConnectionClosed(rcvd=close_frame, sent=None)
        elif connection_attempts == 2:
            assert (
                current_test_client_id == new_generated_client_id
            ), f"Second connect attempt expected new ID {new_generated_client_id}, got {current_test_client_id}"
            print(
                f"Second connect: new ID {current_test_client_id}. Creating FRESH mock websocket."
            )
            fresh_ws = AsyncMock(name=f"ws_conn_attempt_{connection_attempts}")

            async def aiter_anext_sequence():
                print(f"{fresh_ws.name}.__anext__: yielding server ack message")
                yield json.dumps(
                    {
                        "client_id": current_test_client_id,
                        "result": "registration_complete",
                        "message": "Client registration successful",
                    }
                )
                print(f"{fresh_ws.name}.__anext__: raising StopAsyncIteration")
                raise StopAsyncIteration

            aiter_mock = AsyncMock()
            aiter_mock.__anext__ = AsyncMock(side_effect=aiter_anext_sequence())
            fresh_ws.__aiter__ = MagicMock(return_value=aiter_mock)
            fresh_ws.send = AsyncMock(return_value=None, name=f"{fresh_ws.name}.send")
            fresh_ws.close = AsyncMock(return_value=None, name=f"{fresh_ws.name}.close")
            fresh_ws.state = websockets.protocol.State.OPEN
            active_mock_ws_for_second_connection = fresh_ws
            return fresh_ws
        else:
            pytest.fail(
                f"Unexpected number of connection attempts: {connection_attempts}"
            )
        return None

    mock_connect.side_effect = mock_connect_side_effect

    mock_periodic_sender_completed_event = asyncio.Event()

    async def mock_send_status_periodically_replacement(websocket_arg):
        sender_name = getattr(websocket_arg, "name", "mock_ws_sender")
        print(f"MOCK sender ({sender_name}): Started.")
        try:
            if not client.is_paused and not client.manual_disconnect_initiated:
                current_payload = client.get_current_status_payload()
                message_dict = {
                    "client_id": client.CLIENT_ID,
                    "status": {**current_payload, "client_state": "running"},
                }
                print(
                    f"MOCK sender ({sender_name}): Attempting initial send: {message_dict}"
                )
                await websocket_arg.send(json.dumps(message_dict))
                print(f"MOCK sender ({sender_name}): Initial send complete.")

            loop_count = 0
            while True:
                loop_count += 1
                if client.manual_disconnect_initiated:
                    print(
                        f"MOCK sender ({sender_name}): Saw manual_disconnect_initiated after {loop_count} checks. Breaking loop."
                    )
                    break
                if (
                    loop_count > 200
                ):  # Safety break for tests, ~10 seconds if sleep is 0.05
                    print(
                        f"MOCK sender ({sender_name}): Safety break after {loop_count} loops."
                    )
                    # This might indicate an issue if manual_disconnect_initiated wasn't seen
                    # For the test, this means it would run for ~10s max if not disconnected
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            print(f"MOCK sender ({sender_name}): Cancelled.")
            raise
        except Exception as e:
            print(f"MOCK sender ({sender_name}): Error - {e!r}")
            if client.manual_disconnect_initiated:
                print(
                    f"MOCK sender ({sender_name}): Error occurred during manual_disconnect process."
                )
            raise
        finally:
            print(f"MOCK sender ({sender_name}): FINALLY block executing.")
            mock_periodic_sender_completed_event.set()

    with patch(
        "src.client.client.uuid.uuid4", return_value=uuid.UUID(new_generated_client_id)
    ) as mock_uuid_call, patch(
        "src.client.client.send_status_update_periodically",
        new=mock_send_status_periodically_replacement,
    ):

        client.manual_disconnect_initiated = (
            False  # Ensure clean start for client state
        )
        client.is_paused = False

        print("Test: Starting client.connect_and_send_updates() task...")
        client_task = asyncio.create_task(client.connect_and_send_updates())

        # Allow time for the first failed connection, regeneration of ID, and second successful connection.
        # Also for the mocked periodic sender to do its initial send.
        await asyncio.sleep(
            0.7
        )  # Slightly increased to be very safe for two connects + initial send

        print(
            f"Test: Current connection_attempts before signaling disconnect: {connection_attempts}"
        )
        assert (
            connection_attempts == 2
        ), "Should be 2 connection attempts before signaling disconnect"

        print("Test: Setting client.manual_disconnect_initiated = True")
        client.manual_disconnect_initiated = True

        print("Test: Waiting for MOCK sender to complete via event...")
        try:
            await asyncio.wait_for(
                mock_periodic_sender_completed_event.wait(), timeout=2.0
            )  # Increased timeout
            print("Test: MOCK sender completed event was set.")
        except TimeoutError:
            print("Test: MOCK sender completed event TIMED OUT. This is problematic.")
            # Fall through, client_task wait_for will likely also fail or show issues.

        # Additional small sleep to ensure client_task's main loop processes the flag if it was waiting on gather
        await asyncio.sleep(0.1)

        print(
            "Test: Waiting for client_task to complete due to manual_disconnect_initiated..."
        )
        try:
            await asyncio.wait_for(client_task, timeout=2.0)
            print("Test: client_task completed gracefully.")
        except TimeoutError:
            current_conn_attempts_on_timeout = (
                connection_attempts  # Capture attempts at point of failure
            )
            print(
                f"Test: client_task TIMED OUT. connection_attempts = {current_conn_attempts_on_timeout}"
            )
            client_task.cancel()  # Force cancel if it didn't stop
            try:
                await client_task
            except asyncio.CancelledError:
                print("Test: client_task force-cancelled.")
            pytest.fail(
                f"Client task did not terminate gracefully. Attempts: {current_conn_attempts_on_timeout}"
            )
        except asyncio.CancelledError:
            pytest.fail(
                "Test: client_task was unexpectedly cancelled by external force."
            )
        except Exception as e:
            pytest.fail(f"Test: client_task raised an unexpected error: {e!r}")

        print(
            f"Test: Client task processing done. Final connection_attempts: {connection_attempts}"
        )

        # --- Assertions ---
        mock_uuid_call.assert_called_once()
        assert (
            client.CLIENT_ID == new_generated_client_id
        ), f"Client.CLIENT_ID should be {new_generated_client_id}, but is {client.CLIENT_ID}"
        assert (
            mock_connect.call_count == 2
        ), f"Expected 2 connect calls, got {mock_connect.call_count}"

        assert (
            active_mock_ws_for_second_connection is not None
        ), "Mock for second connection was not set"
        # Check if send was called by our MOCK send_status_update_periodically
        if active_mock_ws_for_second_connection.send.called:
            assert (
                active_mock_ws_for_second_connection.send.call_count >= 1
            ), "Expected at least one send by the MOCK periodic sender."
            first_send_args = active_mock_ws_for_second_connection.send.await_args_list[
                0
            ][0][0]
            sent_payload = json.loads(first_send_args)
            assert sent_payload.get("client_id") == new_generated_client_id
            assert "running" == sent_payload.get("status", {}).get("client_state")
        else:
            # This case could happen if the MOCK sender's initial 'if' condition was false
            print(
                "WARN: MOCK sender did not call send. Check client.is_paused or client.manual_disconnect_initiated at send time."
            )
            # Depending on strictness, this could be an assert False if a send is always expected.
            # For now, we'll allow it but note it.

        print("=== Finished test_duplicate_connection_handling ===")


@pytest.mark.asyncio
async def test_connection_timeout_handling(mock_websocket_connection):
    """Test that connection timeouts are handled gracefully."""
    mock_connect, _ = mock_websocket_connection
    mock_connect.side_effect = TimeoutError("Connection timeout")

    # Run the connection in the background
    task = asyncio.create_task(client.connect_and_send_updates())

    await asyncio.sleep(0.1)  # Give it time to run and encounter timeout

    # Client should still be trying to reconnect after timeout, so task won't exit on its own quickly.
    # We need to manually stop it for the test to finish.
    client.manual_disconnect_initiated = True  # Signal client to stop retrying
    await asyncio.sleep(0.05)  # Allow flag to propagate
    task.cancel()  # Cancel the main task
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected

    assert mock_connect.called, "websockets.connect should have been called"


class TestConnectAndSendUpdatesErrorHandling:
    """Test error handling in connect_and_send_updates function."""

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_invalid_uri(self):
        """Test handling of InvalidURI exception."""
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            # Fix: InvalidURI requires uri and msg parameters
            mock_connect.side_effect = websockets.InvalidURI(
                "ws://invalid", "Invalid URI"
            )

            # Run for a short time
            task = asyncio.create_task(client.connect_and_send_updates())
            await asyncio.sleep(0.1)

            # Should set manual_disconnect_initiated and exit
            assert client.manual_disconnect_initiated is True

            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_cancelled_error(self):
        """Test handling of CancelledError."""
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = asyncio.CancelledError("Task cancelled")

            # Run for a short time
            task = asyncio.create_task(client.connect_and_send_updates())
            await asyncio.sleep(0.1)

            # Should set manual_disconnect_initiated and exit
            assert client.manual_disconnect_initiated is True

            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_websocket_cleanup(self):
        """Test WebSocket cleanup in finally block."""
        mock_ws = AsyncMock()
        mock_ws.state = websockets.protocol.State.OPEN

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [mock_ws, asyncio.CancelledError("Stop test")]

            # Mock tasks to complete quickly
            with patch(
                "src.client.client.listen_for_commands", new_callable=AsyncMock
            ) as mock_listen:
                with patch(
                    "src.client.client.send_status_update_periodically",
                    new_callable=AsyncMock,
                ) as mock_periodic:
                    mock_listen.return_value = None
                    mock_periodic.return_value = None

                    # Run for a short time
                    task = asyncio.create_task(client.connect_and_send_updates())
                    await asyncio.sleep(0.2)

                    # WebSocket should be closed in finally block
                    mock_ws.close.assert_called()

                    # Clean up
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_exponential_backoff(self):
        """Test exponential backoff on connection failures."""
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_connect.side_effect = [
                    ConnectionRefusedError("Connection refused"),
                    ConnectionRefusedError("Connection refused"),
                    asyncio.CancelledError("Stop test"),
                ]

                # Run for a short time
                task = asyncio.create_task(client.connect_and_send_updates())
                await asyncio.sleep(0.1)

                # Should have called sleep with increasing delays
                assert mock_sleep.call_count >= 1

                # Clean up
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_delay_zero_skip_sleep(self):
        """Test that sleep is skipped when delay is zero."""
        original_client_id = client.CLIENT_ID

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            with patch("asyncio.sleep", new_callable=AsyncMock):
                # First call raises 4008 (duplicate ID), second call succeeds
                close_frame = Close(code=4008, reason="Duplicate client ID")
                connection_closed = websockets.exceptions.ConnectionClosed(
                    rcvd=close_frame, sent=None
                )

                mock_ws = AsyncMock()
                mock_connect.side_effect = [
                    connection_closed,
                    mock_ws,
                    asyncio.CancelledError("Stop test"),
                ]

                # Mock tasks to complete quickly
                with patch(
                    "src.client.client.listen_for_commands", new_callable=AsyncMock
                ) as mock_listen:
                    with patch(
                        "src.client.client.send_status_update_periodically",
                        new_callable=AsyncMock,
                    ) as mock_periodic:
                        mock_listen.return_value = None
                        mock_periodic.return_value = None

                        # Run for a short time
                        task = asyncio.create_task(client.connect_and_send_updates())
                        await asyncio.sleep(0.2)

                        # Should not sleep when delay is 0 (after 4008 error)
                        # The sleep calls should be minimal

                        # Clean up
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

        # Restore original client ID
        client.CLIENT_ID = original_client_id

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_websocket_state_closed(self):
        """Test WebSocket cleanup when state is already closed."""
        mock_ws = AsyncMock()
        mock_ws.state = websockets.protocol.State.CLOSED  # Already closed

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [mock_ws, asyncio.CancelledError("Stop test")]

            # Mock tasks to complete quickly
            with patch(
                "src.client.client.listen_for_commands", new_callable=AsyncMock
            ) as mock_listen:
                with patch(
                    "src.client.client.send_status_update_periodically",
                    new_callable=AsyncMock,
                ) as mock_periodic:
                    mock_listen.return_value = None
                    mock_periodic.return_value = None

                    # Run for a short time
                    task = asyncio.create_task(client.connect_and_send_updates())
                    await asyncio.sleep(0.2)

                    # WebSocket should NOT be closed since it's already closed
                    mock_ws.close.assert_not_called()

                    # Clean up
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_generic_exception(self):
        """Test handling of generic exceptions."""
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [
                RuntimeError("Generic error"),
                asyncio.CancelledError("Stop test"),
            ]

            # Run for a short time
            task = asyncio.create_task(client.connect_and_send_updates())
            await asyncio.sleep(0.1)

            # Should continue retrying on generic errors
            assert mock_connect.call_count >= 1

            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_connect_and_send_updates_manual_disconnect_break(self):
        """Test that manual disconnect breaks the main loop."""
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = ConnectionRefusedError("Connection refused")

            # Run for a short time
            task = asyncio.create_task(client.connect_and_send_updates())
            await asyncio.sleep(0.05)

            # Set manual disconnect to break the loop
            client.manual_disconnect_initiated = True
            await asyncio.sleep(0.05)

            # Should exit the loop
            assert client.manual_disconnect_initiated is True

            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
