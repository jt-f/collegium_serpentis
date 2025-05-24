"""Redis client management and related operations."""

import asyncio
import random
from datetime import UTC, datetime
from typing import Any

import redis

from src.shared.utils.config import REDIS_CONFIG
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Initialize Redis client
redis_client = redis.asyncio.Redis(**REDIS_CONFIG)

# Status store to track service health
status_store: dict[str, str] = {"redis": "unknown"}

# In-memory cache as fallback when Redis is unavailable
client_cache: dict[str, dict[str, Any]] = {}


async def redis_health_check() -> None:
    """
    Background task that frequently checks if Redis is available.
    This ensures the server notices immediately when Redis goes down.
    """
    check_interval = 3  # Check every 3 seconds

    while True:
        await asyncio.sleep(check_interval)

        if status_store["redis"] != "connected":
            continue

        try:
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
        except (TimeoutError, redis.ConnectionError) as e:
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis connection lost",
                error=str(e),
                previous_status=old_status,
                timestamp=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            old_status = status_store["redis"]
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis health check failed",
                error=str(e),
                previous_status=old_status,
            )


async def redis_reconnector() -> None:
    """Background task that attempts to reconnect to Redis with exponential backoff."""
    base_delay = 5
    max_delay = 300
    current_delay = base_delay

    while True:
        await asyncio.sleep(current_delay)

        if status_store["redis"] == "connected":
            current_delay = base_delay
            continue

        try:
            logger.debug("Attempting to reconnect to Redis", delay=current_delay)
            await redis_client.ping()
            status_store["redis"] = "connected"
            logger.info("Reconnected to Redis")
            await sync_cache_to_redis()
            current_delay = base_delay
        except redis.ConnectionError as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis still unavailable",
                error=str(e),
                next_retry_in=current_delay * 2,
            )
            current_delay = min(current_delay * 2, max_delay)
            current_delay = current_delay * (0.8 + 0.4 * random.random())
        except Exception as e:
            status_store["redis"] = "unavailable"
            logger.error(
                "Redis still unavailable (UnknownError)",
                error=str(e),
                next_retry_in=current_delay,
            )
            current_delay = min(current_delay * 2, max_delay)


async def sync_cache_to_redis() -> None:
    """Synchronize in-memory cache to Redis when Redis becomes available."""
    if not client_cache or status_store["redis"] != "connected":
        return

    try:
        count = 0
        for client_id, status in client_cache.items():
            redis_key = f"client:{client_id}:status"
            await redis_client.hset(redis_key, mapping=status)
            count += 1
        if count > 0:
            logger.info(f"Synced {count} clients from cache to Redis")
        client_cache.clear()
    except Exception as e:
        logger.error("Failed to sync cache to Redis", error=str(e))


async def update_client_status(
    client_id: str, status_attributes: dict[str, Any]
) -> bool:
    """Update client status in Redis and/or local cache."""
    if not status_attributes:
        return True

    processed_attributes = {str(k): str(v) for k, v in status_attributes.items()}

    try:
        if status_store["redis"] == "connected":
            redis_key = f"client:{client_id}:status"
            try:
                await redis_client.hset(redis_key, mapping=processed_attributes)
                logger.debug(
                    "Updated client status in Redis",
                    client_id=client_id,
                    attributes=list(processed_attributes.keys()),
                )
                if client_id not in client_cache:
                    client_cache[client_id] = {}
                client_cache[client_id].update(processed_attributes)
                return True
            except (TimeoutError, redis.ConnectionError) as e:
                status_store["redis"] = "unavailable"
                logger.error(
                    "Redis connection failed during status update",
                    client_id=client_id,
                    error=str(e),
                )

        if client_id not in client_cache:
            client_cache[client_id] = {}
        client_cache[client_id].update(processed_attributes)
        logger.debug(
            "Stored client status in memory cache (Redis unavailable or failed)",
            client_id=client_id,
            attributes=list(processed_attributes.keys()),
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to update client status",
            client_id=client_id,
            error=str(e),
        )
        try:
            if client_id not in client_cache:
                client_cache[client_id] = {}
            client_cache[client_id].update(processed_attributes)
            logger.debug(
                "Stored client status in memory cache after general error",
                client_id=client_id,
            )
            return True
        except Exception as cache_err:
            logger.error(
                "Failed to store client status in memory cache (last resort)",
                client_id=client_id,
                error=str(cache_err),
            )
            return False


