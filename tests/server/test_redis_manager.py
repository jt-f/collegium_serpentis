"""Tests for Redis manager functionality."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis

# Import status_store and perform_cleanup_cycle directly as client_cache is removed
from src.server.redis_manager import (
    perform_cleanup_cycle,
    redis_health_check,
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
        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock()
        monkeypatch.setattr(
            "src.server.redis_manager.redis_client", mock_redis_client_instance
        )
        from src.server.redis_manager import initialize_redis

        await initialize_redis()

        assert status_store["redis"] == "connected"
        mock_redis_client_instance.ping.assert_awaited_once()

    async def test_startup_event_redis_unavailable(self, monkeypatch):
        """Test Redis initialization when Redis is unavailable."""
        mock_redis_client_instance = AsyncMock()
        mock_redis_client_instance.ping = AsyncMock(
            side_effect=redis.ConnectionError("Connection refused")
        )
        monkeypatch.setattr(
            "src.server.redis_manager.redis_client", mock_redis_client_instance
        )
        from src.server.redis_manager import initialize_redis

        await initialize_redis()

        assert status_store["redis"] == "unavailable"
        mock_redis_client_instance.ping.assert_awaited_once()


@pytest.mark.asyncio
class TestCleanupTask:
    """Test the cleanup task functionality."""

    CLIENT_ID_OLD = "client-old-disconnected"
    CLIENT_ID_RECENT = "client-recent-disconnected"
    CLIENT_ID_CONNECTED = "client-still-connected"

    @pytest.fixture(autouse=True)
    def setup_cleanup_test(self, monkeypatch):
        """Setup for cleanup tests."""
        # client_cache.clear() # Removed client_cache
        monkeypatch.setitem(status_store, "redis", "unknown")

    async def test_cleanup_deletes_old_disconnected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup deletes old disconnected clients from Redis."""
        monkeypatch.setitem(status_store, "redis", "connected")
        now = datetime.now(UTC)
        old_disconnect_time = (now - timedelta(seconds=70)).isoformat()

        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_OLD}:status".encode()]
        )
        mock_redis.hgetall.return_value = {
            b"connected": b"false",
            b"disconnect_time": old_disconnect_time.encode(),
        }
        mock_redis.delete = AsyncMock()

        await perform_cleanup_cycle()

        mock_redis.delete.assert_awaited_with(f"client:{self.CLIENT_ID_OLD}:status")

    async def test_cleanup_keeps_recent_disconnected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup keeps recently disconnected clients in Redis."""
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
        mock_redis.delete = AsyncMock()
        await perform_cleanup_cycle()
        mock_redis.delete.assert_not_awaited()

    async def test_cleanup_keeps_connected_client_redis(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup keeps connected clients in Redis."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            [f"client:{self.CLIENT_ID_CONNECTED}:status".encode()]
        )
        mock_redis.hgetall.return_value = {b"connected": b"true"}
        mock_redis.delete = AsyncMock()
        await perform_cleanup_cycle()
        mock_redis.delete.assert_not_awaited()

    async def test_cleanup_skips_if_redis_unavailable(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup skips if Redis is unavailable."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        # No need to set up client_cache data anymore
        mock_redis.scan_iter.return_value = AsyncIteratorWrapper(
            []
        )  # Should not be called

        await perform_cleanup_cycle()

        mock_redis.scan_iter.assert_not_called()
        mock_redis.delete.assert_not_awaited()

    async def test_cleanup_handles_redis_scan_error_gracefully(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup handles Redis errors gracefully during scan."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.scan_iter.side_effect = redis.RedisError("Scan failed")

        await perform_cleanup_cycle()

        assert status_store["redis"] == "unavailable"  # Status should be updated
        mock_redis.delete.assert_not_awaited()  # No Redis delete if scan failed

    async def test_cleanup_redis_unavailable_no_redis_calls(
        self, mock_redis: AsyncMock, monkeypatch
    ):
        """Test cleanup when Redis is unavailable, ensuring no Redis calls are made."""
        monkeypatch.setitem(status_store, "redis", "unavailable")

        # Mock the delete method to ensure it's not called
        mock_redis.delete = AsyncMock()

        await perform_cleanup_cycle()

        # Verify no Redis operations were attempted
        mock_redis.scan_iter.assert_not_called()
        mock_redis.hgetall.assert_not_called()
        mock_redis.delete.assert_not_called()


@pytest.mark.asyncio
class TestUpdateClientStatus:
    """Test the update_client_status function."""

    async def test_update_client_status_redis_connected(self, mock_redis, monkeypatch):
        """Test updating client status when Redis is connected."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock(return_value=1)  # Ensure hset is an AsyncMock

        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {"status": "running"})

        assert result is True
        mock_redis.hset.assert_awaited_once()
        args, kwargs = mock_redis.hset.call_args
        assert args[0] == "client:test_client:status"
        assert kwargs["mapping"]["status"] == "running"

    async def test_update_client_status_redis_unavailable(
        self, mock_redis, monkeypatch
    ):
        """Test updating client status when Redis is unavailable returns False."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {"status": "running"})

        assert result is False  # Expect False as Redis is unavailable
        mock_redis.hset.assert_not_awaited()

    async def test_update_client_status_redis_hset_fails(self, mock_redis, monkeypatch):
        """Test updating client status when Redis hset fails."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hset = AsyncMock(side_effect=redis.ConnectionError("HSET failed"))
        from src.server.redis_manager import update_client_status

        result = await update_client_status("test_client", {"status": "failed_update"})

        assert result is False
        mock_redis.hset.assert_awaited_once()
        assert status_store["redis"] == "unavailable"  # Ensure Redis marked unavailable

    async def test_update_client_status_empty_attributes(self, mock_redis, monkeypatch):
        """Test updating client status with empty attributes returns True."""
        monkeypatch.setitem(status_store, "redis", "connected")
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
        from src.server.redis_manager import get_client_info

        result = await get_client_info("test_client")

        assert result is not None
        assert result["connected"] == "true"
        assert result["status"] == "running"
        mock_redis.hgetall.assert_awaited_once_with("client:test_client:status")

    async def test_get_client_info_redis_unavailable(self, mock_redis, monkeypatch):
        """Test getting client info when Redis is unavailable returns None."""
        monkeypatch.setitem(status_store, "redis", "unavailable")
        from src.server.redis_manager import get_client_info

        result = await get_client_info("test_client")

        assert result is None
        mock_redis.hgetall.assert_not_awaited()

    async def test_get_client_info_not_found_in_redis(self, mock_redis, monkeypatch):
        """Test getting client info when client is not found in Redis returns None."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.return_value = {}  # Empty dict means not found
        from src.server.redis_manager import get_client_info

        result = await get_client_info("nonexistent_client")

        assert result is None
        mock_redis.hgetall.assert_awaited_once_with("client:nonexistent_client:status")

    async def test_get_client_info_redis_hgetall_fails(self, mock_redis, monkeypatch):
        """Test getting client info when Redis hgetall fails."""
        monkeypatch.setitem(status_store, "redis", "connected")
        mock_redis.hgetall.side_effect = redis.ConnectionError("HGETALL failed")
        from src.server.redis_manager import get_client_info

        result = await get_client_info("test_client")

        assert result is None
        mock_redis.hgetall.assert_awaited_once()
        assert status_store["redis"] == "unavailable"  # Ensure Redis marked unavailable


@pytest.fixture
def mock_redis() -> Iterator[AsyncMock]:
    """Create a mock Redis client specifically for redis_manager tests."""
    with patch("src.server.redis_manager.redis_client", new_callable=AsyncMock) as mock:
        mock.hset = AsyncMock(return_value=1)
        mock.scan_iter = MagicMock()
        mock.hgetall = AsyncMock()
        mock.delete = AsyncMock()
        mock.ping = AsyncMock(return_value=True)
        yield mock
