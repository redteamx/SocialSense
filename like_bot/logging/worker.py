"""
Worker module for processing log records in the LikeBot logging system.

This module provides a function to handle log records from a queue in a separate thread,
emitting them to configured handlers.
"""
import queue
import threading
import logging
import sys
from typing import List

from like_bot.logging.handlers import AsyncQueueHandler


def log_worker(
    log_queue: queue.Queue,
    handlers: List[logging.Handler],
    stop_event: threading.Event,
    timeout: float = 1.0,
    logger: logging.Logger = None,
) -> None:
    """
    Process log records from a queue and emit them using provided handlers.

    This function runs in a separate thread, continuously retrieving log records from the
    queue and passing them to each handler for emission. It stops when the stop event is set
    and the queue is empty.

    Args:
        log_queue (queue.Queue): Queue containing log records to process.
        handlers (List[logging.Handler]): List of handlers to emit log records to.
        stop_event (threading.Event): Event to signal when the worker should stop.
        timeout (float): Timeout in seconds for queue retrieval attempts. Defaults to 1.0.
        logger (logging.Logger, optional): Logger for error reporting. Defaults to module logger.

    Raises:
        ValueError: If timeout is non-positive or handlers list is empty.
    """
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    if not handlers:
        raise ValueError("At least one handler must be provided")

    # Use provided logger or default to module-level logger
    effective_logger = logger if logger is not None else logging.getLogger(__name__)

    while not stop_event.is_set() or not log_queue.empty():
        try:
            record = log_queue.get(block=True, timeout=timeout)
            try:
                for handler in handlers:
                    handler.emit(record)
            finally:
                log_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            effective_logger.error(
                f"Error processing log record: {str(e)}",
                exc_info=True,
            )
            print(f"Error in log_worker: {str(e)}", file=sys.stderr)
