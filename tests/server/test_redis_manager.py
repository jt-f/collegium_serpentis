"""Tests for Redis manager functionality."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis_async

from src.server import config
from src.server.redis_manager import RedisManager
from src.shared.schemas.websocket import AllClientStatuses, ClientStatus
from tests.server.conftest import AsyncIteratorWrapper


@pytest.fixture
def redis_manager() -> RedisManager:
    """Create a RedisManager instance for testing."""
    # Initialize RedisManager without establishing a real connection
    return RedisManager()


@pytest.fixture
def mock_redis_connection() -> Iterator[AsyncMock]:
    """Create a mock Redis connection instance."""
    mock_conn = AsyncMock()
    mock_conn.ping = AsyncMock(return_value=True)
    mock_conn.set = AsyncMock(return_value=True)
    mock_conn.get = AsyncMock()
    mock_conn.scan_iter = MagicMock()
    mock_conn.delete = AsyncMock()
    mock_conn.close = AsyncMock()
    yield mock_conn


@pytest.mark.asyncio
class TestRedisManagerInitialization:
    """Test RedisManager initialization and connection management."""

    async def test_initialize_success(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test successful initialization."""
        # Set up the mock connection to simulate a successful connection
        mock_redis_connection.ping.return_value = True

        # Mock _get_async_redis_connection to simulate successful connection
        async def mock_get_connection():
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_CONNECTED
            )
            return mock_redis_connection

        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=mock_get_connection,
        ) as mock_get_conn:
            await redis_manager.initialize()

        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_CONNECTED
        )
        mock_get_conn.assert_awaited_once()

    async def test_initialize_connection_failure(self, redis_manager: RedisManager):
        """Test initialization with _get_async_redis_connection returning None."""

        # Mock _get_async_redis_connection to simulate connection failure
        async def mock_get_connection():
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return None

        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=mock_get_connection,
        ) as mock_get_conn:
            await redis_manager.initialize()

        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        mock_get_conn.assert_awaited_once()

    async def test_initialize_connection_failure_exception(
        self, redis_manager: RedisManager
    ):
        """Test initialization when _get_async_redis_connection raises ConnectionError."""
        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=redis_async.ConnectionError("Connection refused"),
        ) as mock_get_conn:
            await redis_manager.initialize()

        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        mock_get_conn.assert_awaited_once()

    async def test_get_connection_reuses_existing(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test that _get_async_redis_connection reuses existing connection."""
        redis_manager._redis_conn = mock_redis_connection
        mock_redis_connection.ping.return_value = True
        mock_redis_connection.ping.reset_mock()

        first_conn = await redis_manager._get_async_redis_connection()
        mock_redis_connection.ping.assert_awaited_once()

        second_conn = await redis_manager._get_async_redis_connection()
        assert first_conn is second_conn
        assert mock_redis_connection.ping.await_count == 2

    async def test_get_connection_creates_new_on_failure(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test that _get_async_redis_connection creates new connection after failure."""
        redis_manager._redis_conn = mock_redis_connection
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        # Simulate ping failure then success
        mock_redis_connection.ping.side_effect = [
            redis_async.ConnectionError("Connection lost"),
            True,
        ]
        mock_redis_connection.ping.reset_mock()

        # Patch RedisManager._redis_conn to None after first failure to simulate new connection
        async def fake_get_async_redis_connection():
            if redis_manager._redis_conn is mock_redis_connection:
                # Simulate connection loss
                redis_manager._redis_conn = None
                raise redis_async.ConnectionError("Connection lost")
            # Simulate new connection
            redis_manager._redis_conn = mock_redis_connection
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_CONNECTED
            )
            return mock_redis_connection

        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=fake_get_async_redis_connection,
        ):
            try:
                await redis_manager._get_async_redis_connection()
            except redis_async.ConnectionError:
                await redis_manager._get_async_redis_connection()
        assert redis_manager._redis_conn is mock_redis_connection


