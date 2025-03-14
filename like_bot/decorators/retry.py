import asyncio
import random
import socket
from functools import wraps
from typing import Any, Callable, List, Optional, Type, Tuple
import logging

import instaloader
import aiohttp
from aiograpi.exceptions import ClientError

from like_bot.config import MAX_DELAY, MIN_DELAY, NETWORK_MAX_RETRIES, RATE_LIMIT_DELAY

class RetryHandler:
    """
    Handles retry logic for asynchronous operations with exponential backoff.
    """
    def __init__(
        self,
        max_retries: int = NETWORK_MAX_RETRIES,
        initial_delay: float = MIN_DELAY,
        rate_limit_codes: Optional[List[int]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if initial_delay <= 0:
            raise ValueError("initial_delay must be positive")

        self._max_retries = max_retries
        self._initial_delay = initial_delay
        self._rate_limit_codes = rate_limit_codes if rate_limit_codes is not None else [401, 429]
        self._logger = logger if logger else logging.getLogger(__name__)

    def retry(self, func: Callable) -> Callable:
        """
        Decorator to apply retry logic to an async function.

        Args:
            func (Callable): The async function to decorate.

        Returns:
            Callable: The wrapped async function with retry logic.
        """
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            return await self._execute_with_retries(func, *args, **kwargs)
        return wrapper

    async def _execute_with_retries(self, func: Callable, *args, **kwargs) -> Any:
        func_name = func.__name__
        exceptions_to_retry: Tuple[Type[Exception], ...] = (
            instaloader.exceptions.ConnectionException,
            ClientError,
            aiohttp.ClientError,
            socket.gaierror,
            asyncio.TimeoutError,
        )

        for attempt in range(self._max_retries):
            try:
                return await func(*args, **kwargs)
            except exceptions_to_retry as e:
                status_code = self._extract_status_code(e)
                is_rate_limit = status_code in self._rate_limit_codes
                error_type = self._determine_error_type(e)

                if attempt == self._max_retries - 1:
                    self._log_retry(attempt, func_name, error_type, e, 0, is_rate_limit, is_final=True)
                    raise

                delay = self._calculate_delay(attempt, is_rate_limit)
                self._log_retry(attempt, func_name, error_type, e, delay, is_rate_limit)
                await asyncio.sleep(delay)

        raise Exception(f"Operation failed after {self._max_retries} attempts in {func_name}")

    def _extract_status_code(self, exception: Exception) -> int:
        status_code = getattr(exception, "status", 0)
        if not status_code:
            response = getattr(exception, "response", None)
            status_code = getattr(response, "status", 0) if response else 0
        if not status_code and isinstance(exception, instaloader.exceptions.ConnectionException):
            parts = str(exception).split()
            status_code = int(parts[0]) if parts and parts[0].isdigit() else 0
        return status_code

    def _determine_error_type(self, exception: Exception) -> str:
        if isinstance(exception, instaloader.exceptions.ConnectionException):
            return "Network"
        elif isinstance(exception, ClientError):
            return "Aiograpi"
        elif isinstance(exception, socket.gaierror):
            return "DNS"
        elif isinstance(exception, aiohttp.ClientError):
            return "HTTP"
        return "Timeout"

    def _calculate_delay(self, attempt: int, is_rate_limit: bool) -> float:
        base_delay = RATE_LIMIT_DELAY if is_rate_limit else self._initial_delay
        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
        return min(delay, MAX_DELAY)

    def _log_retry(
        self,
        attempt: int,
        func_name: str,
        error_type: str,
        error: Exception,
        delay: float,
        is_rate_limit: bool,
        is_final: bool = False,
    ) -> None:
        message = f"{error_type} error occurred{' - final attempt failed' if is_final else ''}"
        self._logger.warning(
            message,
            extra={
                "function": func_name,
                "attempt": attempt + 1,
                "max_retries": self._max_retries,
                "error": str(error),
                "delay": delay if not is_final else None,
                "is_rate_limit": is_rate_limit,
                "phase": "Processing",
            },
        )
