"""Shared configuration module that exposes server config."""

from src.server import config

# Re-export the config module for easy access
__all__ = ["config"]
