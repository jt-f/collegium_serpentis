"""Configuration management using environment variables."""

import os
from pathlib import Path
from typing import Any, List

from dotenv import load_dotenv

from src.shared.utils.logging import get_logger

# Define project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Load environment variables from .env file
env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=env_path)

# Initialize logger
logger = get_logger(__name__)

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
JSON_LOGS = os.getenv("JSON_LOGS", "false").lower() == "true"

# CORS Configuration
_cors_allowed_origins_str = os.getenv("CORS_ALLOWED_ORIGINS")
if _cors_allowed_origins_str and _cors_allowed_origins_str.strip():
    CORS_ALLOWED_ORIGINS: List[str] = [
        origin.strip() for origin in _cors_allowed_origins_str.split(",")
    ]
else:
    CORS_ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
logger.info(f"CORS allowed origins: {CORS_ALLOWED_ORIGINS}")

# Redis Configuration
REDIS_CONFIG: dict[str, Any] = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", 6379)),
    "db": int(os.getenv("REDIS_DB", 0)),
}

# Server Configuration
SERVER_CONFIG: dict[str, Any] = {
    "host": os.getenv("HOST", "0.0.0.0"),
    "port": int(os.getenv("PORT", 8000)),
    "reload": os.getenv("RELOAD", "false").lower() == "true",
}

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT == "production"

# Frontend Configuration
FRONTEND_BUILD_PATH_FROM_ROOT = os.getenv("FRONTEND_BUILD_PATH_FROM_ROOT", "frontend-command-centre/dist")
FRONTEND_BUILD_DIR = PROJECT_ROOT / FRONTEND_BUILD_PATH_FROM_ROOT

if not FRONTEND_BUILD_DIR.exists():
    logger.warning(f"Frontend build directory not found: {FRONTEND_BUILD_DIR}. Frontend will not be served.")
elif not (FRONTEND_BUILD_DIR / "index.html").exists():
    logger.warning(f"index.html not found in frontend build directory: {FRONTEND_BUILD_DIR}. Frontend may not be served correctly.")
