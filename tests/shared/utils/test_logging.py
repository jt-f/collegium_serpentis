"""
Comprehensive tests for the logging module.

This module tests all logging functionality including setup, configuration,
and utility functions to achieve close to 100% code coverage.
"""

import logging
import warnings
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from structlog.types import EventDict

from src.shared.utils.logging import (
    CONSOLE_FORMAT,
    LOG_COLORS,
    add_service_info,
    get_logger,
    setup_logging,
)


class TestLoggingConstants:
    """Test module-level constants and configurations."""

    def test_log_colors_defined(self):
        """Test that LOG_COLORS contains all expected log levels."""
        expected_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in expected_levels:
            assert level in LOG_COLORS
            assert isinstance(LOG_COLORS[level], str)

    def test_console_format_defined(self):
        """Test that CONSOLE_FORMAT is properly defined."""
        assert isinstance(CONSOLE_FORMAT, str)
        assert "%(log_color)s" in CONSOLE_FORMAT
        assert "%(levelname)" in CONSOLE_FORMAT
        assert "%(name)s" in CONSOLE_FORMAT
        assert "%(message)" in CONSOLE_FORMAT


class TestAddServiceInfo:
    """Test the add_service_info function."""

    def test_add_service_info_with_record(self):
        """Test add_service_info when _record is present in event_dict."""
        # Create a mock record with required attributes
        mock_record = Mock()
        mock_record.filename = "test_file.py"
        mock_record.funcName = "test_function"
        mock_record.lineno = 42

        event_dict: EventDict = {
            "_record": mock_record,
            "message": "test message",
        }

        logger = logging.getLogger("test")
        result = add_service_info(logger, "test_method", event_dict)

        assert result["filename"] == "test_file.py"
        assert result["funcName"] == "test_function"
        assert result["lineno"] == 42
        assert result["message"] == "test message"
        assert result["_record"] == mock_record

    def test_add_service_info_without_record(self):
        """Test add_service_info when _record is not present in event_dict."""
        event_dict: EventDict = {
            "message": "test message",
            "level": "INFO",
        }

        logger = logging.getLogger("test")
        result = add_service_info(logger, "test_method", event_dict)

        # Should return the original event_dict unchanged
        assert result == event_dict
        assert "filename" not in result
        assert "funcName" not in result
        assert "lineno" not in result

    def test_add_service_info_with_none_record(self):
        """Test add_service_info when _record is None."""
        event_dict: EventDict = {
            "_record": None,
            "message": "test message",
        }

        logger = logging.getLogger("test")
        result = add_service_info(logger, "test_method", event_dict)

        # Should return the original event_dict unchanged
        assert result == event_dict
        assert "filename" not in result
        assert "funcName" not in result
        assert "lineno" not in result


