"""Redis client management and related operations using redis.asyncio directly."""

import asyncio
import json  # For deserializing if needed, though Pydantic handles most.
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import redis.asyncio as redis_async  # Use redis.asyncio directly

from src.server import config
from src.shared.schemas.websocket import (
    AllClientStatuses,
    ClientStatus,
)
from src.shared.utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Define a callback type for broadcasting a SINGLE client status
SingleClientStatusBroadcastCallable = Callable[[ClientStatus], Awaitable[None]]

CLIENT_STATUS_KEY_PREFIX = "client_status:"
CHAT_STREAM_PREFIX = "chat_stream:"  # Prefix for chat streams per client
CHAT_GLOBAL_STREAM = "chat_global"  # Stream for broadcast messages


class RedisManager:
    """Manages Redis operations using redis.asyncio."""

    def __init__(self):
        self.status_store: dict[str, str] = {
            config.STATUS_KEY_REDIS_STATUS: config.STATUS_VALUE_REDIS_UNKNOWN
        }
        self._single_status_broadcast_callback: (
            SingleClientStatusBroadcastCallable | None
        ) = None
        self._redis_conn: redis_async.Redis | None = None

    async def _get_async_redis_connection(self) -> redis_async.Redis:
        """Get or create an async Redis connection using redis.asyncio."""
        if self._redis_conn and await self._redis_conn.ping():
            return self._redis_conn

        logger.info(
            f"Attempting to connect to Redis at {config.REDIS_HOST}:{config.REDIS_PORT}"
        )
        try:
            self._redis_conn = redis_async.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                # decode_responses=True, # Pydantic will handle JSON (bytes) to model
            )
            await self._redis_conn.ping()  # Test connection
            logger.info(
                f"Successfully connected to Redis: {config.REDIS_HOST}:{config.REDIS_PORT}"
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_CONNECTED
            )
            return self._redis_conn
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}", exc_info=True)
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            if self._redis_conn:
                await self._redis_conn.close()
            self._redis_conn = None
            raise  # Re-raise the exception to be handled by callers like initialize or reconnector

    def register_single_status_broadcast_callback(
        self, callback: SingleClientStatusBroadcastCallable
    ) -> None:
        """Registers a callback function for broadcasting single client status updates."""
        self._single_status_broadcast_callback = callback

    async def initialize(self) -> None:
        """Initialize Redis connection."""
        try:
            await self._get_async_redis_connection()
            # No Migrator().run() needed as we are not using redis-om schemas directly in Redis
            logger.info("RedisManager initialized successfully.")
        except Exception as e:
            # _get_async_redis_connection already logs and sets status
            logger.error(f"RedisManager initialization failed: {e}")
            # Ensure status reflects unavailability if connection failed during init
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )

    async def health_check(self) -> None:
        """Background task that frequently checks if Redis is available."""
        check_interval = config.REDIS_HEALTH_CHECK_INTERVAL_SECONDS

        while True:
            await asyncio.sleep(check_interval)
            try:
                if not self._redis_conn:
                    logger.warning(
                        "Health check: No active Redis connection, attempting to establish."
                    )
                    await self._get_async_redis_connection()
                else:
                    is_connected = await asyncio.wait_for(
                        self._redis_conn.ping(),
                        timeout=config.REDIS_HEALTH_CHECK_TIMEOUT_SECONDS,
                    )
                    if not is_connected:
                        raise ConnectionError(
                            "Redis ping returned False during health check."
                        )
                    if (
                        self.status_store[config.STATUS_KEY_REDIS_STATUS]
                        != config.STATUS_VALUE_REDIS_CONNECTED
                    ):
                        logger.info("Redis connection re-established by health check.")
                        self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                            config.STATUS_VALUE_REDIS_CONNECTED
                        )

            except Exception as e:
                old_status = self.status_store[config.STATUS_KEY_REDIS_STATUS]
                if old_status == config.STATUS_VALUE_REDIS_CONNECTED:
                    logger.error(
                        "Redis connection lost during health check",
                        error=str(e),
                        timestamp=datetime.now(UTC).isoformat(),
                    )
                self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                    config.STATUS_VALUE_REDIS_UNAVAILABLE
                )
                # Attempt to close the potentially broken connection
                if self._redis_conn:
                    try:
                        await self._redis_conn.close()
                    except Exception as e:
                        pass
                    self._redis_conn = None

    async def reconnector(self) -> None:
        """Background task that attempts to reconnect to Redis with exponential backoff."""
        base_delay = config.REDIS_RECONNECT_ATTEMPT_INTERVAL_SECONDS
        max_delay = 300
        current_delay = base_delay

        while True:
            if (
                self.status_store[config.STATUS_KEY_REDIS_STATUS]
                == config.STATUS_VALUE_REDIS_CONNECTED
            ):
                await asyncio.sleep(
                    current_delay
                )  # Check periodically even if connected
                current_delay = base_delay  # Reset delay if connection was good
                continue

            logger.info(f"Attempting to reconnect to Redis in {current_delay:.2f}s")
            await asyncio.sleep(current_delay)

            try:
                await self._get_async_redis_connection()  # This will set status to CONNECTED on success
                logger.info("Reconnected to Redis successfully.")
                current_delay = base_delay  # Reset delay on successful reconnect
            except Exception as e:
                logger.error(f"Redis reconnection attempt failed: {e}")
                current_delay = min(current_delay * 2, max_delay)
                current_delay = current_delay * (0.8 + 0.4 * random.random())

    def _get_client_key(self, client_id: str) -> str:
        return f"{CLIENT_STATUS_KEY_PREFIX}{client_id}"

    async def update_client_status(
        self,
        client_status: ClientStatus,
        broadcast: bool = True,
        originating_client_id: str | None = None,
    ) -> bool:
        """Update client status in Redis as a JSON string and optionally broadcast."""

        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning(
                f"Redis unavailable, cannot update client status for {client_status.client_id}"
            )
            return False

        try:
            client_key = self._get_client_key(client_status.client_id)
            # Pydantic V2 model_dump_json serializes to JSON string
            json_data = client_status.model_dump_json()
            await self._redis_conn.set(client_key, json_data)
            logger.debug(
                f"Updated client status for {client_status.client_id} in Redis."
            )

            if broadcast and self._single_status_broadcast_callback:
                logger.info(
                    f"RedisManager: Status updated for {client_status.client_id}, initiating broadcast."
                )
                await self._single_status_broadcast_callback(client_status)
            return True
        except Exception as e:
            logger.error(
                f"Failed to update client status in Redis for {client_status.client_id}",
                error=str(e),
                exc_info=True,
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return False

    async def get_client_info(self, client_id: str) -> ClientStatus | None:
        """Get client info from Redis (JSON string) and parse to ClientStatus model."""
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning(
                f"Cannot get client info for {client_id}: Redis unavailable."
            )
            return None

        try:
            client_key = self._get_client_key(client_id)
            json_data = await self._redis_conn.get(client_key)
            if json_data is None:
                logger.debug(
                    f"Client {client_id} not found in Redis (key: {client_key})."
                )
                return None
            # Pydantic V2 model_validate_json parses JSON string (or bytes) to model
            client_status = ClientStatus.model_validate_json(json_data)
            return client_status
        except (
            json.JSONDecodeError
        ) as e:  # Should be caught by Pydantic's validation usually
            logger.error(
                f"Failed to parse JSON for client {client_id}: {e}", exc_info=True
            )
            return None
        except Exception as e:  # Catches Pydantic ValidationError and others
            logger.error(
                f"Could not retrieve/parse client {client_id} status: {e}",
                exc_info=True,
            )
            # Potentially a Redis issue if not JSON/Pydantic error
            if not isinstance(
                e | (json.JSONDecodeError, ValueError)
            ):  # Pydantic validation errors are ValueError subclasses
                self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                    config.STATUS_VALUE_REDIS_UNAVAILABLE
                )
            return None

    async def get_all_client_statuses(self) -> AllClientStatuses | None:
        """Fetch all client statuses from Redis."""
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning("Cannot fetch client statuses: Redis unavailable.")
            return None

        statuses: dict[str, ClientStatus] = {}
        try:
            async for key_bytes in self._redis_conn.scan_iter(
                match=f"{CLIENT_STATUS_KEY_PREFIX}*"
            ):
                key = key_bytes.decode("utf-8")
                client_id_from_key = key.split(CLIENT_STATUS_KEY_PREFIX, 1)[1]
                json_data = await self._redis_conn.get(key)
                if json_data:
                    try:
                        client_status = ClientStatus.model_validate_json(json_data)
                        if client_status.client_id == client_id_from_key:
                            statuses[client_status.client_id] = client_status
                        else:
                            logger.warning(
                                f"Mismatch client_id in key vs data: {key} vs {client_status.client_id}"
                            )
                    except Exception as e_parse:  # Pydantic validation or JSON error
                        logger.error(
                            f"Failed to parse client status for key {key}: {e_parse}"
                        )
                else:
                    logger.warning(f"Got key {key} from scan but no data from GET.")

            logger.debug(
                f"Successfully fetched {len(statuses)} client statuses from Redis."
            )
            return AllClientStatuses.create(
                clients_dict=statuses, redis_status=self.get_redis_status()
            )
        except Exception as e:
            logger.error(
                "Failed to fetch all client statuses from Redis",
                error=str(e),
                exc_info=True,
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return None

    def get_redis_status(self) -> str:
        """Get the current Redis connection status."""
        return self.status_store.get(
            config.STATUS_KEY_REDIS_STATUS, config.STATUS_VALUE_REDIS_UNKNOWN
        )

    async def perform_cleanup_cycle(self):
        """Perform cleanup for disconnected clients."""
        max_disconnect_duration_seconds = (
            config.REDIS_CLIENT_TTL_SECONDS
        )  # Re-using this, but logic is different
        logger.debug("Running cleanup for disconnected clients...")
        current_time = datetime.now(UTC)

        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning("Redis unavailable, skipping cleanup cycle.")
            return

        clients_deleted_count = 0
        try:
            async for key_bytes in self._redis_conn.scan_iter(
                match=f"{CLIENT_STATUS_KEY_PREFIX}*"
            ):
                key = key_bytes.decode("utf-8")
                json_data = await self._redis_conn.get(key)
                if not json_data:
                    continue

                try:
                    client_status = ClientStatus.model_validate_json(json_data)
                    if (
                        client_status.connected == config.STATUS_VALUE_DISCONNECTED
                        and client_status.disconnect_time
                    ):
                        disconnect_dt = datetime.fromisoformat(
                            client_status.disconnect_time.replace("Z", "+00:00")
                        )
                        if (
                            current_time - disconnect_dt
                        ).total_seconds() > max_disconnect_duration_seconds:
                            logger.info(
                                f"Deleting data for stale disconnected client {client_status.client_id} (key: {key})."
                            )
                            await self._redis_conn.delete(key)
                            clients_deleted_count += 1
                except (
                    Exception
                ) as e_parse_check:  # Pydantic validation, JSON, or datetime error
                    logger.warning(
                        f"Error processing client {key} during cleanup: {e_parse_check}"
                    )

            if clients_deleted_count > 0:
                logger.debug(
                    f"Finished client cleanup, deleted {clients_deleted_count} clients."
                )
            else:
                logger.debug(
                    "Client cleanup cycle finished, no stale clients found to delete."
                )

        except Exception as e:
            logger.error(f"Error during client cleanup: {e}", exc_info=True)
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )

    async def cleanup_disconnected_clients(self):
        """Background task that periodically cleans up disconnected clients."""
        cleanup_interval = 60  # Check every 60 seconds
        logger.info("Starting background task: cleanup_disconnected_clients")
        while True:
            await asyncio.sleep(cleanup_interval)
            await self.perform_cleanup_cycle()

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis_conn:
            try:
                await self._redis_conn.close()
                # For redis.asyncio, wait_closed might not be needed or available in the same way
                # await self._redis_conn.wait_closed() # Check redis.asyncio docs if needed
                logger.info("Redis connection closed.")
            except Exception as e:
                logger.warning(
                    f"Error while closing Redis connection: {e}", exc_info=True
                )
            finally:
                self._redis_conn = None
        self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
            config.STATUS_VALUE_REDIS_UNAVAILABLE
        )

    async def delete_client_status(self, client_id: str) -> bool:
        """Delete a client's status from Redis by client_id."""
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning(
                f"Redis unavailable, cannot delete client status for {client_id}"
            )
            return False
        if not client_id:
            logger.warning("delete_client_status called with empty client_id.")
            return False

        try:
            client_key = self._get_client_key(client_id)
            deleted_count = await self._redis_conn.delete(client_key)
            if deleted_count > 0:
                logger.info(
                    f"Successfully deleted client status for {client_id} from Redis (key: {client_key})."
                )
                # Optionally, broadcast a client_removed message if not handled by the caller
                # For example, by calling a registered callback or directly using ws_manager if accessible
                # For now, the background_tasks.py handles broadcasting the disconnected state before calling delete.
                return True
            else:
                logger.warning(
                    f"No client status found to delete for client_id {client_id} (key: {client_key})."
                )
                return False
        except Exception as e:
            logger.error(
                f"Failed to delete client status from Redis for {client_id}",
                error=str(e),
                exc_info=True,
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return False

    # === Redis Streams for Chat Messages ===

    def _get_chat_stream_key(self, client_id: str) -> str:
        """Get the Redis stream key for a specific client's chat messages."""
        return f"{CHAT_STREAM_PREFIX}{client_id}"

    async def publish_chat_message_to_client(
        self, target_client_id: str, message_data: dict
    ) -> str | None:
        """Publish a chat message to a specific client's Redis stream.

        Args:
            target_client_id: The client ID to send the message to
            message_data: Dictionary containing the chat message data

        Returns:
            Message ID if successful, None if failed
        """
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning(
                f"Redis unavailable, cannot publish chat message to {target_client_id}"
            )
            return None

        try:
            stream_key = self._get_chat_stream_key(target_client_id)

            # Add timestamp and ensure all values are strings for Redis
            message_with_timestamp = {
                "timestamp": datetime.now(UTC).isoformat(),
                **message_data,
            }

            # Convert all values to strings as Redis streams require string values
            string_data = {k: str(v) for k, v in message_with_timestamp.items()}

            message_id = await self._redis_conn.xadd(stream_key, string_data)
            logger.info(
                f"Published chat message to client {target_client_id} stream: {message_id}"
            )
            return (
                message_id.decode("utf-8")
                if isinstance(message_id, bytes)
                else message_id
            )

        except Exception as e:
            logger.error(
                f"Failed to publish chat message to {target_client_id}: {e}",
                exc_info=True,
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return None

    async def publish_chat_message_broadcast(self, message_data: dict) -> str | None:
        """Publish a chat message to the global broadcast stream.

        Args:
            message_data: Dictionary containing the chat message data

        Returns:
            Message ID if successful, None if failed
        """
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning("Redis unavailable, cannot publish broadcast chat message")
            return None

        try:
            # Add timestamp and ensure all values are strings for Redis
            message_with_timestamp = {
                "timestamp": datetime.now(UTC).isoformat(),
                **message_data,
            }

            # Convert all values to strings as Redis streams require string values
            string_data = {k: str(v) for k, v in message_with_timestamp.items()}

            message_id = await self._redis_conn.xadd(CHAT_GLOBAL_STREAM, string_data)
            logger.info(f"Published broadcast chat message: {message_id}")
            return (
                message_id.decode("utf-8")
                if isinstance(message_id, bytes)
                else message_id
            )

        except Exception as e:
            logger.error(
                f"Failed to publish broadcast chat message: {e}", exc_info=True
            )
            self.status_store[config.STATUS_KEY_REDIS_STATUS] = (
                config.STATUS_VALUE_REDIS_UNAVAILABLE
            )
            return None

    async def create_consumer_group(
        self, stream_key: str, group_name: str, start_id: str = "0"
    ) -> bool:
        """Create a consumer group for a Redis stream.

        Args:
            stream_key: The Redis stream key
            group_name: Name of the consumer group
            start_id: Starting message ID (default "0" for beginning)

        Returns:
            True if successful, False otherwise
        """
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            logger.warning(
                f"Redis unavailable, cannot create consumer group {group_name}"
            )
            return False

        try:
            # Try to create the consumer group
            await self._redis_conn.xgroup_create(
                stream_key, group_name, start_id, mkstream=True
            )
            logger.info(
                f"Created consumer group '{group_name}' for stream '{stream_key}'"
            )
            return True

        except Exception as e:
            # Consumer group might already exist, which is fine
            if "BUSYGROUP" in str(e):
                logger.debug(
                    f"Consumer group '{group_name}' already exists for stream '{stream_key}'"
                )
                return True
            else:
                logger.error(
                    f"Failed to create consumer group '{group_name}' for stream '{stream_key}': {e}",
                    exc_info=True,
                )
                return False

    async def ensure_chat_consumer_groups(self) -> bool:
        """Ensure consumer groups exist for chat streams.

        Returns:
            True if all groups were created/verified successfully
        """
        success = True

        # Create consumer group for global broadcast stream
        if not await self.create_consumer_group(CHAT_GLOBAL_STREAM, "workers", "$"):
            success = False

        return success

    async def get_stream_length(self, stream_key: str) -> int:
        """Get the length of a Redis stream.

        Args:
            stream_key: The Redis stream key

        Returns:
            Stream length, or 0 if stream doesn't exist or on error
        """
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            return 0

        try:
            length = await self._redis_conn.xlen(stream_key)
            return length
        except Exception as e:
            logger.error(f"Failed to get stream length for {stream_key}: {e}")
            return 0

    async def cleanup_old_stream_messages(
        self, stream_key: str, max_length: int = 1000
    ) -> bool:
        """Trim old messages from a Redis stream to keep it at manageable size.

        Args:
            stream_key: The Redis stream key
            max_length: Maximum number of messages to keep

        Returns:
            True if successful, False otherwise
        """
        if (
            self.status_store[config.STATUS_KEY_REDIS_STATUS]
            != config.STATUS_VALUE_REDIS_CONNECTED
            or not self._redis_conn
        ):
            return False

        try:
            # Use XTRIM to limit stream length
            trimmed = await self._redis_conn.xtrim(
                stream_key, maxlen=max_length, approximate=True
            )
            if trimmed > 0:
                logger.debug(f"Trimmed {trimmed} messages from stream {stream_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to trim stream {stream_key}: {e}")
            return False


# Global instance
redis_manager = RedisManager()
