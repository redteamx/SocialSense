import asyncio
import random
import socket
from functools import wraps
from typing import Any, Callable, List

import instaloader
import aiohttp
from aiograpi.exceptions import ClientError

from like_bot.config import MAX_DELAY, MIN_DELAY, NETWORK_MAX_RETRIES, RATE_LIMIT_DELAY


def async_retrying(
    max_retries: int,
    initial_delay: float,
    rate_limit_codes: List[int] = [401, 429],
    uses_session: bool = False
) -> Callable:
    """
    A decorator factory for retrying asynchronous functions upon encountering specific errors.

    This decorator wraps an async function and retries it up to `max_retries` times with an
    exponentially increasing delay. It is designed to handle common network, timeout, and API
    errors, including rate-limit responses, and logs each retry attempt with a descriptive message.

    :param max_retries: Maximum number of retry attempts.
    :param initial_delay: The initial delay between attempts (in seconds).
    :param rate_limit_codes: List of HTTP status codes that indicate rate limiting.
    :param uses_session: Optional flag for session-based operations (currently unused).
    :return: A decorator that can be applied to an async function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs) -> Any:
            func_name = func.__name__
            for attempt in range(max_retries):
                try:
                    return await func(self, *args, **kwargs)
                except (
                    instaloader.exceptions.ConnectionException,
                    ClientError,
                    aiohttp.ClientError,
                    socket.gaierror,
                    asyncio.TimeoutError
                ) as e:
                    # Attempt to extract a status code from the exception.
                    status_code = getattr(e, 'status', 0)
                    response = getattr(e, 'response', None)
                    if not status_code and response:
                        status_code = getattr(response, 'status', 0)
                    # Special handling for instaloader ConnectionException.
                    if not status_code and isinstance(e, instaloader.exceptions.ConnectionException):
                        parts = str(e).split()
                        status_code = int(parts[0]) if parts and parts[0].isdigit() else 0

                    is_rate_limit = status_code in rate_limit_codes
                    # Determine error type for logging.
                    if isinstance(e, instaloader.exceptions.ConnectionException):
                        error_type = "Network"
                    elif isinstance(e, ClientError):
                        error_type = "Aiograpi"
                    elif isinstance(e, socket.gaierror):
                        error_type = "DNS"
                    elif isinstance(e, aiohttp.ClientError):
                        error_type = "HTTP"
                    else:
                        error_type = "Timeout"

                    # Calculate delay with exponential backoff and random jitter.
                    delay = ((RATE_LIMIT_DELAY if is_rate_limit else initial_delay) * (2 ** attempt)) + random.uniform(0, 1)
                    delay = min(delay, MAX_DELAY)

                    await self.async_logger.warning(
                        f"{error_type} error occurred",
                        extra={
                            "function": func_name,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "error": str(e),
                            "delay": delay,
                            "is_rate_limit": is_rate_limit,
                            "phase": "Processing"
                        }
                    )
                    await asyncio.sleep(delay)
                    # If final attempt, re-raise the exception.
                    if attempt == max_retries - 1:
                        raise
            raise Exception(f"Operation failed after {max_retries} attempts in {func_name}")
        return wrapper
    return decorator

