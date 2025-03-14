import logging
import sys
import time
import json
import queue
from datetime import datetime
from typing import Deque, Tuple, Optional
from collections import deque

from logging.handlers import RotatingFileHandler
from rich.text import Text

from like_bot.config import THEMES


class AsyncQueueHandler(logging.Handler):
    """
    A logging handler that enqueues log records into a provided thread-safe queue.

    This handler is designed for asynchronous logging by placing log records into a queue
    for processing by another thread or process.

    Attributes:
        _queue (queue.Queue): The thread-safe queue where log records are enqueued.
    """
    def __init__(self, queue_: queue.Queue, level: int = logging.NOTSET) -> None:
        """
        Initialize the AsyncQueueHandler with a queue and optional log level.

        Args:
            queue_ (queue.Queue): A thread-safe queue for storing log records.
            level (int): The logging level for this handler. Defaults to NOTSET.
        """
        super().__init__(level=level)
        self._queue: queue.Queue = queue_

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by placing it into the queue.

        If the queue is full, logs an error to sys.stderr and drops the record.

        Args:
            record (logging.LogRecord): The log record to emit.
        """
        try:
            self._queue.put_nowait(record)
        except queue.Full as e:
            # Use the logger's handleError to report queue full errors consistently
            self.handleError(record)
            print(
                f"Log queue full, dropping record: {record.getMessage()} "
                f"(exception: {str(e)})",
                file=sys.stderr,
            )


class RichHandler(logging.Handler):
    """
    A logging handler that formats log records into rich Text objects and stores them in a buffer.

    This handler is designed for rich console output, maintaining a fixed-size buffer of formatted
    log entries with timestamps.

    Attributes:
        _log_buffer (Deque[Tuple[Text, float]]): Buffer storing formatted log entries and timestamps.
        _max_buffer_size (int): Maximum number of entries in the log buffer.
        _theme_name (str): Name of the theme to use for styling log entries.
    """
    def __init__(
        self,
        log_buffer: Deque[Tuple[Text, float]] = None,
        max_buffer_size: int = 50,
        theme_name: str = "dark",
        level: int = logging.NOTSET,
    ) -> None:
        """
        Initialize the RichHandler with a buffer, maximum size, theme, and log level.

        Args:
            log_buffer (Deque[Tuple[Text, float]], optional): A buffer to store log entries with timestamps.
                If None, a new deque is created.
            max_buffer_size (int): Maximum number of entries in the log buffer. Defaults to 50.
            theme_name (str): Name of the theme from THEMES to use for styling. Defaults to "dark".
            level (int): The logging level for this handler. Defaults to NOTSET.

        Raises:
            ValueError: If max_buffer_size is less than 1 or if theme_name is not in THEMES.
        """
        super().__init__(level=level)
        if max_buffer_size < 1:
            raise ValueError("max_buffer_size must be at least 1")
        if theme_name not in THEMES:
            raise ValueError(f"Invalid theme_name '{theme_name}', must be one of {list(THEMES.keys())}")

        self._log_buffer: Deque[Tuple[Text, float]] = log_buffer if log_buffer is not None else deque()
        self._max_buffer_size: int = max_buffer_size
        self._theme_name: str = theme_name

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by formatting it as a rich Text object and appending to the log buffer.

        The buffer is capped at max_buffer_size, removing oldest entries as needed.

        Args:
            record (logging.LogRecord): The log record to emit.
        """
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            # Use the specified theme for coloring
            theme = THEMES[self._theme_name]
            color = theme.get(level, theme.get("default", "white"))
            style = f"{color} dim" if level == "debug" else color
            entry = Text(f"{level.upper()} {msg}", style=style)
            self._log_buffer.append((entry, time.time()))

            # Maintain buffer size
            while len(self._log_buffer) > self._max_buffer_size:
                self._log_buffer.popleft()
        except Exception as e:
            self.handleError(record)
            print(f"Error in RichHandler.emit: {str(e)}", file=sys.stderr)


class JSONFormatter(logging.Formatter):
    """
    A logging formatter that outputs log records in JSON format.

    This formatter serializes log records into JSON strings, including standard fields and
    optional custom attributes.
    """
    def __init__(
        self,
        datefmt: Optional[str] = None,
        extra_fields: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize the JSONFormatter with optional date format and extra fields.

        Args:
            datefmt (str, optional): Custom date format for timestamps. If None, ISO format is used.
            extra_fields (list[str], optional): Additional log record attributes to include in JSON output.
        """
        super().__init__(datefmt=datefmt)
        self._extra_fields: list[str] = extra_fields if extra_fields is not None else []

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as a JSON string.

        Includes standard fields (timestamp, level, message, etc.) and optional extra fields.
        Falls back to default formatting if JSON serialization fails.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: A JSON string representation of the log record.
        """
        try:
            log_entry = {
                "timestamp": self._get_timestamp(record),
                "level": record.levelname,
                "message": record.getMessage(),
                "function": record.funcName,
                "pathname": record.pathname,
                "lineno": record.lineno,
            }

            # Include optional fields if they exist in the record
            for field in self._extra_fields:
                log_entry[field] = getattr(record, field, "N/A")

            # Include exception information if present
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)

            return json.dumps(log_entry, default=str)
        except Exception as e:
            self.handleError(record)
            print(f"Error in JSONFormatter.format: {str(e)}", file=sys.stderr)
            # Fallback to default formatting
            return super().format(record)

    def _get_timestamp(self, record: logging.LogRecord) -> str:
        """
        Get the timestamp for the log record in the appropriate format.

        Args:
            record (logging.LogRecord): The log record containing timestamp information.

        Returns:
            str: The formatted timestamp string.
        """
        if self.datefmt:
            return datetime.fromtimestamp(record.created).strftime(self.datefmt)
        return datetime.utcfromtimestamp(record.created).isoformat()
