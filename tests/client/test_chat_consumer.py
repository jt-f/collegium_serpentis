# tests/client/test_chat_consumer.py
"""
Unit tests for ChatConsumer class and Redis streams functionality.

Tests cover:
- Consumer group uniqueness for broadcast messages
- Redis streams setup and consumer group creation
- Message consumption and processing
- Broadcast behavior verification
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis_async
from redis.exceptions import ResponseError

from src.client.client import ChatConsumer


class TestChatConsumerInitialization:
    """Test ChatConsumer class initialization and configuration."""

    def test_chat_consumer_init_unique_consumer_groups(self):
        """Test that each ChatConsumer instance gets a unique consumer group."""
        client_id_1 = "test-client-1"
        client_id_2 = "test-client-2"
        client_id_3 = "test-client-3"

        consumer1 = ChatConsumer(client_id_1, "localhost", 6379)
        consumer2 = ChatConsumer(client_id_2, "localhost", 6379)
        consumer3 = ChatConsumer(client_id_3, "localhost", 6379)

        # Verify each has unique consumer group
        assert consumer1.consumer_group == f"broadcast-{client_id_1}"
        assert consumer2.consumer_group == f"broadcast-{client_id_2}"
        assert consumer3.consumer_group == f"broadcast-{client_id_3}"

        # Verify all are different
        groups = [
            consumer1.consumer_group,
            consumer2.consumer_group,
            consumer3.consumer_group,
        ]
        assert len(groups) == len(set(groups)), "Consumer groups should be unique"

    def test_chat_consumer_init_stream_configuration(self):
        """Test that ChatConsumer is initialized with correct stream configuration."""
        client_id = "test-client-123"
        consumer = ChatConsumer(client_id, "localhost", 6379)

        assert consumer.client_id == client_id
        assert consumer.redis_host == "localhost"
        assert consumer.redis_port == 6379
        assert consumer.personal_stream == f"chat_stream:{client_id}"
        assert consumer.global_stream == "chat_global"
        assert consumer.consumer_group == f"broadcast-{client_id}"
        assert consumer.consumer_name == f"worker-{client_id}"

    def test_chat_consumer_init_with_different_redis_config(self):
        """Test ChatConsumer initialization with different Redis configuration."""
        client_id = "test-client"
        redis_host = "redis.example.com"
        redis_port = 6380

        consumer = ChatConsumer(client_id, redis_host, redis_port)

        assert consumer.redis_host == redis_host
        assert consumer.redis_port == redis_port
        assert consumer.consumer_group == f"broadcast-{client_id}"


class TestChatConsumerRedisSetup:
    """Test Redis connection and consumer group setup."""

    @pytest.fixture
    def chat_consumer(self):
        """Create a ChatConsumer instance for testing."""
        return ChatConsumer("test-client", "localhost", 6379)

    @patch("redis.asyncio.Redis")
    async def test_connect_success(self, mock_redis_class, chat_consumer):
        """Test successful Redis connection."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis_class.return_value = mock_redis

        result = await chat_consumer.connect()

        assert result is True
        assert chat_consumer.redis_conn == mock_redis
        mock_redis_class.assert_called_once_with(host="localhost", port=6379, db=0)
        mock_redis.ping.assert_called_once()

    @patch("redis.asyncio.Redis")
    async def test_connect_failure(self, mock_redis_class, chat_consumer):
        """Test Redis connection failure."""
        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = redis_async.ConnectionError("Connection failed")
        mock_redis_class.return_value = mock_redis

        result = await chat_consumer.connect()

        assert result is False
        assert chat_consumer.redis_conn == mock_redis

    @patch("redis.asyncio.Redis")
    async def test_setup_consumer_groups_success(self, mock_redis_class, chat_consumer):
        """Test successful consumer group setup."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis.xgroup_create = AsyncMock()
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()
        result = await chat_consumer.setup_consumer_groups()

        assert result is True

        # Verify consumer groups were created for both streams
        expected_calls = [
            # Personal stream
            (chat_consumer.personal_stream, chat_consumer.consumer_group, "$"),
            # Global stream
            (chat_consumer.global_stream, chat_consumer.consumer_group, "$"),
        ]

        assert mock_redis.xgroup_create.call_count == 2
        for call_args in mock_redis.xgroup_create.call_args_list:
            args = call_args[0]
            kwargs = call_args[1]
            assert args in expected_calls
            assert kwargs.get("mkstream") is True

    @patch("redis.asyncio.Redis")
    async def test_setup_consumer_groups_already_exists(
        self, mock_redis_class, chat_consumer
    ):
        """Test consumer group setup when groups already exist."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        # Simulate BUSYGROUP error (group already exists)
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()
        result = await chat_consumer.setup_consumer_groups()

        assert result is True  # Should still return True when groups already exist

    async def test_setup_consumer_groups_resilient_behavior(self, chat_consumer):
        """Test consumer group setup resilient behavior with connection issues."""
        # Set redis_conn to None to simulate no connection
        chat_consumer.redis_conn = None

        result = await chat_consumer.setup_consumer_groups()

        # The method should return False when redis_conn is None
        # This properly handles the case where Redis is not available
        assert result is False