class TestSetupLogging:
    """Test the setup_logging function."""

    def setUp(self):
        """Set up test environment."""
        # Store original handlers to restore later
        self.original_handlers = logging.getLogger().handlers[:]

    def tearDown(self):
        """Clean up test environment."""
        # Restore original handlers
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.handlers.extend(self.original_handlers)

    @patch("src.shared.utils.logging.JSON_LOGS", False)
    @patch("src.shared.utils.logging.LOG_LEVEL", "DEBUG")
    def test_setup_logging_non_json(self):
        """Test setup_logging with non-JSON configuration."""
        mock_stdout = StringIO()
        with patch("sys.stdout", mock_stdout):
            setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG
        assert len(root_logger.handlers) == 1

        handler = root_logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream == mock_stdout

        # Check that colorlog formatter is used
        formatter = handler.formatter
        assert hasattr(formatter, "log_colors")

    @patch("src.shared.utils.logging.JSON_LOGS", True)
    @patch("src.shared.utils.logging.LOG_LEVEL", "INFO")
    def test_setup_logging_json(self):
        """Test setup_logging with JSON configuration."""
        mock_stdout = StringIO()
        with patch("sys.stdout", mock_stdout):
            setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) == 1

        handler = root_logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream == mock_stdout

        # Check that standard formatter is used for JSON
        formatter = handler.formatter
        assert isinstance(formatter, logging.Formatter)
        assert "%(asctime)s" in formatter._fmt
        assert "%(levelname)" in formatter._fmt

    @patch("src.shared.utils.logging.JSON_LOGS", False)
    @patch("src.shared.utils.logging.LOG_LEVEL", "WARNING")
    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        root_logger = logging.getLogger()

        # Add a dummy handler
        dummy_handler = logging.StreamHandler()
        root_logger.addHandler(dummy_handler)

        with patch("sys.stdout", new_callable=StringIO):
            setup_logging()

        # Should have exactly one handler (the new one)
        assert len(root_logger.handlers) == 1
        assert dummy_handler not in root_logger.handlers

    @patch("src.shared.utils.logging.JSON_LOGS", True)
    def test_setup_logging_structlog_json_processors(self):
        """Test that structlog is configured with JSON processors when
        JSON_LOGS is True."""
        with patch("structlog.configure") as mock_configure:
            with patch("sys.stdout", new_callable=StringIO):
                setup_logging()

            mock_configure.assert_called_once()
            call_args = mock_configure.call_args
            processors = call_args[1]["processors"]

            # Check that JSON-specific processors are included
            processor_types = [type(p).__name__ for p in processors]
            assert "TimeStamper" in processor_types
            assert "JSONRenderer" in processor_types

    @patch("src.shared.utils.logging.JSON_LOGS", False)
    def test_setup_logging_structlog_console_processors(self):
        """Test that structlog is configured with console processors when
        JSON_LOGS is False."""
        with patch("structlog.configure") as mock_configure:
            with patch("sys.stdout", new_callable=StringIO):
                setup_logging()

            mock_configure.assert_called_once()
            call_args = mock_configure.call_args
            processors = call_args[1]["processors"]

            # Check that console-specific processors are included
            processor_types = [type(p).__name__ for p in processors]
            assert "TimeStamper" in processor_types
            assert "ConsoleRenderer" in processor_types

    def test_setup_logging_structlog_shared_processors(self):
        """Test that structlog is configured with shared processors."""
        with patch("structlog.configure") as mock_configure:
            with patch("sys.stdout", new_callable=StringIO):
                setup_logging()

            mock_configure.assert_called_once()
            call_args = mock_configure.call_args
            processors = call_args[1]["processors"]

            # Check that shared processors are included
            processor_functions = [
                getattr(p, "__name__", type(p).__name__) for p in processors
            ]
            assert "merge_contextvars" in processor_functions
            assert "add_log_level" in processor_functions
            assert "add_logger_name" in processor_functions
            assert "add_service_info" in processor_functions

    def test_setup_logging_structlog_configuration_options(self):
        """Test that structlog is configured with correct options."""
        with patch("structlog.configure") as mock_configure:
            with patch("sys.stdout", new_callable=StringIO):
                setup_logging()

            mock_configure.assert_called_once()
            call_args = mock_configure.call_args
            config_kwargs = call_args[1]

            assert config_kwargs["context_class"] is dict
            assert config_kwargs["cache_logger_on_first_use"] is True
            assert callable(config_kwargs["logger_factory"])
            assert callable(config_kwargs["wrapper_class"])


class TestGetLogger:
    """Test the get_logger function."""

    def test_get_logger_with_name(self):
        """Test get_logger with a specific name."""
        logger_name = "test.module"
        logger = get_logger(logger_name)

        # Check that it's a structlog logger (could be BoundLogger or
        # BoundLoggerLazyProxy)
        assert hasattr(logger, "_logger") or hasattr(logger, "logger_factory_args")
        # The logger should be bound to the specified name
        assert hasattr(logger, "info")

    def test_get_logger_without_name(self):
        """Test get_logger without a name (None)."""
        logger = get_logger(None)

        assert hasattr(logger, "_logger") or hasattr(logger, "logger_factory_args")
        assert hasattr(logger, "info")

    def test_get_logger_with_empty_string(self):
        """Test get_logger with an empty string name."""
        logger = get_logger("")

        assert hasattr(logger, "_logger") or hasattr(logger, "logger_factory_args")
        assert hasattr(logger, "info")

    def test_get_logger_returns_bound_logger(self):
        """Test that get_logger returns a properly bound logger."""
        logger = get_logger("test.logger")

        # Check that it has the expected methods of a BoundLogger
        assert hasattr(logger, "info")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
        assert hasattr(logger, "critical")
        assert hasattr(logger, "bind")


