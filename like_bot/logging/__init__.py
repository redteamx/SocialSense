import sys
import logging
import queue
import threading
from collections import deque
from typing import Tuple, Optional, Dict, Any
from logging.handlers import RotatingFileHandler
from datetime import datetime  # Added to resolve NameError in AsyncLogger

from like_bot.config import LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, THEMES
from .handlers import AsyncQueueHandler, RichHandler, JSONFormatter
from .worker import log_worker

def setup_logging(log_level: str, verbose: bool, theme: str, log_buffer: deque) -> Tuple[logging.Logger, 'AsyncLogger', threading.Event]:
    logger = logging.getLogger(__name__)
    level = logging.DEBUG if verbose else getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    log_queue = queue.Queue(maxsize=1000)
    stop_event = threading.Event()

    rich_handler = RichHandler(log_buffer)
    rich_handler.setFormatter(logging.Formatter("%(asctime)s - [%(phase)s] - %(message)s"))

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    file_handler.setFormatter(JSONFormatter())

    queue_handler = AsyncQueueHandler(log_queue)
    logger.handlers = [queue_handler]

    handlers = [rich_handler, file_handler]
    log_thread = threading.Thread(target=log_worker, args=(log_queue, handlers, stop_event), daemon=True)
    log_thread.start()

    async_logger = AsyncLogger(logger, log_queue)
    return logger, async_logger, stop_event

class AsyncLogger:
    def __init__(self, logger: logging.Logger, queue: queue.Queue):
        self.logger = logger
        self.queue = queue

    async def log(self, level: int, msg: str, *args, extra: Optional[Dict[str, Any]] = None, exc_info: bool = False) -> None:
        extra = extra or {}
        if "timestamp" not in extra:
            extra["timestamp"] = datetime.utcnow().isoformat()
        record = self.logger.makeRecord(
            self.logger.name, level, __file__, 0, msg, args, None, extra=extra
        )
        if exc_info:
            record.exc_info = sys.exc_info()
            formatter = logging.Formatter()
            record.exc_text = formatter.formatException(record.exc_info)
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            print(f"Log queue full, dropping message: {msg}", file=sys.stderr)

    async def debug(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None) -> None:
        await self.log(logging.DEBUG, msg, *args, extra=extra)

    async def info(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None) -> None:
        await self.log(logging.INFO, msg, *args, extra=extra)

    async def warning(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None) -> None:
        await self.log(logging.WARNING, msg, *args, extra=extra)

    async def error(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, exc_info: bool = False) -> None:
        await self.log(logging.ERROR, msg, *args, extra=extra, exc_info=exc_info)