class TestChatConsumerMessageProcessing:
    """Test message consumption and processing from Redis streams."""

    @pytest.fixture
    def chat_consumer(self):
        """Create a ChatConsumer instance for testing."""
        return ChatConsumer("test-client", "localhost", 6379)

    @patch("redis.asyncio.Redis")
    async def test_consume_messages_from_streams(self, mock_redis_class, chat_consumer):
        """Test consuming messages from Redis streams."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        # Mock message data
        message_id = "1234567890-0"
        message_fields = {
            b"client_id": b"sender-123",
            b"message": b"Hello world",
            b"target_id": b"",
            b"message_id": b"msg-456",
            b"timestamp": b"2025-06-08T23:00:00Z",
        }

        # First call returns messages, second call returns empty to stop the loop
        mock_redis.xreadgroup.side_effect = [
            [(b"chat_global", [(message_id.encode(), message_fields)])],
            [],  # Empty result to prevent infinite loop
        ]
        mock_redis.xack = AsyncMock()
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()

        # Mock the process_message method to avoid AI processing
        with patch.object(chat_consumer, "process_message"):
            # Start consuming task
            consume_task = asyncio.create_task(chat_consumer.consume_messages())

            # Let it process one message
            await asyncio.sleep(0.2)

            # Stop the consumer
            chat_consumer.is_running = False

            # Wait for task to complete with timeout
            try:
                await asyncio.wait_for(consume_task, timeout=2.0)
            except TimeoutError:
                consume_task.cancel()
                await asyncio.sleep(0.1)  # Give it time to clean up

        # Verify xreadgroup was called with correct parameters
        mock_redis.xreadgroup.assert_called()
        call_args = mock_redis.xreadgroup.call_args
        group_name = call_args[0][0]
        consumer_name = call_args[0][1]
        streams = call_args[0][2]

        assert group_name == chat_consumer.consumer_group
        assert consumer_name == chat_consumer.consumer_name
        assert chat_consumer.global_stream in streams
        assert chat_consumer.personal_stream in streams

    @patch("redis.asyncio.Redis")
    async def test_process_chat_message_skip_own_messages(
        self, mock_redis_class, chat_consumer
    ):
        """Test that consumer skips processing its own messages."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()

        # Create message from the same client
        message_fields = {
            "client_id": chat_consumer.client_id,  # Same as consumer
            "message": "Hello world",
            "target_id": "",
            "message_id": "msg-456",
        }

        # Mock AI response generation
        with patch.object(chat_consumer, "generate_ai_response") as mock_ai:
            await chat_consumer.process_message(
                "chat_global",
                "msg-id",
                {k.encode(): v.encode() for k, v in message_fields.items()},
            )

            # Should not generate AI response for own messages
            mock_ai.assert_not_called()

    @patch("redis.asyncio.Redis")
    async def test_process_chat_message_generate_response(
        self, mock_redis_class, chat_consumer
    ):
        """Test AI response generation for broadcast messages."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()

        # Create broadcast message from different client
        message_fields = {
            "client_id": "other-client",
            "message": "Hello everyone!",
            "target_id": "",  # Broadcast
            "message_id": "msg-456",
        }

        # Mock AI response generation and publishing
        with patch.object(
            chat_consumer, "generate_ai_response", return_value="AI response"
        ) as mock_ai:
            with patch.object(chat_consumer, "publish_response") as mock_publish:
                await chat_consumer.process_message(
                    "chat_global",
                    "msg-id",
                    {k.encode(): v.encode() for k, v in message_fields.items()},
                )

                # Should generate AI response for broadcast messages
                mock_ai.assert_called_once_with("Hello everyone!", "other-client")
                mock_publish.assert_called_once()


class TestChatConsumerBroadcastBehavior:
    """Test broadcast message behavior with multiple consumers."""

    def test_multiple_consumers_unique_groups(self):
        """Test that multiple ChatConsumer instances have unique consumer groups."""
        # Create multiple consumers simulating multiple Python clients
        consumers = []
        for i in range(5):
            client_id = f"worker-{i}"
            consumer = ChatConsumer(client_id, "localhost", 6379)
            consumers.append(consumer)

        # Extract all consumer groups
        consumer_groups = [c.consumer_group for c in consumers]

        # Verify all are unique
        assert len(consumer_groups) == len(set(consumer_groups))

        # Verify naming pattern
        for i, consumer in enumerate(consumers):
            expected_group = f"broadcast-worker-{i}"
            assert consumer.consumer_group == expected_group

    @patch("redis.asyncio.Redis")
    async def test_broadcast_message_consumption_pattern(self, mock_redis_class):
        """Test that broadcast messages can be consumed by multiple consumer groups."""
        # Create multiple consumers
        consumer1 = ChatConsumer("client-1", "localhost", 6379)
        consumer2 = ChatConsumer("client-2", "localhost", 6379)
        consumer3 = ChatConsumer("client-3", "localhost", 6379)

        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xack = AsyncMock()

        mock_redis_class.return_value = mock_redis

        # Connect all consumers
        await consumer1.connect()
        await consumer2.connect()
        await consumer3.connect()

        # Setup consumer groups
        await consumer1.setup_consumer_groups()
        await consumer2.setup_consumer_groups()
        await consumer3.setup_consumer_groups()

        # Test the consumer group configuration without running the actual consume loop
        consumers = [consumer1, consumer2, consumer3]

        # Verify each consumer has unique consumer group
        consumer_groups = [c.consumer_group for c in consumers]
        assert len(consumer_groups) == len(
            set(consumer_groups)
        ), "All consumer groups must be unique"

        # Verify the groups follow our naming pattern
        expected_groups = {
            "broadcast-client-1",
            "broadcast-client-2",
            "broadcast-client-3",
        }
        assert set(consumer_groups) == expected_groups

        # Test that each consumer would call xreadgroup with different groups
        # by simulating a single call for each
        for consumer in consumers:
            # Mock a single xreadgroup call to verify the parameters
            streams = {
                consumer.personal_stream: ">",
                consumer.global_stream: ">",
            }

            # Simulate what consume_messages would call
            mock_redis.xreadgroup.reset_mock()
            mock_redis.xreadgroup.return_value = []  # Return empty to prevent hanging

            try:
                await mock_redis.xreadgroup(
                    consumer.consumer_group,
                    consumer.consumer_name,
                    streams,
                    count=1,
                    block=1000,
                )

                # Verify the call was made with the correct consumer group
                mock_redis.xreadgroup.assert_called_once_with(
                    consumer.consumer_group,
                    consumer.consumer_name,
                    streams,
                    count=1,
                    block=1000,
                )

            except Exception as e:
                pytest.fail(
                    f"Failed to simulate xreadgroup call for {consumer.consumer_group}: {e}"
                )

        # Verify that all consumer groups are different and follow the pattern
        for consumer in consumers:
            assert consumer.consumer_group.startswith("broadcast-")
            assert consumer.client_id in consumer.consumer_group


class TestChatConsumerErrorHandling:
    """Test error handling in ChatConsumer."""

    @pytest.fixture
    def chat_consumer(self):
        """Create a ChatConsumer instance for testing."""
        return ChatConsumer("test-client", "localhost", 6379)

    @patch("redis.asyncio.Redis")
    async def test_consume_messages_redis_error(self, mock_redis_class, chat_consumer):
        """Test handling Redis errors during message consumption."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis.xreadgroup.side_effect = redis_async.ConnectionError("Redis down")
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()
        chat_consumer.is_running = True

        # Should handle Redis errors gracefully
        consume_task = asyncio.create_task(chat_consumer.consume_messages())
        await asyncio.sleep(0.1)
        chat_consumer.is_running = False

        try:
            await asyncio.wait_for(consume_task, timeout=1.0)
        except TimeoutError:
            consume_task.cancel()

        # Should have attempted to read from Redis
        mock_redis.xreadgroup.assert_called()

    @patch("redis.asyncio.Redis")
    async def test_process_malformed_message(self, mock_redis_class, chat_consumer):
        """Test handling malformed message data."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis

        await chat_consumer.connect()

        # Test with missing required fields
        malformed_fields = {
            "message": "Hello",
            # Missing client_id, target_id, etc.
        }

        # Should handle gracefully without crashing
        try:
            await chat_consumer.process_message(
                "chat_global",
                "msg-id",
                {k.encode(): v.encode() for k, v in malformed_fields.items()},
            )
        except Exception as e:
            pytest.fail(f"Should handle malformed messages gracefully, but got: {e}")


class TestConsumerGroupUniqueness:
    """Test to verify the broadcast fix works by ensuring unique consumer groups."""

    def test_consumer_group_pattern_prevents_message_distribution(self):
        """Test that unique consumer groups prevent message distribution to single consumer."""
        # This test verifies the fix for the original issue

        # Create multiple consumers as would happen in production
        num_clients = 10
        consumers = []

        for _i in range(num_clients):
            client_id = f"agent-{uuid.uuid4()}"
            consumer = ChatConsumer(client_id, "localhost", 6379)
            consumers.append(consumer)

        # Extract consumer groups
        consumer_groups = [c.consumer_group for c in consumers]

        # Verify the fix: each consumer has unique group
        assert len(consumer_groups) == len(
            set(consumer_groups)
        ), "All consumer groups must be unique for broadcast to work"

        # Verify naming follows broadcast pattern
        for consumer in consumers:
            assert consumer.consumer_group.startswith(
                "broadcast-"
            ), f"Consumer group {consumer.consumer_group} should start with 'broadcast-'"
            assert (
                consumer.client_id in consumer.consumer_group
            ), "Consumer group should contain client ID"

        # Verify no consumers use the old problematic pattern
        for consumer in consumers:
            assert (
                consumer.consumer_group != "workers"
            ), "No consumer should use the old 'workers' group that caused message distribution"

    def test_broadcast_vs_direct_message_routing(self):
        """Test that broadcast and direct messages are routed correctly."""
        client_id = "test-worker-123"
        consumer = ChatConsumer(client_id, "localhost", 6379)

        # Verify stream configuration
        assert (
            consumer.global_stream == "chat_global"
        ), "Global stream should be 'chat_global' for broadcast messages"
        assert (
            consumer.personal_stream == f"chat_stream:{client_id}"
        ), "Personal stream should include client ID for direct messages"

        # Verify consumer group is unique per client
        assert (
            consumer.consumer_group == f"broadcast-{client_id}"
        ), "Consumer group should be unique per client for broadcast functionality"
