#!/usr/bin/env python3
"""
Example Python client showing how to consume chat messages from Redis Streams.

This demonstrates how Python worker clients can consume chat messages
that are published to Redis Streams by the server.
"""

import asyncio
from datetime import datetime

import redis.asyncio as redis_async


class ChatConsumer:
    """Redis Streams consumer for chat messages."""

    def __init__(
        self, client_id: str, redis_host: str = "localhost", redis_port: int = 6379
    ):
        self.client_id = client_id
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_conn: redis_async.Redis | None = None
        self.is_running = False

        # Stream keys
        self.personal_stream = f"chat_stream:{client_id}"
        self.global_stream = "chat_global"
        self.consumer_group = "workers"
        self.consumer_name = f"worker-{client_id}"

    async def connect(self) -> bool:
        """Connect to Redis."""
        try:
            self.redis_conn = redis_async.Redis(
                host=self.redis_host, port=self.redis_port, db=0
            )
            await self.redis_conn.ping()
            print(f"✅ Connected to Redis at {self.redis_host}:{self.redis_port}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to Redis: {e}")
            return False

    async def setup_consumer_groups(self) -> bool:
        """Set up consumer groups for streams."""
        try:
            # Create consumer group for personal stream (starting from newest messages)
            try:
                await self.redis_conn.xgroup_create(
                    self.personal_stream, self.consumer_group, "$", mkstream=True
                )
                print(
                    f"✅ Created consumer group for personal stream: {self.personal_stream}"
                )
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    print(
                        f"ℹ️  Consumer group already exists for personal stream: {self.personal_stream}"
                    )
                else:
                    print(
                        f"⚠️  Failed to create consumer group for personal stream: {e}"
                    )

            # Create consumer group for global stream (starting from newest messages)
            try:
                await self.redis_conn.xgroup_create(
                    self.global_stream, self.consumer_group, "$", mkstream=True
                )
                print(
                    f"✅ Created consumer group for global stream: {self.global_stream}"
                )
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    print(
                        f"ℹ️  Consumer group already exists for global stream: {self.global_stream}"
                    )
                else:
                    print(f"⚠️  Failed to create consumer group for global stream: {e}")

            return True
        except Exception as e:
            print(f"❌ Failed to setup consumer groups: {e}")
            return False

    async def consume_messages(self) -> None:
        """Main message consumption loop."""
        print(f"🚀 Starting message consumption for client {self.client_id}")
        self.is_running = True

        while self.is_running:
            try:
                # Read from both personal and global streams
                streams = {
                    self.personal_stream: ">",  # Get new messages
                    self.global_stream: ">",  # Get new messages
                }

                # Use XREADGROUP to consume messages
                messages = await self.redis_conn.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    streams,
                    count=10,  # Read up to 10 messages at once
                    block=1000,  # Block for 1 second if no messages
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        stream_name = stream_name.decode("utf-8")
                        for message_id, fields in stream_messages:
                            message_id = message_id.decode("utf-8")
                            await self.process_message(stream_name, message_id, fields)

                            # Acknowledge the message
                            await self.redis_conn.xack(
                                stream_name, self.consumer_group, message_id
                            )

            except Exception as e:
                print(f"❌ Error in message consumption loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def process_message(
        self, stream_name: str, message_id: str, fields: dict[bytes, bytes]
    ) -> None:
        """Process a received chat message."""
        try:
            # Decode fields from bytes to strings
            decoded_fields = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in fields.items()
            }

            # Extract message data
            sender_id = decoded_fields.get("client_id", "unknown")
            message_text = decoded_fields.get("message", "")
            target_id = decoded_fields.get("target_id", "")
            sender_role = decoded_fields.get("sender_role", "unknown")
            timestamp = decoded_fields.get("timestamp", datetime.now().isoformat())

            # Determine message type
            is_direct = bool(target_id and target_id != "")
            is_personal = stream_name == self.personal_stream

            # Format message for display
            message_type = "📩 Direct" if is_direct else "📢 Broadcast"
            stream_type = "Personal" if is_personal else "Global"

            print(f"\n{message_type} message received on {stream_type} stream:")
            print(f"  From: {sender_id} ({sender_role})")
            print(f"  Message: {message_text}")
            print(f"  Time: {timestamp}")
            print(f"  Stream: {stream_name}")
            print(f"  Message ID: {message_id}")
            if is_direct:
                print(f"  Target: {target_id}")
            print("-" * 50)

            # Here you would add your business logic to handle the message
            # For example:
            # - Execute commands sent from the frontend
            # - Update local state based on instructions
            # - Send responses back via WebSocket or Redis

        except Exception as e:
            print(f"❌ Error processing message {message_id}: {e}")

    async def stop(self) -> None:
        """Stop the consumer."""
        print("🛑 Stopping message consumption...")
        self.is_running = False
        if self.redis_conn:
            await self.redis_conn.close()
            print("✅ Redis connection closed")


async def main():
    """Main function to demonstrate the chat consumer."""
    client_id = "python-worker-123"  # Replace with your actual client ID

    consumer = ChatConsumer(client_id)

    # Connect to Redis
    if not await consumer.connect():
        print("Failed to connect to Redis, exiting")
        return

    # Setup consumer groups
    if not await consumer.setup_consumer_groups():
        print("Failed to setup consumer groups, exiting")
        return

    try:
        # Start consuming messages
        await consumer.consume_messages()
    except KeyboardInterrupt:
        print("\n🔄 Received interrupt signal")
    finally:
        await consumer.stop()


if __name__ == "__main__":
    print("🐍 Python Client Redis Streams Consumer Example")
    print("=" * 50)
    print("This client will consume chat messages from Redis Streams")
    print("Press Ctrl+C to stop")
    print("=" * 50)

    asyncio.run(main())