class TestLoggingIntegration:
    """Integration tests for the logging module."""

    def test_module_imports_successfully(self):
        """Test that the module imports without errors."""
        # This test ensures all imports work correctly
        import src.shared.utils.logging as logging_module

        assert hasattr(logging_module, "setup_logging")
        assert hasattr(logging_module, "get_logger")
        assert hasattr(logging_module, "add_service_info")
        assert hasattr(logging_module, "LOG_COLORS")
        assert hasattr(logging_module, "CONSOLE_FORMAT")

    def test_setup_logging_called_on_import(self):
        """Test that setup_logging is called when the module is imported."""
        # Since setup_logging() is called at module level, we can verify
        # that the root logger has been configured
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0

    @patch("src.shared.utils.logging.JSON_LOGS", False)
    def test_end_to_end_logging_non_json(self):
        """Test end-to-end logging functionality with non-JSON format."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            setup_logging()
            logger = get_logger("test.integration")

            test_message = "Integration test message"
            logger.info(test_message)

            # Check that something was written to stdout
            output = mock_stdout.getvalue()
            assert len(output) > 0

    @patch("src.shared.utils.logging.JSON_LOGS", True)
    def test_end_to_end_logging_json(self):
        """Test end-to-end logging functionality with JSON format."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            setup_logging()
            logger = get_logger("test.integration.json")

            test_message = "JSON integration test message"
            logger.info(test_message)

            # Check that something was written to stdout
            output = mock_stdout.getvalue()
            assert len(output) > 0

    def test_logger_with_context(self):
        """Test that logger works with context binding."""
        logger = get_logger("test.context")
        bound_logger = logger.bind(user_id="12345", request_id="abc-def")

        # Should not raise any exceptions
        assert hasattr(bound_logger, "info")
        assert hasattr(bound_logger, "bind")

    def test_different_log_levels(self):
        """Test that different log levels work correctly."""
        logger = get_logger("test.levels")

        # These should not raise exceptions
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")


