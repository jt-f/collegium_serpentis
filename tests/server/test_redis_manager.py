"""Tests for Redis manager functionality."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis

from src.server.redis_manager import (
    client_cache,
    perform_cleanup_cycle,
    redis_health_check,
    redis_reconnector,
    status_store,
)
from tests.server.conftest import AsyncIteratorWrapper


@pytest.mark.asyncio
class TestRedisHealthCheck:
    """Test the redis_health_check function."""

    async def test_redis_health_check_connection_lost(self, mock_redis, monkeypatch):
        """Test that redis_health_check updates status when connection is lost."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.ping.side_effect = redis.ConnectionError("Simulated connection lost")

        # Run the health check once (it's an infinite loop, so we mock ping once)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [
                None,
                asyncio.CancelledError,
            ]  # Allow one check, then cancel
            with pytest.raises(asyncio.CancelledError):
                await perform_health_check_cycle(mock_redis)  # Call the internal logic

        assert status_store["redis"] == "unavailable"
        mock_redis.ping.assert_awaited_once()

    async def test_redis_health_check_timeout(self, mock_redis, monkeypatch):
        """Test that redis_health_check updates status on timeout."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.ping.side_effect = TimeoutError("Simulated timeout")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            with pytest.raises(asyncio.CancelledError):
                await perform_health_check_cycle(mock_redis)

        assert status_store["redis"] == "unavailable"
        mock_redis.ping.assert_awaited_once()

    async def test_redis_health_check_general_exception(self, mock_redis, monkeypatch):
        """Test that redis_health_check updates status on general exception."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.ping.side_effect = Exception("Simulated general error")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            with pytest.raises(asyncio.CancelledError):
                await perform_health_check_cycle(mock_redis)

        assert status_store["redis"] == "unavailable"
        mock_redis.ping.assert_awaited_once()

    async def test_redis_health_check_already_unavailable(
        self, mock_redis, monkeypatch
    ):
        """Test that health check does nothing if Redis is already unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            with pytest.raises(asyncio.CancelledError):
                await perform_health_check_cycle(mock_redis)

        mock_redis.ping.assert_not_awaited()  # Should not attempt to ping
        assert status_store["redis"] == "unavailable"


# Helper function to allow testing redis_health_check's internal logic without infinite loop
async def perform_health_check_cycle(mock_redis):
    """Performs one cycle of the redis health check logic for testing."""
    # Temporarily replace the global redis_client with the mock
    with patch("src.server.redis_manager.redis_client", mock_redis):
        # Temporarily replace the global status_store with a mutable mock for testing
        with patch("src.server.redis_manager.status_store", status_store):
            # Call the function directly, it will hit the mocked ping
            await redis_health_check()


@pytest.mark.asyncio
class TestRedisStartup:
    """Test Redis initialization and startup behavior."""

    async def test_startup_event_redis_available(self, monkeypatch):
        """Test Redis initialization when Redis is available."""

        # Mock the redis_client in the module
        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock()

        monkeypatch.setattr(
            "src.server.redis_manager.redis_client", mock_redis_client_instance
        )

        # Mock asyncio.create_task to track what tasks are created
        # asyncio.create_task
        created_tasks = []

        def collect_tasks_side_effect(coro):
            created_tasks.append(coro)
            # Return a mock task instead of actually creating the task
            mock_task = AsyncMock()
            return mock_task

        monkeypatch.setattr("asyncio.create_task", collect_tasks_side_effect)

        # Import here to ensure we get the patched version
        from src.server.redis_manager import initialize_redis

        await initialize_redis()

        assert status_store["redis"] == "connected"
        mock_redis_client_instance.ping.assert_awaited_once()
        assert (
            len(created_tasks) == 0
        )  # redis_health_check, redis_reconnector, cleanup_disconnected_clients

    async def test_startup_event_redis_unavailable(self, monkeypatch):
        """Test Redis initialization when Redis is unavailable."""

        # Mock the redis_client in the module
        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )

        monkeypatch.setattr(
            "src.server.redis_manager.redis_client", mock_redis_client_instance
        )

        # Mock asyncio.create_task to track what tasks are created
        # original_create_task = asyncio.create_task
        created_tasks = []

        def collect_tasks_side_effect(coro):
            created_tasks.append(coro)
            # Return a mock task instead of actually creating the task
            mock_task = AsyncMock()
            return mock_task

        monkeypatch.setattr("asyncio.create_task", collect_tasks_side_effect)

        # Import here to ensure we get the patched version
        from src.server.redis_manager import initialize_redis

        await initialize_redis()

        assert status_store["redis"] == "unavailable"
        mock_redis_client_instance.ping.assert_awaited_once()
        assert len(created_tasks) == 0


@pytest.mark.asyncio
class TestRedisReconnector:
    """Test the redis_reconnector function."""

    async def test_reconnector_redis_unavailable(self, mock_redis, monkeypatch):
        """Test reconnector when Redis remains unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        mock_redis.ping.side_effect = redis.ConnectionError("Still down")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [
                None,
                asyncio.CancelledError,
            ]  # Allow one check, then cancel
            with pytest.raises(asyncio.CancelledError):
                await perform_reconnector_cycle(mock_redis)

        assert status_store["redis"] == "unavailable"
        mock_redis.ping.assert_awaited_once()

    async def test_reconnector_redis_reconnects(self, mock_redis, monkeypatch):
        """Test reconnector when Redis successfully reconnects."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        mock_redis.ping.return_value = True  # Simulate successful ping

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            with pytest.raises(asyncio.CancelledError):
                await perform_reconnector_cycle(mock_redis)

        assert status_store["redis"] == "connected"
        mock_redis.ping.assert_awaited_once()

    async def test_reconnector_redis_already_connected(self, mock_redis, monkeypatch):
        """Test reconnector does nothing if Redis is already connected."""
        monkeypatch.setitem(status_store, "redis", "connected")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            with pytest.raises(asyncio.CancelledError):
                await perform_reconnector_cycle(mock_redis)

        mock_redis.ping.assert_not_awaited()  # Should not attempt to ping
        assert status_store["redis"] == "connected"

    async def test_get_all_client_statuses_no_clients(self, mock_redis, monkeypatch):
        """Test getting all client statuses when no clients are present."""
        # Clear any existing cache
        client_cache.clear()
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper([])

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()
        assert statuses == {}
        assert data_source == "redis"
        assert error_msg is None
        mock_redis.scan_iter.assert_called_once_with("client:*:status")

    async def test_get_all_client_statuses_redis_unavailable(
        self, mock_redis, monkeypatch
    ):
        """Test getting all client statuses when Redis is unavailable."""
        # Clear any existing cache and set up test data
        client_cache.clear()
        test_data = {"client1": {"status": "cached"}, "client2": {"status": "cached"}}
        client_cache.update(test_data)

        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert statuses == test_data
        assert data_source == "memory_cache"
        assert error_msg is None
        mock_redis.scan_iter.assert_not_called()


# Helper function to allow testing redis_reconnector's internal logic without infinite loop
async def perform_reconnector_cycle(mock_redis):
    """Performs one cycle of the redis reconnector logic for testing."""
    # Temporarily replace the global redis_client with the mock
    with patch("src.server.redis_manager.redis_client", mock_redis):
        # Temporarily replace the global status_store with a mutable mock for testing
        with patch("src.server.redis_manager.status_store", status_store):
            # Call the function directly, it will hit the mocked ping
            await redis_reconnector()


@pytest.mark.asyncio
class TestGetAllClientStatuses:
    """Test the get_all_client_statuses function."""

    async def test_get_all_statuses_redis_connected(self, mock_redis, monkeypatch):
        """Test fetching statuses when Redis is connected."""
        monkeypatch.setitem(status_store, "redis", "connected")
        # Configure scan_iter to return an async iterator properly
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [b"client:client1:status", b"client:client2:status"]
        )
        mock_redis.hgetall.side_effect = [
            {b"connected": b"true", b"status": b"active"},
            {b"connected": b"false", b"status": b"inactive"},
        ]

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert data_source == "redis"
        assert error_msg is None
        assert len(statuses) == 2
        assert statuses["client1"]["connected"] == "true"
        assert statuses["client2"]["connected"] == "false"
        mock_redis.scan_iter.assert_called_once_with("client:*:status")
        assert mock_redis.hgetall.call_count == 2

    async def test_get_all_statuses_redis_unavailable(self, mock_redis, monkeypatch):
        """Test fetching statuses when Redis is unavailable."""
        # Clear any existing cache and set up test data
        client_cache.clear()
        test_data = {"test_client": {"connected": "true", "status": "cached"}}
        client_cache.update(test_data)

        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert data_source == "memory_cache"
        assert error_msg is None
        assert statuses == test_data
        mock_redis.scan_iter.assert_not_called()

    async def test_get_all_statuses_redis_error(self, mock_redis, monkeypatch):
        """Test fetching statuses when Redis encounters an error."""
        # Clear any existing cache and set up test data
        client_cache.clear()
        test_data = {"test_client": {"connected": "true", "status": "cached"}}
        client_cache.update(test_data)

        monkeypatch.setitem(status_store, "redis", "connected")

        from tests.server.conftest import ErringAsyncIterator

        mock_redis.scan_iter.return_value = ErringAsyncIterator(
            redis.RedisError("Test Redis error")
        )

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert data_source == "memory_cache"
        assert error_msg is not None
        assert "Test Redis error" in error_msg
        assert statuses == test_data


@pytest.mark.asyncio
class TestCleanupTask:
    """Test the cleanup task functionality."""

    CLIENT_ID_OLD = "client-old-disconnected"
    CLIENT_ID_RECENT = "client-recent-disconnected"
    CLIENT_ID_CONNECTED = "client-still-connected"
    CLIENT_ID_CACHE_OLD = "client-cache-old"

    @pytest.fixture(autouse=True)
    def setup_cleanup_test(self, monkeypatch):
        """Setup for cleanup tests."""
        # Clear cache before each test
        client_cache.clear()
        # Reset status store
        monkeypatch.setitem(status_store, "redis", "unknown")

    async def test_cleanup_deletes_old_disconnected_client_redis_and_cache(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup deletes old disconnected clients from Redis and cache."""
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=70)).isoformat()

        # Client in Redis and Cache, old disconnect
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_OLD}:status".encode()]
        )
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": old_disconnect_time.encode(),
        }
        mock_redis.delete = AsyncMock()  # Ensure delete is awaitable

        client_cache[self.CLIENT_ID_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }

        await perform_cleanup_cycle()

        mock_redis.delete.assert_awaited_with(f"client:{self.CLIENT_ID_OLD}:status")
        assert self.CLIENT_ID_OLD not in client_cache

    async def test_cleanup_keeps_recent_disconnected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup keeps recently disconnected clients."""
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        recent_disconnect_time = (now - timedelta(seconds=10)).isoformat()

        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_RECENT}:status".encode()]
        )
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": recent_disconnect_time.encode(),
        }
        mock_redis.delete = (
            AsyncMock()
        )  # Ensure delete is awaitable (though not called here)

        client_cache[self.CLIENT_ID_RECENT] = {
            "connected": "false",
            "disconnect_time": recent_disconnect_time,
        }

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()
        assert self.CLIENT_ID_RECENT in client_cache  # Should remain in cache too

    async def test_cleanup_keeps_connected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup keeps connected clients."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_CONNECTED}:status".encode()]
        )
        mock_redis.hgetall.return_value = {b"connected": b"true"}
        mock_redis.delete = (
            AsyncMock()
        )  # Ensure delete is awaitable (though not called here)

        client_cache[self.CLIENT_ID_CONNECTED] = {"connected": "true"}

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()
        assert self.CLIENT_ID_CONNECTED in client_cache

    async def test_cleanup_deletes_old_disconnected_client_from_cache_redis_unavailable(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup deletes old clients from cache when Redis unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=70)).isoformat()

        client_cache[self.CLIENT_ID_CACHE_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper([])

        await perform_cleanup_cycle()

        mock_redis.delete.assert_not_awaited()  # Redis delete should not be called
        assert self.CLIENT_ID_CACHE_OLD not in client_cache

    async def test_cleanup_handles_redis_error_gracefully(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup handles Redis errors gracefully."""
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=70)).isoformat()

        mock_redis.scan_iter.side_effect = redis.RedisError("Scan failed")

        # Client in cache that should be cleaned up
        client_cache[self.CLIENT_ID_CACHE_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }

        await perform_cleanup_cycle()

        assert status_store["redis"] == "unavailable"  # Status should be updated
        mock_redis.delete.assert_not_awaited()  # No Redis delete if scan failed
        assert (
            self.CLIENT_ID_CACHE_OLD not in client_cache
        )  # Cache cleanup should still run

    async def test_cleanup_redis_unavailable_no_redis_calls(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup when Redis is unavailable, ensuring no Redis calls are made."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=70)).isoformat()

        # Add an old disconnected client to the cache only
        client_cache[self.CLIENT_ID_CACHE_OLD] = {
            "connected": "false",
            "disconnect_time": old_disconnect_time,
        }

        # Mock the delete method to ensure it's not called
        mock_redis.delete = AsyncMock()

        await perform_cleanup_cycle()

        # Verify no Redis operations were attempted
        mock_redis.scan_iter.assert_not_called()
        mock_redis.hgetall.assert_not_called()
        mock_redis.delete.assert_not_called()

        # The client should be removed from the cache
        assert (
            self.CLIENT_ID_CACHE_OLD not in client_cache
        )  # Cache cleanup should still happen


@pytest.mark.asyncio
class TestUpdateClientStatus:
    """Test the update_client_status function."""

    async def test_update_client_status_redis_connected(self, mock_redis, monkeypatch):
        """Test updating client status when Redis is connected."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock()

        # Import here to ensure we get the patched version
        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {"status": "running"})

        assert result is True
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == "client:test_client:status"
        assert kwargs["mapping"]["status"] == "running"
        assert client_cache["test_client"]["status"] == "running"

    async def test_update_client_status_redis_unavailable(
        self, mock_redis, monkeypatch
    ):
        """Test updating client status when Redis is unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Import here to ensure we get the patched version
        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {"status": "running"})

        assert result is True
        mock_redis.hset.assert_not_awaited()
        assert client_cache["test_client"]["status"] == "running"

    async def test_update_client_status_empty_attributes(self, mock_redis, monkeypatch):
        """Test updating client status with empty attributes."""
        monkeypatch.setitem(status_store, "redis", "connected")

        # Import here to ensure we get the patched version
        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {})

        assert result is True
        mock_redis.hset.assert_not_awaited()


