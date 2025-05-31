"""Redis client management and related operations."""

import asyncio
import random
from datetime import UTC, datetime
from typing import Any

import redis

from src.server import config
from src.shared.utils.config import REDIS_CONFIG
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Initialize Redis client
redis_client = redis.asyncio.Redis(**REDIS_CONFIG)

# Status store to track service health
status_store: dict[str, str] = {
    config.STATUS_KEY_REDIS_STATUS: config.STATUS_VALUE_REDIS_UNKNOWN
}


async def redis_health_check() -> None:
    """
    Background task that frequently checks if Redis is available.
    This ensures the server notices immediately when Redis goes down.
    """
    check_interval = 3  # Check every 3 seconds

    while True:
        await asyncio.sleep(check_interval)

        if (
            status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
        ):
            continue

        try:
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
        except (TimeoutError, redis.ConnectionError) as e:
            old_status = status_store[config.STATUS_KEY_REDIS_STATUS]
            status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            logger.error(
                "Redis connection lost",
                error=str(e),
                previous_status=old_status,
                timestamp=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            old_status = status_store[config.STATUS_KEY_REDIS_STATUS]
            status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
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

        if (
            status_store[config.STATUS_KEY_REDIS_STATUS]
            == config.STATUS_VALUE_REDIS_CONNECTED
        ):
            current_delay = base_delay
            continue

        try:
            logger.debug("Attempting to reconnect to Redis", delay=current_delay)
            await redis_client.ping()
            status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_CONNECTED
            )
            logger.info("Reconnected to Redis")
            current_delay = base_delay
        except redis.ConnectionError as e:
            status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            logger.error(
                "Redis still unavailable",
                error=str(e),
                next_retry_in=current_delay * 2,
            )
            current_delay = min(current_delay * 2, max_delay)
            current_delay = current_delay * (0.8 + 0.4 * random.random())
        except Exception as e:
            status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            logger.error(
                "Redis still unavailable (UnknownError)",
                error=str(e),
                next_retry_in=current_delay,
            )
            current_delay = min(current_delay * 2, max_delay)


async def update_client_status(
    client_id: str, status_attributes: dict[str, Any]
) -> bool:
    """Update client status in Redis."""
    if not status_attributes:
        return True

    processed_attributes = {str(k): str(v) for k, v in status_attributes.items()}

    if (
        status_store[config.STATUS_KEY_REDIS_STATUS]
        != config.STATUS_VALUE_REDIS_CONNECTED
    ):
        logger.warning(
            "Redis unavailable, cannot update client status for %s",
            client_id,
        )
        return False

    try:
        redis_key = f"client:{client_id}:status"
        await redis_client.hset(redis_key, mapping=processed_attributes)
        logger.debug(
            "Updated client status in Redis for %s",
            client_id,
        )
        return True
    except (TimeoutError, redis.ConnectionError) as e:
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        logger.error(
            "Redis connection failed during status update for %s",
            client_id,
            error=str(e),
        )
        return False
    except Exception as e:
        logger.error(
            "Failed to update client status in Redis for %s",
            client_id,
            error=str(e),
        )
        return False


