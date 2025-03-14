import queue
import threading
import logging
import sys
from typing import List

def log_worker(log_queue: queue.Queue, handlers: List[logging.Handler], stop_event: threading.Event) -> None:
    """
    Processes log records from a queue and emits them using the provided logging handlers.

    This function continuously retrieves log records from `log_queue` and passes them to each
    handler in `handlers`. It continues running until the `stop_event` is set and the queue is empty.
    
    :param log_queue: A Queue containing logging records.
    :param handlers: A list of logging.Handler objects to emit the log records.
    :param stop_event: A threading.Event used to signal when the worker should stop processing.
    """
    while not stop_event.is_set() or not log_queue.empty():
        try:
            # Attempt to get a log record with a timeout.
            record = log_queue.get(block=True, timeout=1.0)
            for handler in handlers:
                handler.emit(record)
            log_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            logging.error(f"Error in log_worker: {str(e)}", exc_info=True)
            # Also print to stderr as a fallback.
            print(f"Error in log_worker: {str(e)}", file=sys.stderr)

