import logging
import sys
import time
import json
import queue
from datetime import datetime
from collections import deque
from typing import Deque, Tuple

from logging.handlers import RotatingFileHandler
from rich.text import Text

from like_bot.config import THEMES


class AsyncQueueHandler(logging.Handler):
    """
    A logging handler that enqueues log records into a provided queue.

    Attributes:
        queue (queue.Queue): The thread-safe queue to which log records are enqueued.
    """
    def __init__(self, queue_: queue.Queue) -> None:
        """
        Initialize the AsyncQueueHandler.

        Args:
            queue_ (queue.Queue): A thread-safe queue for log records.
        """
        super().__init__()
        self.queue = queue_

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by placing it into the queue.

        If the queue is full, an error message is logged to sys.stderr.
        """
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            print(f"Log queue full, dropping record: {record.msg}", file=sys.stderr)


class RichHandler(logging.Handler):
    """
    A logging handler that formats log records into rich Text objects and appends them to a log buffer.

    Attributes:
        log_buffer (deque[Tuple[Text, float]]): Buffer to store formatted log entries along with timestamps.
    """
    def __init__(self, log_buffer: deque[Tuple[Text, float]]) -> None:
        """
        Initialize the RichHandler.

        Args:
            log_buffer (deque[Tuple[Text, float]]): A buffer to store log entries with their timestamps.
        """
        super().__init__()
        self.log_buffer = log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by formatting it into a rich Text object and appending it to the log buffer.

        The log buffer maintains a fixed size of 50 entries.
        """
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            # Retrieve color from the "dark" theme; adjust as needed for different themes.
            color = THEMES["dark"].get(level, "white")
            style = color if level != "debug" else f"{color} dim"
            entry = Text(f"{level.upper()} {msg}", style=style)
            self.log_buffer.append((entry, time.time()))
            if len(self.log_buffer) > 50:
                self.log_buffer.popleft()
        except Exception:
            self.handleError(record)


class JSONFormatter(logging.Formatter):
    """
    A logging formatter that outputs log records in JSON format.
    """
    def format(self, record: logging.LogRecord) -> str:
        """
        Format the log record as a JSON string.

        Returns:
            str: A JSON string representation of the log record.
        """
        try:
            log_entry = {
                "timestamp": getattr(record, "timestamp", datetime.utcnow().isoformat()),
                "level": record.levelname,
                "message": record.msg,
                "function": getattr(record, "function", record.funcName),
                "username": getattr(record, "username", "N/A"),
                "phase": getattr(record, "phase", "Unknown"),
                "pathname": record.pathname,
                "lineno": record.lineno,
            }
            if getattr(record, "exc_info", None):
                log_entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_entry)
        except Exception:
            self.handleError(record)
            # Fallback to default formatting if JSON conversion fails.
            return super().format(record)