class TestLoggingConfiguration:
    """Test logging configuration edge cases."""

    @patch("src.shared.utils.logging.LOG_LEVEL", "INVALID")
    def test_setup_logging_with_invalid_log_level(self):
        """Test setup_logging behavior with invalid log level."""
        # This should crash with ValueError for invalid log level
        with patch("sys.stdout", new_callable=StringIO):
            with pytest.raises(ValueError, match="Unknown level"):
                setup_logging()

    def test_add_service_info_with_partial_record(self):
        """Test add_service_info with a record missing some attributes."""
        # Create a mock record with only some attributes
        mock_record = Mock()
        mock_record.filename = "test_file.py"
        # Missing funcName and lineno

        event_dict: EventDict = {
            "_record": mock_record,
            "message": "test message",
        }

        logger = logging.getLogger("test")

        # This should handle missing attributes gracefully
        try:
            result = add_service_info(logger, "test_method", event_dict)
            # Check that it at least tried to access the attributes
            assert result["filename"] == "test_file.py"
        except AttributeError:
            # If it raises AttributeError for missing attributes, that's
            # expected
            pass

    def test_multiple_setup_logging_calls(self):
        """Test that calling setup_logging multiple times works correctly."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging()
            initial_handler_count = len(logging.getLogger().handlers)

            setup_logging()
            final_handler_count = len(logging.getLogger().handlers)

            # Should still have the same number of handlers (old ones cleared)
            assert final_handler_count == initial_handler_count


# Additional edge case tests
class TestLoggingEdgeCases:
    """Test edge cases and error conditions."""

    def test_get_logger_with_various_name_types(self):
        """Test get_logger with different types of names."""
        # Test with different valid name types
        names_to_test = [
            "simple",
            "module.submodule",
            "very.long.module.name.with.many.parts",
            "123numeric",
            "with-dashes",
            "with_underscores",
        ]

        for name in names_to_test:
            logger = get_logger(name)
            # Check that it's a valid logger object with expected methods
            assert hasattr(logger, "info")
            assert hasattr(logger, "debug")
            assert hasattr(logger, "warning")
            assert hasattr(logger, "error")
            assert hasattr(logger, "critical")

    def test_logging_with_exception_info(self):
        """Test that logging with exception info works."""
        logger = get_logger("test.exception")

        # Suppress the structlog warning about format_exc_info
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Remove `format_exc_info` from your processor chain",
                category=UserWarning,
                module="structlog._base",
            )

            try:
                raise ValueError("Test exception")
            except ValueError:
                # This should not crash
                logger.error("Error with exception", exc_info=True)

    @patch("structlog.configure")
    def test_setup_logging_structlog_configure_called(self, mock_configure):
        """Test that structlog.configure is called with correct parameters."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging()

        mock_configure.assert_called_once()
        call_kwargs = mock_configure.call_args[1]

        # Verify all expected configuration keys are present
        expected_keys = [
            "processors",
            "context_class",
            "logger_factory",
            "wrapper_class",
            "cache_logger_on_first_use",
        ]
        for key in expected_keys:
            assert key in call_kwargs

    def test_log_colors_completeness(self):
        """Test that LOG_COLORS contains colors for all standard log levels."""
        standard_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in standard_levels:
            assert level in LOG_COLORS
            color = LOG_COLORS[level]
            assert isinstance(color, str)
            assert len(color) > 0

    def test_console_format_contains_required_placeholders(self):
        """Test that CONSOLE_FORMAT contains all required format
        placeholders."""
        required_placeholders = [
            "%(log_color)s",
            "%(levelname)",
            "%(reset)s",
            "%(name)s",
            "%(message_log_color)s",
            "%(message)s",
        ]
        for placeholder in required_placeholders:
            assert placeholder in CONSOLE_FORMAT

    def test_add_service_info_preserves_existing_fields(self):
        """Test that add_service_info preserves all existing fields in
        event_dict."""
        mock_record = Mock()
        mock_record.filename = "test_file.py"
        mock_record.funcName = "test_function"
        mock_record.lineno = 42

        event_dict: EventDict = {
            "_record": mock_record,
            "message": "test message",
            "level": "INFO",
            "timestamp": "2024-01-01T00:00:00Z",
            "custom_field": "custom_value",
        }

        logger = logging.getLogger("test")
        result = add_service_info(logger, "test_method", event_dict)

        # All original fields should be preserved
        assert result["message"] == "test message"
        assert result["level"] == "INFO"
        assert result["timestamp"] == "2024-01-01T00:00:00Z"
        assert result["custom_field"] == "custom_value"
        assert result["_record"] == mock_record

        # New fields should be added
        assert result["filename"] == "test_file.py"
        assert result["funcName"] == "test_function"
        assert result["lineno"] == 42

    @patch("src.shared.utils.logging.JSON_LOGS", True)
    @patch("src.shared.utils.logging.LOG_LEVEL", "CRITICAL")
    def test_setup_logging_with_critical_level(self):
        """Test setup_logging with CRITICAL log level."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.CRITICAL

    @patch("src.shared.utils.logging.JSON_LOGS", False)
    @patch("src.shared.utils.logging.LOG_LEVEL", "ERROR")
    def test_setup_logging_with_error_level(self):
        """Test setup_logging with ERROR log level."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.ERROR

    def test_get_logger_function_signature(self):
        """Test that get_logger function has the correct signature."""
        import inspect

        sig = inspect.signature(get_logger)
        params = list(sig.parameters.keys())

        assert len(params) == 1
        assert params[0] == "name"
        assert sig.parameters["name"].default is None
        assert sig.parameters["name"].annotation == "str | None"

    def test_add_service_info_function_signature(self):
        """Test that add_service_info function has the correct signature."""
        import inspect

        sig = inspect.signature(add_service_info)
        params = list(sig.parameters.keys())

        assert len(params) == 3
        assert params[0] == "logger"
        assert params[1] == "method_name"
        assert params[2] == "event_dict"
