# src/server/config.py

# --- Server Configuration ---
SERVER_HOST = "0.0.0.0" # Host for the WebSocket server
SERVER_PORT = 8765 # Port for the WebSocket server
WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN = 1001 # Standard code for server going away
WEBSOCKET_CLOSE_CODE_CLIENT_DISCONNECT = 1000 # Standard code for normal closure by client
WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR = 1002 # Standard code for protocol error
WEBSOCKET_CLOSE_CODE_UNSUPPORTED_DATA = 1003 # Standard code for unsupported data
WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION = 1008 # Standard code for policy violation
WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD = 1007 # Consistent with CloseCode.INVALID_FRAME_PAYLOAD_DATA
WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR = 1011 # Consistent with CloseCode.INTERNAL_ERROR

# --- Redis Configuration ---
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_CLIENT_INFO_KEY_PREFIX = "client_info:" # Prefix for keys storing client information
REDIS_CLIENT_TTL_SECONDS = 3600 # Time-to-live for client keys in Redis (1 hour)
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = 30 # How often to check Redis health
REDIS_HEALTH_CHECK_TIMEOUT_SECONDS = 5 # Timeout for Redis health check
REDIS_RECONNECT_ATTEMPT_INTERVAL_SECONDS = 10 # Interval between Redis reconnect attempts

# --- Logging Configuration ---
LOG_LEVEL = "INFO" # Default log level
LOG_FORMAT_CONSOLE = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FORMAT_FILE = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE_PATH = "server.log" # Path to the log file
LOG_FILE_MAX_BYTES = 1024 * 1024 * 5 # Max log file size (5MB)
LOG_FILE_BACKUP_COUNT = 3 # Number of backup log files

# --- Path Configuration ---
STATUSES_ENDPOINT_PATH = "/statuses"
HEALTH_ENDPOINT_PATH = "/health"
WEBSOCKET_ENDPOINT_PATH = "/ws"
FRONTEND_BUILD_DIR = "static/frontend" # Relative to the project root where server is run

# --- CORS Configuration ---
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173", # Default Vite dev server
    "http://127.0.0.1:5173",
    # Add other origins as needed, e.g., production frontend URL
]

# --- Client Roles ---
CLIENT_ROLE_FRONTEND = "frontend"
CLIENT_ROLE_WORKER = "worker" # Generic term for non-frontend clients

# --- Status Keys ---
STATUS_KEY_CONNECTED = "connected"
STATUS_KEY_CONNECT_TIME = "connect_time"
STATUS_KEY_LAST_SEEN = "last_seen"
STATUS_KEY_DISCONNECT_TIME = "disconnect_time"
STATUS_KEY_CLIENT_ROLE = "client_role"
STATUS_KEY_REDIS_STATUS = "redis_status"
STATUS_KEY_STATUS_DETAIL = "status_detail"
STATUS_KEY_CLIENT_STATE = "client_state"
STATUS_KEY_RAW_PAYLOAD = "raw_payload"

# --- Common Status Values ---
STATUS_VALUE_CONNECTED = "true"
STATUS_VALUE_DISCONNECTED = "false"
STATUS_VALUE_REDIS_UNAVAILABLE = "unavailable"
STATUS_VALUE_REDIS_UNKNOWN = "unknown"
# Note: "connected" for redis_status usually comes from status_store

CLIENT_STATE_OFFLINE = "offline"
CLIENT_STATE_PAUSED = "paused"
CLIENT_STATE_RUNNING = "running"

# --- WebSocket Message Types ---
MSG_TYPE_ALL_CLIENTS_UPDATE = "all_clients_update"
MSG_TYPE_CLIENT_STATUS_UPDATE = "client_status_update"
MSG_TYPE_REGISTRATION_COMPLETE = "registration_complete"
MSG_TYPE_CONTROL = "control"
MSG_TYPE_CONTROL_RESPONSE = "control_response"
MSG_TYPE_MESSAGE_PROCESSED = "message_processed"
MSG_TYPE_MESSAGE_RECEIPT_UNKNOWN = "message_receipt_unknown"
# Specific to server.py internal logic, not part of client-server message spec as such
MSG_TYPE_ERROR = "error" # For generic error messages in responses

# --- Control Actions ---
CONTROL_ACTION_PAUSE = "pause"
CONTROL_ACTION_RESUME = "resume"
CONTROL_ACTION_DISCONNECT = "disconnect"

# --- Common Payloads/Keys for Messages ---
# These are keys used within the JSON payloads of messages
PAYLOAD_KEY_CLIENT_ID = "client_id"
PAYLOAD_KEY_STATUS = "status"
PAYLOAD_KEY_TYPE = "type"
PAYLOAD_KEY_DATA = "data"
PAYLOAD_KEY_CLIENTS = "clients"
PAYLOAD_KEY_ACTION = "action"
PAYLOAD_KEY_TARGET_CLIENT_ID = "target_client_id"
PAYLOAD_KEY_MESSAGE_ID = "message_id" # Optional for tracking control messages
PAYLOAD_KEY_MESSAGE = "message" # For textual messages in responses
PAYLOAD_KEY_COMMAND = "command" # For commands sent to clients
PAYLOAD_KEY_RESULT = "result" # For results of operations
PAYLOAD_KEY_TIMESTAMP = "timestamp"
PAYLOAD_KEY_INFO = "info"
PAYLOAD_KEY_ERROR_REDIS = "error_redis" # Specific error key for redis issues in status response
PAYLOAD_KEY_STATUS_UPDATED = "status_updated" # For worker status update acknowledgements
PAYLOAD_KEY_ORIGINAL_MESSAGE = "original_message" # For unknown message type responses

# --- Static Files ---
STATIC_FILES_NAME = "static-frontend"

# --- Default Messages & Reasons ---
REASON_SERVER_SHUTDOWN = "Server shutting down"
REASON_DISCONNECTED_BY_CLIENT = "Disconnected by client"
REASON_SERVER_INITIATED_DISCONNECT_HTTP = "Server initiated disconnect via HTTP"
STATUS_DETAIL_DISCONNECTED_BY_SERVER_HTTP = "Disconnected by server request (HTTP)"

ERROR_MSG_INVALID_JSON_REGISTRATION = "Invalid JSON format for registration"
ERROR_MSG_REGISTRATION_MISSING_IDS = "client_id and client_role in status are required for registration"
ERROR_MSG_CONTROL_PERMISSION_DENIED = "Permission denied: Only frontend clients can issue control commands."
ERROR_MSG_CONTROL_INVALID_PAYLOAD = "'action' and 'target_client_id' are required for control commands."
ERROR_MSG_INVALID_JSON = "Invalid JSON format"
INFO_MSG_UNHANDLED_FRONTEND_MSG = "Message type not recognized for frontend clients, aside from 'control'."

# --- Health Status ---
HEALTH_STATUS_HEALTHY = "healthy"
