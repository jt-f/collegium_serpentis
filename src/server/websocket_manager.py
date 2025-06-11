# Websocket connection manager
import asyncio
import json
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import ValidationError

from src.server import config
from src.server.message_validation import validate_registration_message
from src.server.redis_manager import redis_manager
from src.shared.schemas.websocket import (
    AllClientsUpdateMessage,
    ChatAckMessage,
    ChatMessage,
    ClientStatus,
    ClientStatusUpdateMessage,
    CommandMessage,
    ControlResponseMessage,
    ErrorMessage,
    MessageProcessedMessage,
    MessageReceiptUnknownMessage,
    RegistrationCompleteMessage,
    ServerMessage,
)
from src.shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.client_contexts: dict[str, dict] = {}  # Store client_id -> context mapping
        self._stream_monitor_task: asyncio.Task | None = None
        self._monitor_running = False

    # Registers the client and starts a message loop
    async def handle_websocket_session(self, websocket: WebSocket) -> None:
        """Handle the complete websocket session lifecycle."""
        client_status: ClientStatus | None = None

        try:
            await websocket.accept()
            logger.info("New WebSocket connection established, awaiting registration")

            client_status = await self._handle_registration(websocket)
            if client_status is None:
                logger.error(
                    "Failed to handle registration. WebSocket may have been closed."
                )
                return

            await self._handle_message_loop(
                websocket, client_status.client_id, client_status.client_role
            )

        except WebSocketDisconnect:
            client_id_for_log = client_status.client_id if client_status else "unknown"
            client_role_for_log = (
                client_status.client_role if client_status else "unknown"
            )
            logger.info(
                f"Client disconnected: client_id='{client_id_for_log}', "
                f"role='{client_role_for_log}'"
            )
        except json.JSONDecodeError as e:
            client_id_for_log = (
                client_status.client_id if client_status else "unregistered client"
            )
            logger.error(
                f"{config.ERROR_MSG_INVALID_JSON} from {client_id_for_log}: {e}",
                exc_info=True,
            )
            await self._send_error_and_close_ws(
                websocket,
                config.ERROR_MSG_INVALID_JSON,
                config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR,
                log_level="none",
            )
        except Exception as e:
            client_id_for_log = (
                client_status.client_id if client_status else "unregistered client"
            )
            logger.error(
                f"Unexpected error in WebSocket session for client '{client_id_for_log}': {e}",
                exc_info=True,
            )
            await self._send_error_and_close_ws(
                websocket,
                "An unexpected error occurred. Connection will be closed.",
                config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
                log_level="none",
            )
        finally:
            if client_status and client_status.client_id:
                self.disconnect(client_status.client_id)
                logger.info(
                    f"Cleaned up connection for client {client_status.client_id}"
                )
                # Import here to avoid circular import
                from src.server.server import handle_disconnect

                # Trigger proper disconnection handling to update Redis and broadcast
                try:
                    await handle_disconnect(
                        client_status.client_id, websocket, "WebSocket connection lost"
                    )
                except Exception as e:
                    logger.error(
                        f"Error during disconnect handling for {client_status.client_id}: {e}"
                    )
            elif (
                client_status is None
                and websocket.client_state != WebSocketState.DISCONNECTED
            ):
                logger.warning(
                    "Client status object is None, but websocket state is not disconnected. Attempting to close."
                )
                try:
                    await websocket.close(
                        code=config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
                        reason="Registration failed cleanup",
                    )
                except Exception:
                    pass

    async def _handle_registration(self, websocket: WebSocket) -> ClientStatus | None:
        """Handle client registration process.
        Returns:
            ClientStatus object if successful, None if failed
        """
        try:
            initial_data = await websocket.receive_text()
        except Exception as e:
            await self._send_error_and_close_ws(
                websocket,
                f"Error receiving registration data: {e}",
                config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
                log_level="warning",
            )
            return None

        registration_msg = await validate_registration_message(initial_data, websocket)
        if registration_msg is None:
            if websocket.client_state == WebSocketState.CONNECTED:
                await self._send_error_and_close_ws(
                    websocket,
                    "Registration message validation failed.",
                    config.WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR,
                    log_level="warning",
                )
            else:
                logger.warning(
                    "Registration message validation failed and websocket was not connected"
                )
            return None

        # Enrich the registration message with default status values
        client_status = ClientStatus.enrich_from_registration(registration_msg)

        # Add the client to the active connections register
        self.active_connections[client_status.client_id] = websocket

        await self._handle_post_registration(websocket, client_status)

        return client_status

    async def _handle_post_registration(
        self,
        websocket: WebSocket,
        client_status: ClientStatus,
    ) -> None:
        """Handle post-registration tasks.

        Updates Redis ,
        sends registration complete ack, and sends all statuses to a newly
        registered frontend.
        """
        success = await redis_manager.update_client_status(
            client_status, broadcast=True
        )

        if not success:
            logger.error(
                f"Failed to update Redis for {client_status.client_id} in post-registration."
            )
            await self._send_error_and_close_ws(
                websocket,
                "Server error during registration finalization.",
                config.WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR,
                log_level="error",
            )
            return

        reg_complete_msg = RegistrationCompleteMessage(
            client_id=client_status.client_id,
            redis_status=redis_manager.get_redis_status(),
        )
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(reg_complete_msg.model_dump_json())
            else:
                logger.warning(
                    f"Cannot send reg_complete_msg to {client_status.client_id}, WS disconnected."
                )
        except Exception as e:
            logger.warning(
                f"Failed to send registration complete to {client_status.client_id}: {e}"
            )

        if client_status.client_role == config.CLIENT_ROLE_FRONTEND:
            await self.send_all_statuses_to_single_frontend(
                websocket, client_status.client_id
            )

    def disconnect(self, client_id: str) -> None:
        """Remove a client from connection tracking."""
        self.active_connections.pop(client_id, None)

    async def send_message(self, message: ServerMessage, client_id: str) -> bool:
        """Send a Pydantic message object to a specific client.

        Returns True if sent successfully.
        """
        if client_id not in self.active_connections:
            return False

        websocket = self.active_connections[client_id]
        try:
            await websocket.send_text(message.model_dump_json())
            return True
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(f"Client {client_id} disconnected during send: {e}")
            self.disconnect(client_id)
            return False
        except Exception as e:
            logger.error(f"Error sending to client {client_id}: {e}")
            self.disconnect(client_id)
            return False

    async def send_error(self, error: ErrorMessage, websocket: WebSocket) -> None:
        """Send an error message to a specific websocket."""
        await websocket.send_text(error.model_dump_json())

    async def _send_error_and_close_ws(
        self,
        websocket: WebSocket,
        error_message_content: str,
        close_code: int,
        log_level: str = "error",
        close_reason: str | None = None,
    ) -> None:
        """Logs an error, sends an ErrorMessage, and closes the WebSocket."""
        log_message = f"Sending error to client and closing WS (code={close_code}): {error_message_content}"
        if log_level == "error":
            logger.error(log_message)
        elif log_level == "warning":
            logger.warning(log_message)
        elif log_level == "info":
            logger.info(log_message)
        # If log_level is "none" or anything else, no log from here.

        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                error_model = ErrorMessage(error=error_message_content)
                await websocket.send_text(error_model.model_dump_json())
            except Exception as e_send:
                logger.error(
                    f"Failed to send error message to client before closing: {e_send}"
                )
            finally:  # Ensure close is attempted even if send fails
                try:
                    await websocket.close(code=close_code, reason=close_reason)
                except Exception as e_close:
                    logger.error(
                        f"Exception during WebSocket close after error: {e_close}"
                    )
        else:
            logger.info(
                f"WebSocket already closed or not connected. Error: {error_message_content}"
            )

    async def broadcast_json(self, message: ServerMessage) -> None:
        """Broadcast a Pydantic message object to all connected clients."""
        message_str = message.model_dump_json()
        for connection in self.active_connections.values():
            await connection.send_text(message_str)

    async def broadcast_to_frontends(
        self, message: ServerMessage, exclude_client_id: str | None = None
    ) -> None:
        """Broadcast a Pydantic message object to all frontend clients."""
        disconnected_clients = []
        message_str = message.model_dump_json()

        # Get all frontend client IDs from Redis
        for client_id in list(self.active_connections.keys()):
            # Skip the excluded client if specified
            if exclude_client_id and client_id == exclude_client_id:
                continue

            client_info = await redis_manager.get_client_info(client_id)
            if client_info and client_info.client_role == config.CLIENT_ROLE_FRONTEND:
                try:
                    await self.active_connections[client_id].send_text(message_str)
                except (WebSocketDisconnect, RuntimeError) as e:
                    logger.warning(
                        f"Frontend client {client_id} disconnected during "
                        f"broadcast: {e}"
                    )
                    disconnected_clients.append(client_id)
                except Exception as e:
                    logger.error(f"Error broadcasting to frontend {client_id}: {e}")
                    disconnected_clients.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected_clients:
            self.disconnect(client_id)

    async def broadcast_to_workers(
        self, message: ServerMessage, exclude_client_id: str | None = None
    ) -> int:
        """Broadcast a Pydantic message object to all worker (Python) clients.

        Returns the number of workers the message was sent to.
        """
        disconnected_clients = []
        message_str = message.model_dump_json()
        sent_count = 0

        # Get all worker client IDs from Redis
        for client_id in list(self.active_connections.keys()):
            # Skip the excluded client if specified
            if exclude_client_id and client_id == exclude_client_id:
                continue

            client_info = await redis_manager.get_client_info(client_id)
            if client_info and client_info.client_role == config.CLIENT_ROLE_WORKER:
                try:
                    await self.active_connections[client_id].send_text(message_str)
                    sent_count += 1
                    logger.debug(f"Sent message to worker {client_id}")
                except (WebSocketDisconnect, RuntimeError) as e:
                    logger.warning(
                        f"Worker client {client_id} disconnected during "
                        f"broadcast: {e}"
                    )
                    disconnected_clients.append(client_id)
                except Exception as e:
                    logger.error(f"Error broadcasting to worker {client_id}: {e}")
                    disconnected_clients.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected_clients:
            self.disconnect(client_id)

        return sent_count

    async def send_to_specific_worker(
        self, target_client_id: str, message: ServerMessage
    ) -> bool:
        """Send a message to a specific worker client.

        Returns True if sent successfully, False otherwise.
        """
        if target_client_id not in self.active_connections:
            logger.warning(f"Worker {target_client_id} not in active connections")
            return False

        client_info = await redis_manager.get_client_info(target_client_id)
        if not client_info or client_info.client_role != config.CLIENT_ROLE_WORKER:
            logger.warning(
                f"Attempted to send message to non-worker or unknown client: "
                f"{target_client_id}"
            )
            return False

        try:
            message_str = message.model_dump_json()
            await self.active_connections[target_client_id].send_text(message_str)
            logger.debug(f"Sent message to specific worker {target_client_id}")
            return True
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.warning(
                f"Worker client {target_client_id} disconnected during send: {e}"
            )
            self.disconnect(target_client_id)
            return False
        except Exception as e:
            logger.error(f"Error sending to worker {target_client_id}: {e}")
            self.disconnect(target_client_id)
            return False

    async def broadcast_client_status_update(self, client_status: ClientStatus) -> None:
        """Constructs and broadcasts a ClientStatusUpdateMessage to all frontends."""
        if not client_status or not client_status.client_id:
            logger.warning(
                "broadcast_client_status_update: Invalid client_status provided."
            )
            return

        logger.info(
            f"Broadcasting status update for client {client_status.client_id} to all frontends."
        )
        update_message = ClientStatusUpdateMessage(
            client_id=client_status.client_id,
            status=client_status.model_dump(),
            redis_status=redis_manager.get_redis_status(),
            timestamp=datetime.now(UTC).isoformat(),
        )
        await self.broadcast_to_frontends(update_message)

    async def send_all_statuses_to_single_frontend(
        self, websocket: WebSocket, client_id: str
    ) -> None:
        """Fetches all client statuses and sends them to a single frontend client."""
        logger.info(f"Sending initial full status list to new frontend {client_id}")
        all_statuses_obj = await redis_manager.get_all_client_statuses()
        if all_statuses_obj and all_statuses_obj.clients:  # Ensure there are clients
            initial_dump_msg = AllClientsUpdateMessage(
                data={
                    "clients": {
                        cid: cs.model_dump()
                        for cid, cs in all_statuses_obj.clients.items()
                    },
                    "redis_status": all_statuses_obj.redis_status,
                    "timestamp": all_statuses_obj.timestamp,
                }
            )
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(initial_dump_msg.model_dump_json())
                    logger.info(f"Successfully sent all_statuses to {client_id}")
                else:
                    logger.warning(
                        f"WebSocket for {client_id} is not connected. Cannot send all_statuses."
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to send initial all_statuses to {client_id}: {e}"
                )
        elif all_statuses_obj:  # No clients but Redis might be ok
            logger.info(
                f"No clients to send to {client_id}, but Redis status: {all_statuses_obj.redis_status}"
            )
            # Optionally send an empty client list if that's desired behavior
            empty_dump_msg = AllClientsUpdateMessage(
                data={
                    "clients": {},
                    "redis_status": all_statuses_obj.redis_status,
                    "timestamp": all_statuses_obj.timestamp,
                }
            )
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(empty_dump_msg.model_dump_json())
                    logger.info(f"Sent empty client list to {client_id}")
                else:
                    logger.warning(
                        f"WebSocket for {client_id} is not connected. Cannot send empty_statuses."
                    )
            except Exception as e:
                logger.warning(f"Failed to send empty client list to {client_id}: {e}")
        else:
            logger.warning(
                f"Could not get all_statuses for initial send to {client_id}."
            )

    async def send_to_worker(self, client_id: str, message: CommandMessage) -> bool:
        """Send a CommandMessage to a specific worker client.

        Returns True if sent successfully.
        """
        if client_id not in self.active_connections:
            return False

        client_info = await redis_manager.get_client_info(client_id)
        if not client_info or client_info.client_role == config.CLIENT_ROLE_FRONTEND:
            logger.warning(
                f"Attempted to send worker command to non-worker or unknown client: "
                f"{client_id}"
            )
            return False

        return await self.send_message(message, client_id)
        # send_message already handles Pydantic model

    async def close_connection(
        self, client_id: str, code: int = 1000, reason: str = "Server initiated"
    ) -> bool:
        """Close a specific client's connection."""
        if client_id not in self.active_connections:
            return False

        websocket = self.active_connections[client_id]
        try:
            await websocket.close(code=code, reason=reason)
            return True
        except Exception as e:
            logger.warning(f"Error closing connection for {client_id}: {e}")
            return False
        finally:
            self.disconnect(client_id)

    def is_connected(self, client_id: str) -> bool:
        """Check if a client is currently connected."""
        return client_id in self.active_connections

    async def get_connection_stats(self) -> dict:
        """Get connection statistics."""
        frontend_count = 0
        worker_count = 0

        for client_id in self.active_connections.keys():
            client_info = await redis_manager.get_client_info(client_id)
            if client_info:
                if client_info.client_role == config.CLIENT_ROLE_FRONTEND:
                    frontend_count += 1
                else:
                    worker_count += 1

        return {
            "total_connections": len(self.active_connections),
            "frontend_connections": frontend_count,
            "worker_connections": worker_count,
        }

    async def _handle_message_loop(
        self, websocket: WebSocket, client_id: str, client_role: str
    ) -> None:
        """Handle the main message processing loop."""
        while True:
            try:
                data = await websocket.receive_text()
                logger.debug(f"Received data: {{data}}")
                message = json.loads(data)
            except WebSocketDisconnect as e:
                logger.info(f"Client {client_id} disconnected during message loop: {e}")
                raise  # Re-raise to trigger cleanup in handle_websocket_session
            except Exception as e:
                logger.error(f"Error receiving/parsing message from {client_id}: {e}")
                raise  # Re-raise to trigger cleanup
            message_type = message.get(config.PAYLOAD_KEY_TYPE)

            if message_type == config.MSG_TYPE_CONTROL:
                await self._handle_control_message(
                    message, websocket, client_id, client_role
                )
            elif (
                message_type == config.MSG_TYPE_HEARTBEAT
                and client_role == config.CLIENT_ROLE_FRONTEND
            ):  # Handle heartbeat
                await self._handle_heartbeat_message(client_id, message)
            elif (
                message_type == config.MSG_TYPE_CHAT
                and client_role == config.CLIENT_ROLE_FRONTEND
            ):  # Handle chat message
                await self._handle_chat_message(message, websocket, client_id)
            elif (
                client_role != config.CLIENT_ROLE_FRONTEND
                and message_type == config.MSG_TYPE_STATUS
            ):  # Worker status update
                await self._handle_status_update(message, websocket, client_id)
            # Ensure other worker messages also update last_communication_timestamp if any
            elif client_role != config.CLIENT_ROLE_FRONTEND:
                logger.info(
                    f"Received other message type '{message_type}' from worker {client_id}. Updating last communication time."
                )
                await self._update_last_communication_time(client_id)
                # Potentially handle other worker message types here or log as unhandled
                # For now, just ack receipt if it's not a known type by _handle_status_update
                if (
                    message_type != config.MSG_TYPE_STATUS
                ):  # Avoid double ACK if it was status
                    ack_msg = MessageProcessedMessage(
                        client_id=client_id,
                        info=f"Received generic message type {message_type}",
                        status_updated={},  # Provide an empty dict for status_updated
                        redis_status=redis_manager.get_redis_status(),
                    )
                    await websocket.send_text(ack_msg.model_dump_json())

            else:  # Handles frontend messages that are not control or heartbeat
                await self._handle_unknown_frontend_message(
                    message, websocket, client_id
                )

    async def _handle_control_message(
        self, message: dict, websocket: WebSocket, client_id: str, client_role: str
    ) -> None:
        """Handle control messages from frontend clients."""
        action = message.get(config.PAYLOAD_KEY_ACTION)
        target_client_id = message.get(config.PAYLOAD_KEY_TARGET_CLIENT_ID)
        control_message_id = message.get(config.PAYLOAD_KEY_MESSAGE_ID)

        if client_role != config.CLIENT_ROLE_FRONTEND:
            logger.warning(
                f"Non-frontend client {client_id} attempted control action {action}"
            )
            error_response = ControlResponseMessage(
                action=action or "unknown",
                target_client_id=target_client_id or "unknown",
                message_id=control_message_id,
                status=config.MSG_TYPE_ERROR,
                message=config.ERROR_MSG_CONTROL_PERMISSION_DENIED,
                client_id=client_id,
            )
            await websocket.send_text(error_response.model_dump_json())
            return

        if not action or not target_client_id:
            error_response = ControlResponseMessage(
                action=action or "unknown",
                target_client_id=target_client_id or "unknown",
                message_id=control_message_id,
                status=config.MSG_TYPE_ERROR,
                message=config.ERROR_MSG_CONTROL_INVALID_PAYLOAD,
                client_id=client_id,
            )
            await websocket.send_text(error_response.model_dump_json())
            return

        logger.info(
            f"Frontend {client_id} sending control: {action} to {target_client_id}"
        )

        response_payload_dict: dict
        if action == config.CONTROL_ACTION_PAUSE:
            response_payload_dict = await self.pause_client(target_client_id, client_id)
        elif action == config.CONTROL_ACTION_RESUME:
            response_payload_dict = await self.resume_client(
                target_client_id, client_id
            )
        elif action == config.CONTROL_ACTION_DISCONNECT:
            response_payload_dict = await self.disconnect_client(
                target_client_id, client_id
            )
        else:
            response_payload_dict = {
                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                config.PAYLOAD_KEY_MESSAGE: f"Unknown control action: {action}",
            }

        final_response = ControlResponseMessage(
            action=action,
            target_client_id=target_client_id,
            message_id=control_message_id,
            status=response_payload_dict.get(
                config.PAYLOAD_KEY_STATUS, config.MSG_TYPE_ERROR
            ),
            message=response_payload_dict.get(
                config.PAYLOAD_KEY_MESSAGE, "Error processing command"
            ),
            client_id=response_payload_dict.get(
                config.PAYLOAD_KEY_CLIENT_ID, target_client_id
            ),
            redis_status=response_payload_dict.get(config.STATUS_KEY_REDIS_STATUS),
        )
        await websocket.send_text(final_response.model_dump_json())

    async def _handle_status_update(
        self, message: dict, websocket: WebSocket, client_id: str
    ) -> None:
        """Handle status update messages from worker clients."""
        logger.info(f"Handling status update from worker {client_id}")

        message_client_id = message.get(config.PAYLOAD_KEY_CLIENT_ID)
        if message_client_id and message_client_id != client_id:
            logger.warning(
                f"Worker {client_id} sent status with conflicting client_id "
                f"'{message_client_id}'. Ignoring message client_id, using authenticated session client_id."
            )

        incoming_status_attributes = message.get(config.PAYLOAD_KEY_STATUS, message)
        if not isinstance(incoming_status_attributes, dict):
            logger.warning(
                f"Non-dict status received from worker {client_id}: {incoming_status_attributes}. "
                "Wrapping as raw payload."
            )
            incoming_status_attributes = {
                config.STATUS_KEY_RAW_PAYLOAD: incoming_status_attributes
            }

        current_redis_status = redis_manager.get_redis_status()
        processed_successfully = False
        final_status_for_ack: dict = incoming_status_attributes.copy()

        try:
            # 1. Fetch existing client status from Redis
            existing_client_status = await redis_manager.get_client_info(client_id)
            if not existing_client_status:
                logger.error(
                    f"Cannot process status update for {client_id}: "
                    "Client not found in Redis. This should not happen for an authenticated worker."
                )
                # Send an error ACK? For now, just log and don't update.
                final_status_for_ack[config.STATUS_KEY_UPDATE_STATUS] = (
                    "failed_no_client_record"
                )
                final_status_for_ack[config.STATUS_KEY_REDIS_STATUS] = (
                    current_redis_status
                )
                # Optionally send an error message back or close.
                # For now, we'll still attempt to send an ACK with failure.
            else:
                # 2. Merge incoming attributes with existing status
                merged_data = existing_client_status.model_dump()
                merged_data.update(incoming_status_attributes)

                # 3. Ensure critical fields are set/updated
                now_iso = datetime.now(UTC).isoformat()
                merged_data["client_id"] = (
                    client_id  # Ensure it's the authenticated one
                )
                merged_data[config.STATUS_KEY_LAST_SEEN] = now_iso
                merged_data["last_communication_timestamp"] = (
                    now_iso  # Update communication timestamp
                )
                merged_data["connected"] = (
                    config.STATUS_VALUE_CONNECTED
                )  # Worker sending update is connected

                # Handle client state transitions based on the update
                current_server_state = merged_data.get(config.STATUS_KEY_CLIENT_STATE)
                incoming_client_state = incoming_status_attributes.get(
                    config.STATUS_KEY_CLIENT_STATE
                )

                if (
                    not incoming_client_state
                ):  # If client doesn't specify a new state in this update
                    if current_server_state == config.CLIENT_STATE_DORMANT:
                        merged_data[config.STATUS_KEY_CLIENT_STATE] = (
                            config.CLIENT_STATE_RUNNING
                        )  # Awaken from dormant
                        logger.info(
                            f"Client {client_id} awakened from dormant to running due to status update."
                        )
                    elif (
                        current_server_state == config.CLIENT_STATE_INITIALIZING
                        or current_server_state is None
                    ):
                        # If current state was 'initializing' OR None (e.g. from a registration that didn't set it),
                        # and the client sends a status update (implying it's active),
                        # move it to 'running'.
                        merged_data[config.STATUS_KEY_CLIENT_STATE] = (
                            config.CLIENT_STATE_RUNNING
                        )
                        logger.info(
                            f"Client {client_id} moved from '{current_server_state}' to running due to status update."
                        )
                # If incoming_client_state is provided, it will be used directly by the earlier merged_data.update(incoming_status_attributes)
                # So, if client sends 'client_state: "running"', that will take precedence.

                # 4. Validate the merged data
                updated_client_status_obj = ClientStatus.model_validate(merged_data)

                # 5. Update Redis (broadcast=True will trigger single status broadcast)
                processed_successfully = await redis_manager.update_client_status(
                    updated_client_status_obj, broadcast=True
                )
                if processed_successfully:
                    final_status_for_ack = (
                        updated_client_status_obj.model_dump()
                    )  # Use validated model for ACK
                    final_status_for_ack[config.STATUS_KEY_UPDATE_STATUS] = "success"
                    logger.info(
                        f"Successfully processed and updated status for worker {client_id}"
                    )
                else:
                    final_status_for_ack[config.STATUS_KEY_UPDATE_STATUS] = (
                        "failed_redis_update"
                    )
                    logger.error(
                        f"Failed to update Redis for worker {client_id} after successful validation."
                    )

        except ValidationError as ve:
            logger.error(
                f"Validation error processing status update for {client_id}: {ve}",
                exc_info=True,
            )
            final_status_for_ack[config.STATUS_KEY_UPDATE_STATUS] = "failed_validation"
        except Exception as e:
            logger.error(
                f"Unexpected error processing status update for {client_id}: {e}",
                exc_info=True,
            )
            final_status_for_ack[config.STATUS_KEY_UPDATE_STATUS] = (
                "failed_unexpected_error"
            )

        # Prepare and send ACK
        final_status_for_ack[config.STATUS_KEY_REDIS_STATUS] = current_redis_status

        try:
            # Moved ACK creation and serialization inside this try block
            ack_msg = MessageProcessedMessage(
                client_id=client_id,
                status_updated=final_status_for_ack,  # This now contains richer info
                redis_status=current_redis_status,
            )
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(ack_msg.model_dump_json())
            else:
                logger.warning(
                    f"Cannot send status update ACK to {client_id}, WebSocket is not connected."
                )
        except Exception as e_ack_prep_send:  # Catch errors from ACK prep or send
            logger.error(
                f"Failed to prepare or send status update ACK to {client_id}: {e_ack_prep_send}",
                exc_info=True,
            )

    async def _handle_unknown_frontend_message(
        self, message: dict, websocket: WebSocket, client_id: str
    ) -> None:
        """Handle unknown messages from frontend clients."""
        message_type = message.get(config.PAYLOAD_KEY_TYPE, "unknown")
        logger.debug(
            f"Received unhandled message type '{message_type}' from frontend {client_id}"
        )

        unknown_msg_response = MessageReceiptUnknownMessage(
            original_message=message,
            info=config.INFO_MSG_UNHANDLED_FRONTEND_MSG,
            client_id=client_id,
        )
        await websocket.send_text(unknown_msg_response.model_dump_json())

    async def _handle_chat_message(
        self, message: dict, websocket: WebSocket, client_id: str
    ) -> None:
        """Handle chat messages from frontend clients."""
        logger.info(f"Received chat message from frontend client {client_id}")

        # Extract message content using unified schema
        chat_content = message.get("message", "")
        message_timestamp = message.get("timestamp") or datetime.now(UTC).isoformat()
        message_id = message.get("message_id")
        target_id = message.get("target_id")
        in_response_to_message_id = message.get("in_response_to_message_id")

        if not chat_content.strip():
            logger.warning(f"Empty chat message received from client {client_id}")
            error_response = ChatAckMessage(
                client_id=client_id,
                message_id=message_id,
                timestamp=message_timestamp,
                redis_status=redis_manager.get_redis_status(),
            )
            await websocket.send_text(error_response.model_dump_json())
            return

        # Log the message flow
        target_info = f" to {target_id}" if target_id else " (broadcast)"
        response_info = (
            f" (responding to {in_response_to_message_id})"
            if in_response_to_message_id
            else ""
        )
        logger.info(
            f"Chat message from {client_id}{target_info}{response_info}: {chat_content[:100]}{'...' if len(chat_content) > 100 else ''}"
        )

        # Create acknowledgment
        ack_response = ChatAckMessage(
            client_id=client_id,
            message_id=message_id or f"ack-{datetime.now(UTC).timestamp()}",
            timestamp=message_timestamp,
            redis_status=redis_manager.get_redis_status(),
        )

        # Send acknowledgment back to sender
        await websocket.send_text(ack_response.model_dump_json())
        logger.info(f"Sent chat acknowledgment to client {client_id}")

        # Create a ChatMessage for broadcasting/routing using unified schema
        chat_message = ChatMessage(
            message_id=message_id or f"msg-{datetime.now(UTC).timestamp()}",
            client_id=client_id,
            message=chat_content,
            target_id=target_id,
            in_response_to_message_id=in_response_to_message_id,
            sender_role="frontend",
            timestamp=message_timestamp,
        )

        # Route message to Python workers via Redis Streams using unified schema
        message_data = {
            "type": "chat",
            "message_id": chat_message.message_id,
            "client_id": client_id,
            "message": chat_content,
            "target_id": target_id or "",  # Empty string if None for Redis
            "in_response_to_message_id": in_response_to_message_id or "",
            "sender_role": "frontend",
            "timestamp": message_timestamp,
        }

        if target_id:
            # Direct message to specific Python worker via Redis stream
            redis_message_id = await redis_manager.publish_chat_message_to_client(
                target_id, message_data
            )
            if redis_message_id:
                logger.info(
                    f"Published direct chat message from {client_id} to worker {target_id} "
                    f"stream (redis_message_id: {redis_message_id})"
                )
            else:
                logger.warning(
                    f"Failed to publish direct chat message from {client_id} to worker {target_id}"
                )
        else:
            # Broadcast to all Python workers via global Redis stream
            redis_message_id = await redis_manager.publish_chat_message_broadcast(
                message_data
            )
            if redis_message_id:
                logger.info(
                    f"Published broadcast chat message from {client_id} to global stream "
                    f"(redis_message_id: {redis_message_id})"
                )
            else:
                logger.warning(
                    f"Failed to publish broadcast chat message from {client_id}"
                )

        # Also broadcast to other frontends for visibility (excluding the sender)
        await self.broadcast_to_frontends(chat_message, exclude_client_id=client_id)

        broadcast_type = "direct message" if target_id else "broadcast message"
        logger.info(f"Broadcasted {broadcast_type} from {client_id} to other frontends")

    async def pause_client(
        self, target_client_id: str, originating_client_id: str | None = None
    ) -> dict:
        """Pause a worker client."""
        logger.info(
            f"Processing pause request for client {target_client_id}, "
            f"initiated by {originating_client_id or 'server'}"
        )

        client_info = await redis_manager.get_client_info(target_client_id)
        target_client_exists_and_connected = (
            client_info is not None
            and client_info.connected == config.STATUS_VALUE_CONNECTED
        )

        if not target_client_exists_and_connected:
            msg = (
                f"Client {target_client_id} not found or already disconnected. "
                f"Cannot pause."
            )
            logger.error(msg)
            return {
                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                config.PAYLOAD_KEY_MESSAGE: msg,
            }

        if client_info.client_state == config.CLIENT_STATE_PAUSED:
            msg = f"Client {target_client_id} is already paused. No action taken."
            logger.info(msg)
            return {
                config.PAYLOAD_KEY_STATUS: config.PAYLOAD_KEY_INFO,
                config.PAYLOAD_KEY_MESSAGE: msg,
            }

        pause_command = CommandMessage(command=config.CONTROL_ACTION_PAUSE)
        sent_command = await self.send_to_worker(target_client_id, pause_command)

        if not sent_command:
            logger.warning(
                f"Client {target_client_id} not actively connected via WebSocket "
                f"for pause. Updating state in Redis only."
            )

        status_update = {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_PAUSED}

        try:
            current_info = await redis_manager.get_client_info(target_client_id)
            if current_info:
                merged_data = current_info.model_dump()
                merged_data.update(status_update)
                merged_data["connected"] = config.STATUS_VALUE_CONNECTED
            else:  # Should not happen if target_client_exists_and_connected was true
                logger.error(
                    f"Client {target_client_id} info disappeared before pause update."
                )
                merged_data = status_update.copy()
                merged_data["client_id"] = target_client_id
                merged_data["connected"] = (
                    config.STATUS_VALUE_CONNECTED
                )  # Assume connected if trying to pause

            client_status_for_update = ClientStatus.model_validate(merged_data)
            await redis_manager.update_client_status(
                client_status_for_update,
                broadcast=True,  # This will broadcast the single paused client
            )
        except Exception as e:
            logger.error(
                f"Failed to update client status for pause on {target_client_id}: {e}"
            )

        return {
            config.PAYLOAD_KEY_STATUS: "success",
            config.PAYLOAD_KEY_MESSAGE: f"Pause command processed for client {target_client_id}. "
            f"State updated and broadcasted. WebSocket command "
            f"{'sent' if sent_command else 'not sent (client not connected)'}.",
        }

    async def resume_client(
        self, target_client_id: str, originating_client_id: str | None = None
    ) -> dict:
        """Resume a worker client."""
        logger.info(
            f"Processing resume request for client {target_client_id}, "
            f"initiated by {originating_client_id or 'server'}"
        )

        client_info = await redis_manager.get_client_info(target_client_id)
        target_client_exists_and_connected = (
            client_info is not None
            and client_info.connected == config.STATUS_VALUE_CONNECTED
        )

        if not target_client_exists_and_connected:
            msg = (
                f"Client {target_client_id} not found or already disconnected. "
                f"Cannot resume."
            )
            logger.error(msg)
            return {
                config.PAYLOAD_KEY_STATUS: config.MSG_TYPE_ERROR,
                config.PAYLOAD_KEY_MESSAGE: msg,
            }

        if client_info.client_state == config.CLIENT_STATE_RUNNING:
            msg = f"Client {target_client_id} is already running. No action taken."
            logger.info(msg)
            return {
                config.PAYLOAD_KEY_STATUS: config.PAYLOAD_KEY_INFO,
                config.PAYLOAD_KEY_MESSAGE: msg,
            }

        resume_command = CommandMessage(command=config.CONTROL_ACTION_RESUME)
        sent_command = await self.send_to_worker(target_client_id, resume_command)

        if not sent_command:
            logger.warning(
                f"Client {target_client_id} not actively connected via WebSocket "
                f"for resume. Updating state in Redis only."
            )

        status_update = {config.STATUS_KEY_CLIENT_STATE: config.CLIENT_STATE_RUNNING}

        try:
            current_info = await redis_manager.get_client_info(target_client_id)
            if current_info:
                merged_data = current_info.model_dump()
                merged_data.update(status_update)
                merged_data["connected"] = config.STATUS_VALUE_CONNECTED
            else:  # Should not happen
                logger.error(
                    f"Client {target_client_id} info disappeared before resume update."
                )
                merged_data = status_update.copy()
                merged_data["client_id"] = target_client_id
                merged_data["connected"] = config.STATUS_VALUE_CONNECTED

            client_status_for_update = ClientStatus.model_validate(merged_data)
            await redis_manager.update_client_status(
                client_status_for_update,
                broadcast=True,  # This will broadcast the single resumed client
            )
        except Exception as e:
            logger.error(
                f"Failed to update client status for resume on {target_client_id}: {e}"
            )

        return {
            config.PAYLOAD_KEY_STATUS: "success",
            config.PAYLOAD_KEY_MESSAGE: f"Resume command processed for client {target_client_id}. "
            f"State updated and broadcasted. WebSocket command "
            f"{'sent' if sent_command else 'not sent (client not connected)'}.",
        }

    async def disconnect_client(
        self, target_client_id: str, originating_client_id: str | None = None
    ) -> dict:
        """Disconnect a client."""
        logger.info(
            f"Processing disconnect request for client {target_client_id}, "
            f"initiated by {originating_client_id or 'server'}"
        )

        client_already_disconnected = False
        client_info_pre_disconnect = await redis_manager.get_client_info(
            target_client_id
        )
        if (
            client_info_pre_disconnect
            and client_info_pre_disconnect.connected == config.STATUS_VALUE_DISCONNECTED
        ):
            client_already_disconnected = True
            logger.info(
                f"Client {target_client_id} was already marked as disconnected in "
                f"Redis."
            )

        closed_connection = await self.close_connection(
            target_client_id,
            code=config.WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE,
            reason=config.REASON_SERVER_INITIATED_DISCONNECT,
        )

        # Prepare a nearly complete ClientStatus for the update
        # Some fields like client_role might come from client_info_pre_disconnect
        # if available, otherwise they will be None or default.
        disconnect_status_data = {
            "client_id": target_client_id,
            "connected": config.STATUS_VALUE_DISCONNECTED,
            "status_detail": config.STATUS_DETAIL_DISCONNECTED_BY_SERVER,
            "disconnect_time": datetime.now(UTC).isoformat(),
            "client_state": config.CLIENT_STATE_OFFLINE,
        }
        if client_info_pre_disconnect:  # Use existing info if available
            disconnect_status_data["client_role"] = (
                client_info_pre_disconnect.client_role
            )
            disconnect_status_data["client_type"] = (
                client_info_pre_disconnect.client_type
            )
            # Copy other relevant attributes that should persist through a disconnect
            if client_info_pre_disconnect.attributes:
                disconnect_status_data["attributes"] = (
                    client_info_pre_disconnect.attributes.copy()
                )
            if client_info_pre_disconnect.hostname:
                disconnect_status_data["hostname"] = client_info_pre_disconnect.hostname
            if client_info_pre_disconnect.ip_address:
                disconnect_status_data["ip_address"] = (
                    client_info_pre_disconnect.ip_address
                )

        try:
            client_status_for_disconnect = ClientStatus.model_validate(
                disconnect_status_data
            )
            await redis_manager.update_client_status(
                client_status_for_disconnect,
                broadcast=True,  # This will broadcast the single disconnected client
            )
        except Exception as e:
            logger.error(
                f"Failed to update client status for disconnect on {target_client_id}: {e}"
            )

        current_redis_status = redis_manager.get_redis_status()
        status_key: str
        message: str

        if not closed_connection and not client_already_disconnected:
            message = (
                f"Client {target_client_id} was not actively connected. Status "
                f"updated to disconnected and broadcasted."
            )
            status_key = config.PAYLOAD_KEY_INFO
        elif client_already_disconnected and not closed_connection:
            message = (
                f"Client {target_client_id} was already disconnected. Status "
                f"re-updated and broadcasted."
            )
            status_key = config.PAYLOAD_KEY_INFO
        else:  # closed_connection is True
            message = (
                f"Disconnection process for client {target_client_id} "
                f"initiated. WebSocket closed, status updated and broadcasted."
            )
            status_key = "success"
        logger.info(message)

        return {
            config.PAYLOAD_KEY_STATUS: status_key,
            config.PAYLOAD_KEY_MESSAGE: message,
            config.PAYLOAD_KEY_CLIENT_ID: target_client_id,  # Important for ControlResponseMessage
            config.STATUS_KEY_REDIS_STATUS: current_redis_status,
        }

    async def _update_last_communication_time(self, client_id: str) -> bool:
        """Helper to update only the last_communication_timestamp for a client."""
        try:
            existing_client_status = await redis_manager.get_client_info(client_id)
            if not existing_client_status:
                logger.warning(
                    f"Cannot update last_communication_time for {client_id}: Client not found in Redis."
                )
                return False

            now_iso = datetime.now(UTC).isoformat()
            if (
                existing_client_status.last_communication_timestamp == now_iso
            ):  # Avoid redundant updates
                return True

            update_data = {
                "last_communication_timestamp": now_iso,
                "last_seen": now_iso,
            }

            # Create a new ClientStatus object by updating the existing one
            # This ensures model validation runs if new fields were added or changed.
            updated_status_data = existing_client_status.model_dump()
            updated_status_data.update(update_data)

            # Ensure client_id is present, as it's key for ClientStatus
            if "client_id" not in updated_status_data:
                updated_status_data["client_id"] = client_id

            try:
                client_status_for_update = ClientStatus.model_validate(
                    updated_status_data
                )
            except ValidationError as ve_update:
                logger.error(
                    f"Validation error updating communication time for {client_id}: {ve_update}"
                )
                return False

            # Update Redis without broadcasting, as this is a passive update
            success = await redis_manager.update_client_status(
                client_status_for_update, broadcast=False
            )
            if success:
                logger.debug(f"Updated last_communication_timestamp for {client_id}.")
            else:
                logger.warning(
                    f"Failed to update last_communication_timestamp for {client_id} in Redis."
                )
            return success
        except Exception as e:
            logger.error(
                f"Error in _update_last_communication_time for {client_id}: {e}",
                exc_info=True,
            )
            return False

    async def _handle_heartbeat_message(self, client_id: str, message: dict) -> None:
        """Handle heartbeat messages from frontend clients."""
        logger.debug(f"Received heartbeat from client {client_id}")
        try:
            existing_client_status = await redis_manager.get_client_info(client_id)
            if not existing_client_status:
                logger.warning(
                    f"Heartbeat from unknown or unregistered client {client_id}. Ignoring."
                )
                return

            if existing_client_status.client_role != config.CLIENT_ROLE_FRONTEND:
                logger.warning(
                    f"Heartbeat received from non-frontend client {client_id} (role: {existing_client_status.client_role}). Ignoring."
                )
                return

            now_iso = datetime.now(UTC).isoformat()
            update_data = {"last_heartbeat_timestamp": now_iso, "last_seen": now_iso}

            updated_status_data = existing_client_status.model_dump()
            updated_status_data.update(update_data)
            if (
                "client_id" not in updated_status_data
            ):  # Should always be there from get_client_info
                updated_status_data["client_id"] = client_id

            try:
                client_status_for_update = ClientStatus.model_validate(
                    updated_status_data
                )
            except ValidationError as ve_heartbeat:
                logger.error(
                    f"Validation error updating heartbeat for {client_id}: {ve_heartbeat}"
                )
                return

            # Update Redis without broadcasting, as heartbeats are frequent
            success = await redis_manager.update_client_status(
                client_status_for_update, broadcast=False
            )
            if not success:
                logger.warning(
                    f"Failed to update heartbeat timestamp for {client_id} in Redis."
                )
            # No ACK for heartbeats to reduce traffic
        except Exception as e:
            logger.error(
                f"Error processing heartbeat for {client_id}: {e}", exc_info=True
            )

    async def start_stream_monitoring(self) -> None:
        """Start monitoring Redis streams for Python client responses."""
        if self._stream_monitor_task is None or self._stream_monitor_task.done():
            self._monitor_running = True
            self._stream_monitor_task = asyncio.create_task(
                self._monitor_redis_streams()
            )
            logger.info("Started Redis stream monitoring for chat responses")

    async def stop_stream_monitoring(self) -> None:
        """Stop monitoring Redis streams."""
        self._monitor_running = False
        if self._stream_monitor_task and not self._stream_monitor_task.done():
            self._stream_monitor_task.cancel()
            try:
                await self._stream_monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped Redis stream monitoring")

    async def _monitor_redis_streams(self) -> None:
        """Background task to monitor Redis streams for responses from Python clients."""
        consumer_group = "websocket_server"
        consumer_name = "ws-manager"
        global_stream = "chat_global"

        # Set up consumer group for global stream monitoring
        try:
            await redis_manager.create_consumer_group(
                global_stream, consumer_group, "$"
            )
        except Exception as e:
            logger.error(f"Failed to create consumer group for stream monitoring: {e}")
            return

        logger.info("Redis stream monitoring started")
        redis_conn = None

        while self._monitor_running:
            try:
                # Get Redis connection (reuse if possible)
                if redis_conn is None:
                    redis_conn = await redis_manager._get_async_redis_connection()

                # Check Redis connection health
                try:
                    await redis_conn.ping()
                except Exception:
                    logger.warning("Redis connection lost, reconnecting...")
                    redis_conn = await redis_manager._get_async_redis_connection()

                # Monitor global stream for broadcast responses
                streams = {global_stream: ">"}

                # Read from global stream
                messages = await redis_conn.xreadgroup(
                    consumer_group,
                    consumer_name,
                    streams,
                    count=10,  # Process up to 10 messages at once
                    block=1000,  # Block for 1 second if no messages
                )

                if messages:
                    for stream_name, stream_messages in messages:
                        stream_name = stream_name.decode("utf-8")
                        for message_id, fields in stream_messages:
                            message_id = message_id.decode("utf-8")

                            # Process the message
                            await self._process_stream_message(
                                stream_name, message_id, fields
                            )

                            # Acknowledge the message
                            try:
                                await redis_conn.xack(
                                    stream_name, consumer_group, message_id
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to ack message {message_id}: {e}"
                                )

                # Also monitor individual client streams for direct responses
                await self._monitor_individual_streams(
                    redis_conn, consumer_group, consumer_name
                )

            except asyncio.CancelledError:
                logger.info("Stream monitoring cancelled")
                break
            except Exception as e:
                if self._monitor_running:
                    logger.error(f"Error in stream monitoring: {e}")
                    redis_conn = None  # Reset connection on error
                    await asyncio.sleep(5)  # Wait before retrying
                else:
                    break

        # Clean up Redis connection
        if redis_conn:
            try:
                await redis_conn.aclose()
            except Exception:
                pass

    async def _monitor_individual_streams(
        self, redis_conn, consumer_group: str, consumer_name: str
    ) -> None:
        """Monitor individual client streams for direct message responses."""
        try:
            # Get all active frontend clients to monitor their streams
            frontend_clients = []
            for client_id in list(self.active_connections.keys()):
                client_info = await redis_manager.get_client_info(client_id)
                if (
                    client_info
                    and client_info.client_role == config.CLIENT_ROLE_FRONTEND
                ):
                    frontend_clients.append(client_id)

            if not frontend_clients:
                return

            # Build streams dict for individual client streams
            individual_streams = {}
            for client_id in frontend_clients:
                stream_key = f"chat_stream:{client_id}"
                individual_streams[stream_key] = ">"

                # Ensure consumer group exists for this stream
                try:
                    await redis_manager.create_consumer_group(
                        stream_key, consumer_group, "$"
                    )
                except Exception:
                    pass  # Group might already exist

            if individual_streams:
                # Read from individual streams
                individual_messages = await redis_conn.xreadgroup(
                    consumer_group,
                    consumer_name,
                    individual_streams,
                    count=5,  # Fewer messages per stream
                    block=100,  # Shorter block time for individual streams
                )

                if individual_messages:
                    for stream_name, stream_messages in individual_messages:
                        stream_name = stream_name.decode("utf-8")
                        for message_id, fields in stream_messages:
                            message_id = message_id.decode("utf-8")

                            # Process the message
                            await self._process_stream_message(
                                stream_name, message_id, fields
                            )

                            # Acknowledge the message
                            try:
                                await redis_conn.xack(
                                    stream_name, consumer_group, message_id
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to ack message {message_id}: {e}"
                                )

        except Exception as e:
            logger.warning(f"Error monitoring individual streams: {e}")

    async def _process_stream_message(
        self, stream_name: str, message_id: str, fields: dict
    ) -> None:
        """Process a message from Redis streams and forward to frontend clients."""
        try:
            # Decode fields from bytes to strings
            decoded_fields = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in fields.items()
            }

            # Extract message data using unified schema
            sender_id = decoded_fields.get("client_id", "unknown")
            message_text = decoded_fields.get("message", "")
            target_id = decoded_fields.get("target_id", "")
            in_response_to_message_id = decoded_fields.get(
                "in_response_to_message_id", ""
            )
            sender_role = decoded_fields.get("sender_role", "unknown")
            msg_message_id = decoded_fields.get("message_id", message_id)
            timestamp = decoded_fields.get("timestamp", datetime.now(UTC).isoformat())

            logger.info(f"Forwarding chat message from worker {{sender_id}} via stream {{stream_name}}: {{message_text[:100]}}{{'...' if len(message_text) > 100 else ''}}")
            # Only process messages from worker clients (responses)
            if sender_role != "worker":
                logger.debug(f"Skipping non-worker message from {sender_id}")
                return

            logger.info(
                f"Processing worker response from {sender_id} on stream {stream_name}"
            )

            # Create ChatMessage for broadcasting to frontends
            chat_message = ChatMessage(
                message_id=msg_message_id,
                client_id=sender_id,
                message=message_text,
                target_id=target_id if target_id else None,
                in_response_to_message_id=(
                    in_response_to_message_id if in_response_to_message_id else None
                ),
                sender_role="worker",
                timestamp=timestamp,
            )

            # Determine how to route the response
            if stream_name.startswith("chat_stream:"):
                # Direct response to specific frontend
                target_frontend_id = stream_name.replace("chat_stream:", "")
                await self._send_chat_to_specific_frontend(
                    chat_message, target_frontend_id
                )
            else:
                # Global response - broadcast to all frontends
                await self.broadcast_to_frontends(chat_message)

            logger.debug(
                f"Successfully forwarded worker response {msg_message_id} to frontends"
            )

        except Exception as e:
            logger.error(
                f"Error processing stream message {message_id}: {e}", exc_info=True
            )

    async def _send_chat_to_specific_frontend(
        self, chat_message: ChatMessage, frontend_client_id: str
    ) -> None:
        """Send a chat message to a specific frontend client."""
        if frontend_client_id in self.active_connections:
            try:
                websocket = self.active_connections[frontend_client_id]
                await websocket.send_text(chat_message.model_dump_json())
                logger.debug(
                    f"Sent direct chat response to frontend {frontend_client_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to send chat to frontend {frontend_client_id}: {e}"
                )
                self.disconnect(frontend_client_id)
        else:
            logger.debug(
                f"Frontend {frontend_client_id} not connected, cannot send direct response"
            )


manager = ConnectionManager()