async def perform_cleanup_cycle():
    """Perform one cycle of client cleanup. This function can be tested directly."""
    max_disconnect_duration = 60  # 1 minute in seconds
    logger.debug("Running cleanup for disconnected clients...")
    current_time = datetime.now(UTC)
    clients_to_delete = []

    if (
        status_store[config.STATUS_KEY_REDIS_STATUS]
        != config.STATUS_VALUE_REDIS_CONNECTED
    ):
        logger.warning("Redis unavailable, skipping cleanup cycle.")
        return

    try:
        async for key_b in redis_client.scan_iter("client:*:status"):
            key = key_b.decode()
            client_id = key.split(":")[1]
            status_data_raw = await redis_client.hgetall(key)

            status_data = {k.decode(): v.decode() for k, v in status_data_raw.items()}

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

        if clients_to_delete:
            logger.debug(
                f"Finished Redis cleanup, deleted {len(clients_to_delete)} clients."
            )

    except redis.RedisError as e:
        logger.error(f"Redis error during client cleanup: {e}")
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )  # Mark Redis as unavailable
    except Exception as e:
        logger.error(f"Unexpected error during Redis client cleanup: {e}")

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
    Fetch all client statuses from Redis.

    Returns:
        tuple: (statuses, redis_status, error_msg)
    """
    statuses: dict[str, dict[str, str]] = {}
    error_msg: str | None = None
    redis_status = status_store.get(
        config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNKNOWN
    )

    if (
        status_store[config.STATUS_KEY_REDIS_STATUS]
        != config.STATUS_VALUE_REDIS_CONNECTED
    ):
        error_msg = "Redis is unavailable."
        logger.warning("Cannot fetch client statuses: Redis unavailable.")
        return statuses, redis_status, error_msg

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
    except (TimeoutError, redis.ConnectionError) as e:
        error_msg = str(e)
        logger.error(
            "Failed to fetch client statuses from Redis (Connection Error)",
            error=error_msg,
        )
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        redis_status = config.STATUS_VALUE_REDIS_UNAVAILABLE
    except redis.RedisError as e:
        error_msg = str(e)
        logger.error(
            "Failed to fetch client statuses from Redis (RedisError)", error=error_msg
        )
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        redis_status = config.STATUS_VALUE_REDIS_UNAVAILABLE
    except Exception as e:
        error_msg = str(e)
        logger.error(
            "Failed to fetch client statuses from Redis (Unknown Error)",
            error=error_msg,
        )
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        redis_status = config.STATUS_VALUE_REDIS_UNAVAILABLE

    return statuses, redis_status, error_msg


async def get_client_info(client_id: str) -> dict[str, str] | None:
    """Get client info from Redis."""
    if (
        status_store[config.STATUS_KEY_REDIS_STATUS]
        != config.STATUS_VALUE_REDIS_CONNECTED
    ):
        logger.warning(f"Cannot get client info for {client_id}: Redis unavailable.")
        return None

    try:
        redis_key = f"client:{client_id}:status"
        client_info_redis_raw = await redis_client.hgetall(redis_key)
        if client_info_redis_raw:
            return {k.decode(): v.decode() for k, v in client_info_redis_raw.items()}
        return None
    except (TimeoutError, redis.ConnectionError) as e:
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        logger.warning(
            f"Could not retrieve client {client_id} status from Redis (Connection Error): {e}"
        )
        return None
    except redis.RedisError as e:
        logger.warning(
            f"Could not retrieve client {client_id} status from Redis (RedisError): {e}"
        )
        return None
    except Exception as e:
        logger.warning(
            f"Could not retrieve client {client_id} status from Redis (Unknown Error): {e}"
        )
        return None


async def initialize_redis() -> None:
    """Initialize Redis connection and set status."""
    try:
        await redis_client.ping()
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_CONNECTED
        )
        logger.info("Successfully connected to Redis")
    except redis.ConnectionError as e:
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except redis.RedisError as e:
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        logger.error(
            "Failed to connect to Redis",
            error=str(e),
        )
    except Exception as e:
        status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )
        logger.error(
            "Failed to connect to Redis (UnknownError)",
            error=str(e),
        )


async def close_redis() -> None:
    """Close Redis connection."""
    try:
        await redis_client.close()
        await redis_client.wait_closed()
        logger.info("Redis connection closed and waited")
    except Exception as e:
        logger.warning("Error while closing Redis connection", error=str(e))


__all__ = [
    "redis_client",
    "status_store",
    "redis_health_check",
    "redis_reconnector",
    "update_client_status",
    "perform_cleanup_cycle",
    "cleanup_disconnected_clients",
    "get_all_client_statuses",
    "get_client_info",
    "initialize_redis",
    "close_redis",
]
