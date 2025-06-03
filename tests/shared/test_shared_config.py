"""Tests for shared configuration."""

from src.shared import config as shared_config
from src.shared.config import config


def test_shared_config_values():
    """Test the values of the shared configuration object."""
    assert config is not None
    assert shared_config.config is not None  # Testing the imported module object
    assert config == shared_config.config  # Ensure they are the same instance

    assert hasattr(config, "SERVER_HOST")
    assert config.SERVER_HOST == "0.0.0.0"

    assert hasattr(config, "REDIS_PORT")
    assert config.REDIS_PORT == 6379

    assert hasattr(config, "CLIENT_ROLE_FRONTEND")
    assert config.CLIENT_ROLE_FRONTEND == "frontend"

    assert hasattr(config, "MSG_TYPE_REGISTER")
    assert config.MSG_TYPE_REGISTER == "register"

    # Test the __all__ definition
    # Note: __all__ affects 'from src.shared.config import *'
    # Direct import of 'config' object is typical, but testing __all__ ensures
    # that if wildcard imports are ever used, 'config' is the only thing exported.
    # We access __all__ on the module object itself.
    assert hasattr(shared_config, "__all__")
    assert shared_config.__all__ == ["config"]