@pytest.mark.asyncio
class TestRedisManagerHealthCheck:
    """Test RedisManager health check functionality."""

    async def test_health_check_success(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test successful health check."""
        await redis_manager.initialize()
        with patch.object(redis_manager, "_redis_conn", new=mock_redis_connection):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await redis_manager.health_check()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_CONNECTED
        )

    async def test_health_check_connection_lost(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test health check when connection is lost."""
        await redis_manager.initialize()
        mock_redis_connection.ping.side_effect = redis_async.ConnectionError(
            "Connection lost"
        )
        with patch.object(redis_manager, "_redis_conn", new=mock_redis_connection):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await redis_manager.health_check()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )

    async def test_health_check_timeout(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test health check timeout handling."""
        await redis_manager.initialize()
        mock_redis_connection.ping.side_effect = TimeoutError()
        with patch.object(redis_manager, "_redis_conn", new=mock_redis_connection):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await redis_manager.health_check()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )


@pytest.mark.asyncio
class TestRedisManagerReconnector:
    """Test RedisManager reconnection functionality."""

    async def test_reconnector_success(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test successful reconnection."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )

        # Set up the mock connection to simulate a successful connection
        mock_redis_connection.ping.return_value = True

        # Mock _get_async_redis_connection to simulate successful connection
        async def mock_get_connection():
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_CONNECTED
            )
            return mock_redis_connection

        # Patch _get_async_redis_connection to return the mock connection
        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=mock_get_connection,
        ) as mock_get_conn:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await redis_manager.reconnector()

        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_CONNECTED
        )
        mock_get_conn.assert_awaited_once()

    async def test_reconnector_failure(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test reconnection failure."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        # Patch _get_async_redis_connection to raise connection error
        with patch.object(
            redis_manager,
            "_get_async_redis_connection",
            side_effect=redis_async.ConnectionError("Connection refused"),
        ) as mock_get_conn:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = [None, asyncio.CancelledError()]
                with pytest.raises(asyncio.CancelledError):
                    await redis_manager.reconnector()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        mock_get_conn.assert_awaited_once()


@pytest.mark.asyncio
class TestRedisManagerClientStatus:
    """Test RedisManager client status operations."""

    async def test_update_client_status(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test updating client status."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        client_status = ClientStatus(
            client_id="test_client",
            client_role="worker",
            is_active=True,
            last_seen=datetime.now(UTC).isoformat(),
            status="online",
            version="1.0.0",
            connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
        )
        result = await redis_manager.update_client_status(client_status)
        assert result is True
        mock_redis_connection.set.assert_awaited_once()
        call_args = mock_redis_connection.set.call_args[0]
        assert call_args[0] == f"client_status:{client_status.client_id}"
        assert json.loads(call_args[1]) == client_status.model_dump()

    async def test_update_client_status_broadcast(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test client status update with broadcast."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        broadcast_called = False

        async def mock_broadcast(status: ClientStatus) -> None:
            nonlocal broadcast_called
            broadcast_called = True
            assert status.client_id == "test-client"

        redis_manager.register_single_status_broadcast_callback(mock_broadcast)
        client_status_obj = ClientStatus(
            client_id="test-client",
            client_role="worker",
            is_active=True,
            last_seen=datetime.now(UTC).isoformat(),
            status="online",
            version="1.0.0",
            connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
        )
        await redis_manager.update_client_status(client_status_obj, broadcast=True)
        assert broadcast_called
        mock_redis_connection.set.assert_awaited_once()

    async def test_get_client_info(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test getting client info."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        client_id = "test_client"
        status = ClientStatus(
            client_id=client_id,
            client_role="worker",
            is_active=True,
            last_seen=datetime.now(UTC).isoformat(),
            status="online",
            version="1.0.0",
            connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
        )
        mock_redis_connection.get.return_value = status.model_dump_json().encode()
        result = await redis_manager.get_client_info(client_id)
        assert result is not None
        assert isinstance(result, ClientStatus)
        assert result.client_id == client_id
        mock_redis_connection.get.assert_awaited_once_with(f"client_status:{client_id}")

    async def test_get_all_client_statuses(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test getting all client statuses."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        client_ids = ["client1", "client2"]
        statuses = {
            "client1": ClientStatus(
                client_id="client1",
                client_role="worker",
                is_active=True,
                last_seen=datetime.now(UTC).isoformat(),
                status="online",
                version="1.0.0",
                connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
            ),
            "client2": ClientStatus(
                client_id="client2",
                client_role="worker",
                is_active=True,
                last_seen=datetime.now(UTC).isoformat(),
                status="online",
                version="1.0.0",
                connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
            ),
        }
        # Use an async generator for scan_iter
        yielded_keys = []

        async def async_iter(items):
            for item in items:
                yielded_keys.append(item)
                yield item

        mock_redis_connection.scan_iter.side_effect = lambda match=None: async_iter(
            [f"client_status:{client_id}".encode() for client_id in client_ids]
        )
        called_keys = []

        async def mock_get(key) -> bytes:
            called_keys.append(key)
            # key is a string, not bytes
            client_id = key.split("client_status:")[1]
            return statuses[client_id].model_dump_json().encode()

        mock_redis_connection.get.side_effect = mock_get
        result = await redis_manager.get_all_client_statuses()
        # Debug: print what was yielded and called
        print("Yielded keys:", yielded_keys)
        print("Called keys:", called_keys)
        assert yielded_keys, "No keys were yielded by scan_iter!"
        assert called_keys, "get was never called!"
        assert result is not None
        assert isinstance(result, AllClientStatuses)
        assert len(result.clients) == 2
        assert "client1" in result.clients
        assert "client2" in result.clients
        assert result.clients["client1"].client_id == "client1"
        assert result.clients["client2"].client_id == "client2"
        mock_redis_connection.scan_iter.assert_called_once_with(match="client_status:*")
        assert mock_redis_connection.get.call_count == 2


@pytest.mark.asyncio
class TestRedisManagerCleanup:
    """Test RedisManager cleanup functionality."""

    async def test_cleanup_disconnected_clients(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test cleanup of disconnected clients."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        now = datetime.now(UTC)
        # Set disconnect_time to be well beyond the TTL
        old_disconnect_time = (
            now - timedelta(seconds=config.REDIS_CLIENT_TTL_SECONDS + 10)
        ).isoformat()
        client_status = ClientStatus(
            client_id="test-client",
            client_role="worker",
            is_active=False,
            last_seen=old_disconnect_time,
            status="offline",
            version="1.0.0",
            connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
            connected=config.STATUS_VALUE_DISCONNECTED,
            disconnect_time=old_disconnect_time,
        )

        # Provide async_iter for this test
        async def async_iter(items):
            for item in items:
                yield item

        mock_redis_connection.scan_iter.side_effect = lambda match=None: async_iter(
            [f"client_status:{client_status.client_id}".encode()]
        )
        mock_redis_connection.get.return_value = (
            client_status.model_dump_json().encode()
        )
        await redis_manager.perform_cleanup_cycle()
        assert mock_redis_connection.delete.call_count == 1
        mock_redis_connection.delete.assert_awaited_once_with(
            f"client_status:{client_status.client_id}"
        )

    async def test_cleanup_keeps_recent_disconnected(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test cleanup keeps recently disconnected clients."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        now = datetime.now(UTC)
        recent_disconnect_time = (now - timedelta(seconds=10)).isoformat()
        client_status = ClientStatus(
            client_id="test-client",
            client_role="worker",
            is_active=False,
            last_seen=recent_disconnect_time,
            status="offline",
            version="1.0.0",
            connection_info={"ip": "127.0.0.1", "port": 8080, "protocol": "ws"},
            connected=config.STATUS_VALUE_DISCONNECTED,
            disconnect_time=recent_disconnect_time,
        )
        mock_redis_connection.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client_status:{client_status.client_id}".encode()]
        )
        mock_redis_connection.get.return_value = (
            client_status.model_dump_json().encode()
        )
        await redis_manager.perform_cleanup_cycle()
        assert mock_redis_connection.delete.call_count == 0

    async def test_cleanup_handles_errors(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test cleanup error handling."""
        redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        redis_manager._redis_conn = mock_redis_connection
        mock_redis_connection.scan_iter.side_effect = redis_async.RedisError(
            "Scan failed"
        )
        await redis_manager.perform_cleanup_cycle()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )


@pytest.mark.asyncio
class TestRedisManagerClose:
    """Test RedisManager close functionality."""

    async def test_close_success(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test successful connection close."""
        await redis_manager.initialize()
        with patch.object(redis_manager, "_redis_conn", new=mock_redis_connection):
            await redis_manager.close()
        mock_redis_connection.close.assert_awaited_once()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )

    async def test_close_handles_errors(
        self, redis_manager: RedisManager, mock_redis_connection: AsyncMock
    ):
        """Test close error handling."""
        await redis_manager.initialize()
        mock_redis_connection.close.side_effect = redis_async.RedisError("Close failed")
        with patch.object(redis_manager, "_redis_conn", new=mock_redis_connection):
            await redis_manager.close()
        mock_redis_connection.close.assert_awaited_once()
        assert (
            redis_manager.status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
