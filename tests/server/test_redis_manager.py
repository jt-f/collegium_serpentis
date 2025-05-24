"""Tests for Redis manager functionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis

from src.server.redis_manager import (
    client_cache,
    perform_cleanup_cycle,
    status_store,
)
from tests.server.conftest import AsyncIteratorWrapper


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
        monkeypatch.setitem(status_store, "redis", "unavailable")
        client_cache["test_client"] = {"connected": "true", "status": "cached"}

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert data_source == "memory_cache"
        assert error_msg is None
        assert statuses == {"test_client": {"connected": "true", "status": "cached"}}
        mock_redis.scan_iter.assert_not_called()

    async def test_get_all_statuses_redis_error(self, mock_redis, monkeypatch):
        """Test fetching statuses when Redis encounters an error."""
        monkeypatch.setitem(status_store, "redis", "connected")

        from tests.server.conftest import ErringAsyncIterator

        mock_redis.scan_iter.return_value = ErringAsyncIterator(
            redis.RedisError("Test Redis error")
        )
        client_cache["test_client"] = {"connected": "true", "status": "cached"}

        # Import here to ensure we get the patched version
        from src.server.redis_manager import get_all_client_statuses

        statuses, data_source, error_msg = await get_all_client_statuses()

        assert data_source == "memory_cache"
        assert error_msg is not None
        assert "Test Redis error" in error_msg
        assert statuses == {"test_client": {"connected": "true", "status": "cached"}}


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
def mock_redis() -> AsyncMock:
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