@pytest.mark.asyncio
class TestSyncCacheToRedis:
    """Test the sync_cache_to_redis function."""

    async def test_sync_cache_to_redis_empty_cache(self, mock_redis, monkeypatch):
        """Test that sync_cache_to_redis does nothing when cache is empty."""
        monkeypatch.setitem(status_store, "redis", "connected")
        client_cache.clear()

        # Import here to ensure we get the patched version
        from src.server.redis_manager import sync_cache_to_redis

        await sync_cache_to_redis()

        mock_redis.hset.assert_not_awaited()

    async def test_sync_cache_to_redis_redis_unavailable(self, mock_redis, monkeypatch):
        """Test that sync_cache_to_redis does nothing when Redis is unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        client_cache["test_client"] = {"status": "cached"}

        # Import here to ensure we get the patched version
        from src.server.redis_manager import sync_cache_to_redis

        await sync_cache_to_redis()

        mock_redis.hset.assert_not_awaited()

    async def test_sync_cache_to_redis_success(self, mock_redis, monkeypatch):
        """Test successful synchronization of cache to Redis."""
        # Clear any existing cache and set up test data
        client_cache.clear()
        test_data = {"client1": {"status": "active"}, "client2": {"status": "inactive"}}
        client_cache.update(test_data)

        monkeypatch.setitem(status_store, "redis", "connected")

        # Configure mock to return a successful response
        mock_redis.hset = AsyncMock(return_value=1)

        # Import here to ensure we get the patched version
        from src.server.redis_manager import sync_cache_to_redis

        # Reset call count in case redis_reconnector has already called sync_cache_to_redis
        mock_redis.hset.reset_mock()

        await sync_cache_to_redis()

        # Verify the hset calls were made correctly
        assert mock_redis.hset.call_count == 2
        mock_redis.hset.assert_any_call(
            "client:client1:status", mapping={"status": "active"}
        )
        mock_redis.hset.assert_any_call(
            "client:client2:status", mapping={"status": "inactive"}
        )
        assert not client_cache  # Cache should be cleared after sync

    async def test_sync_cache_to_redis_error(self, mock_redis, monkeypatch):
        """Test that sync_cache_to_redis handles Redis errors gracefully."""
        monkeypatch.setitem(status_store, "redis", "connected")
        client_cache["test_client"] = {"status": "cached"}
        mock_redis.hset.side_effect = redis.RedisError("Sync failed")

        # Import here to ensure we get the patched version
        from src.server.redis_manager import sync_cache_to_redis

        await sync_cache_to_redis()

        assert client_cache  # Cache should not be cleared on error


@pytest.mark.asyncio
class TestGetClientInfo:
    """Test the get_client_info function."""

    async def test_get_client_info_redis_connected(self, mock_redis, monkeypatch):
        """Test getting client info when Redis is connected."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {
            b"connected": b"true",
            b"status": b"running",
        }

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_client_info

        result = await get_client_info("test_client")

        assert result["connected"] == "true"
        assert result["status"] == "running"
        mock_redis.hgetall.assert_awaited_once_with("client:test_client:status")

    async def test_get_client_info_redis_unavailable(self, mock_redis, monkeypatch):
        """Test getting client info when Redis is unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        client_cache["test_client"] = {"connected": "true", "status": "cached"}

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_client_info

        result = await get_client_info("test_client")

        assert result == {"connected": "true", "status": "cached"}
        mock_redis.hgetall.assert_not_awaited()

    async def test_get_client_info_not_found(self, mock_redis, monkeypatch):
        """Test getting client info when client is not found."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {}

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_client_info

        result = await get_client_info("nonexistent_client")

        assert result is None
        mock_redis.hgetall.assert_awaited_once_with("client:nonexistent_client:status")


@pytest.fixture
def mock_redis() -> Iterator[AsyncMock]:
    """Create a mock Redis client specifically for redis_manager tests."""
    with patch("src.server.redis_manager.redis_client", new_callable=AsyncMock) as mock:
        # Configure hset to return a value (typically the number of fields updated)
        mock.hset = AsyncMock(return_value=1)
        # Configure scan_iter to return an async iterator directly (not awaitable)
        mock.scan_iter = MagicMock()  # Not AsyncMock, just MagicMock
        mock.hgetall = AsyncMock()
        mock.delete = AsyncMock()
        # Configure ping to succeed by default
        mock.ping = AsyncMock(return_value=True)
        yield mock