async def perform_cleanup_cycle():
    """Perform one cycle of client cleanup. This function can be tested directly."""
    max_disconnect_duration = 60  # 1 minute in seconds
    logger.debug("Running cleanup for disconnected clients...")
    current_time = datetime.now(UTC)
    clients_to_delete = []

    # Check Redis first if available
    if status_store["redis"] == "connected":
        try:
            async for key_b in redis_client.scan_iter("client:*:status"):
                key = key_b.decode()
                client_id = key.split(":")[1]
                status_data_raw = await redis_client.hgetall(key)

                # Decode status_data from bytes to str
                status_data = {
                    k.decode(): v.decode() for k, v in status_data_raw.items()
                }

                if status_data.get("connected") == "false":
                    disconnect_time_str = status_data.get("disconnect_time")
                    if disconnect_time_str:
                        try:
                            disconnect_time = datetime.fromisoformat(
                                disconnect_time_str.replace("Z", "+00:00")
                            )
                            if (
                                current_time - disconnect_time
                            ).total_seconds() > max_disconnect_duration:
                                clients_to_delete.append(client_id)
                        except ValueError as e:
                            logger.warning(
                                f"Invalid disconnect_time format for client "
                                f"{client_id}: {disconnect_time_str}, error: {e}"
                            )

            for client_id in clients_to_delete:
                logger.info(
                    f"Deleting data for disconnected client {client_id} from Redis."
                )
                await redis_client.delete(f"client:{client_id}:status")
                # Also remove from in-memory cache if it exists there, for consistency
                if client_id in client_cache:
                    del client_cache[client_id]
            if clients_to_delete:
                logger.debug(
                    f"Finished Redis cleanup, deleted {len(clients_to_delete)} "
                    f"clients."
                )

        except redis.RedisError as e:
            logger.error(f"Redis error during client cleanup: {e}")
            status_store["redis"] = "unavailable"  # Mark Redis as unavailable
        except Exception as e:
            logger.error(f"Unexpected error during Redis client cleanup: {e}")

    # Fallback or primary: Check in-memory cache
    cached_clients_to_delete = []
    for client_id, status_data in list(client_cache.items()):
        if client_id in clients_to_delete and status_store["redis"] == "connected":
            continue

        if status_data.get("connected") == "false":
            disconnect_time_str = status_data.get("disconnect_time")
            if disconnect_time_str:
                try:
                    disconnect_time = datetime.fromisoformat(
                        disconnect_time_str.replace("Z", "+00:00")
                    )
                    if (
                        current_time - disconnect_time
                    ).total_seconds() > max_disconnect_duration:
                        cached_clients_to_delete.append(client_id)
                except ValueError as e:
                    logger.warning(
                        f"Invalid disconnect_time format in cache for client "
                        f"{client_id}: {disconnect_time_str}, error: {e}"
                    )

    for client_id in cached_clients_to_delete:
        if client_id in client_cache:
            logger.info(
                f"Deleting data for disconnected client {client_id} from "
                f"in-memory cache."
            )
            del client_cache[client_id]
    if cached_clients_to_delete:
        logger.debug(
            f"Finished cache cleanup, deleted {len(cached_clients_to_delete)} "
            f"clients from cache."
        )

    logger.debug("Client cleanup cycle finished.")


async def cleanup_disconnected_clients():
    """Background task that periodically cleans up disconnected clients."""
    cleanup_interval = 60  # Check every 60 seconds
    logger.info("Starting background task: cleanup_disconnected_clients")
    while True:
        await asyncio.sleep(cleanup_interval)
        await perform_cleanup_cycle()


async def get_all_client_statuses() -> (
    tuple[dict[str, dict[str, str]], str, str | None]
):
    """
    Fetch all client statuses from Redis or cache.

    Returns:
        tuple: (statuses, data_source, error_msg)
    """
    statuses = {}
    error_msg = None

    if status_store["redis"] == "connected":
        try:
            async for key_b in redis_client.scan_iter("client:*:status"):
                key = key_b.decode()
                client_id = key.split(":")[1]
                status_data_raw = await redis_client.hgetall(key)
                statuses[client_id] = {
                    k.decode(): v.decode() for k, v in status_data_raw.items()
                }
            logger.debug(
                "Successfully fetched client statuses from Redis", count=len(statuses)
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to fetch client statuses from Redis", error=error_msg)
            status_store["redis"] = "unavailable"

    if not statuses and client_cache:
        logger.debug(
            "Using in-memory cache for client statuses (Redis empty or unavailable)",
            count=len(client_cache),
            redis_status=status_store["redis"],
        )
        statuses = client_cache.copy()
    elif status_store["redis"] != "connected" and client_cache:
        logger.debug(
            "Using in-memory cache for client statuses (Redis unavailable)",
            count=len(client_cache),
            redis_status=status_store["redis"],
        )
        statuses = client_cache.copy()

    data_source = (
        "redis"
        if status_store["redis"] == "connected" and not error_msg
        else "memory_cache"
    )

    return statuses, data_source, error_msg


async def get_client_info(client_id: str) -> dict[str, str] | None:
    """Get client info from Redis or cache."""
    client_info = client_cache.get(client_id)

    if status_store["redis"] == "connected":
        try:
            redis_key = f"client:{client_id}:status"
            client_info_redis_raw = await redis_client.hgetall(redis_key)
            if client_info_redis_raw:
                client_info = {
                    k.decode(): v.decode() for k, v in client_info_redis_raw.items()
                }
        except Exception as e:
            logger.warning(f"Could not verify client {client_id} status in Redis: {e}")

    return client_info


async def initialize_redis() -> None:
    """Initialize Redis connection and set status."""
    try:
        await redis_client.ping()
        status_store["redis"] = "connected"
        logger.info("Successfully connected to Redis")
    except redis.ConnectionError as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except redis.RedisError as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except Exception as e:
        status_store["redis"] = "unavailable"
        logger.error(
            "Failed to connect to Redis (UnknownError)",
            error=str(e),
        )


async def close_redis() -> None:
    """Close Redis connection."""
    try:
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.warning("Error while closing Redis connection", error=str(e))


__all__ = [
    "redis_client",
    "status_store",
    "client_cache",
    "redis_health_check",
    "redis_reconnector",
    "sync_cache_to_redis",
    "update_client_status",
    "perform_cleanup_cycle",
    "cleanup_disconnected_clients",
    "get_all_client_statuses",
    "get_client_info",
    "initialize_redis",
    "close_redis",
]
