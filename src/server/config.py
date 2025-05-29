# Configuration settings for the server
import os

# --- Paths ---
# Assumes 'frontend-command-centre' is at the root of the workspace,
# and this config file is in 'src/server/'
FRONTEND_BUILD_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend-command-centre", "dist"
    )
)

# --- CORS ---
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",  # Vite default dev port
    "http://127.0.0.1:5173",
    "http://localhost:3000",  # Common React dev port (e.g. Create React App)
    "http://127.0.0.1:3000",
    # Add your production frontend URL here when you deploy
]

# WebSocket Close Codes
WEBSOCKET_CLOSE_CODE_NORMAL_CLOSURE = 1000
WEBSOCKET_CLOSE_CODE_SERVER_SHUTDOWN = 1001  # Or GOING_AWAY
WEBSOCKET_CLOSE_CODE_PROTOCOL_ERROR = 1002
WEBSOCKET_CLOSE_CODE_UNSUPPORTED_DATA = 1003
WEBSOCKET_CLOSE_CODE_POLICY_VIOLATION = 1008
WEBSOCKET_CLOSE_CODE_INTERNAL_ERROR = 1011
WEBSOCKET_CLOSE_CODE_INVALID_PAYLOAD = 1007  # Invalid frame payload data
# Add other WebSocket close codes as needed, e.g.:
# WEBSOCKET_CLOSE_CODE_UNSUPPORTED_DATA = 1003


# --- API Endpoint Paths --- #
WEBSOCKET_ENDPOINT_PATH = "/ws"
STATUSES_ENDPOINT_PATH = "/api/v1/statuses"
DISCONNECT_CLIENT_ENDPOINT_PATH = "/api/v1/clients/{client_id}/disconnect"
PAUSE_CLIENT_ENDPOINT_PATH = "/api/v1/clients/{client_id}/pause"
RESUME_CLIENT_ENDPOINT_PATH = "/api/v1/clients/{client_id}/resume"
HEALTH_ENDPOINT_PATH = "/api/v1/health"

# --- Logging Configuration (Placeholder) ---
