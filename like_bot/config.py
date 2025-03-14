from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import logging

# Timing and retry constants
MAX_RETRIES: int = 5
NETWORK_MAX_RETRIES: int = 10
MIN_DELAY: float = 5.0
MAX_DELAY: float = 60.0
FETCH_TIMEOUT: int = 120
REQUEST_DELAY: float = 5.0
INTRA_REQUEST_DELAY: float = 1.0
RATE_LIMIT_DELAY: float = 60.0

# Logging and file constants
LOG_FILE: str = "bot.log"
LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5MB
LOG_BACKUP_COUNT: int = 2
FOLLOWERS_FILE: str = "followers.txt"
SUMMARY_FILE: str = "summary_report.json"

# Concurrency and metrics constants
CONCURRENCY_LIMIT: int = 5
METRICS_UPDATE_INTERVAL: int = 300  # 5 minutes in seconds

# Batch Animation Constants
BATCH_START_ANIM: Tuple[str, ...] = (
    "🚀 Starting batch process...",
    "⏳ Processing...",
    "⚙️ Almost done..."
)
BATCH_END_ANIM: Tuple[str, ...] = (
    "✅ Batch process completed.",
)

# Themes for UI
THEMES: Dict[str, Dict[str, str]] = {
    "dark": {"primary": "cyan", "success": "green", "warning": "yellow", "error": "red", "bg": "black"},
    "light": {"primary": "blue", "success": "green", "warning": "orange", "error": "red", "bg": "white"},
    "holiday": {"primary": "red", "success": "green", "warning": "gold", "error": "purple", "bg": "black"},
    "cyberpunk": {"primary": "bright_cyan", "success": "bright_green", "warning": "bright_magenta", "error": "bright_red", "bg": "black"},
}

@dataclass
class Config:
    """
    Configuration settings for the LikeBot application.

    Attributes:
        db_config (Optional[Dict[str, str]]): Database configuration parameters (e.g., host, user).
        instagram_username (str): The Instagram username to use for operations.
        log_level (str): Logging level (e.g., "INFO", "DEBUG").
        request_timeout (int): Timeout for HTTP requests in seconds.
        request_delay (float): Delay between consecutive requests in seconds.
        intra_request_delay (float): Delay within a single request cycle in seconds.
        verbose (bool): If True, enables verbose output for debugging.
        theme (str): UI theme name corresponding to keys in THEMES.
        concurrency_limit (int): Maximum number of concurrent operations allowed.
    """
    db_config: Optional[Dict[str, str]] = field(default=None)
    instagram_username: str = "afadesign.official"
    log_level: str = "INFO"
    request_timeout: int = FETCH_TIMEOUT
    request_delay: float = REQUEST_DELAY
    intra_request_delay: float = INTRA_REQUEST_DELAY
    verbose: bool = False
    theme: str = "dark"
    concurrency_limit: int = CONCURRENCY_LIMIT

    def __post_init__(self) -> None:
        logger = logging.getLogger(__name__)
        if self.db_config is None:
            self.db_config = self._get_default_db_config()
        else:
            required_db_keys = {"database", "user", "password", "host", "port"}
            if not all(key in self.db_config for key in required_db_keys):
                missing_keys = required_db_keys - set(self.db_config.keys())
                logger.error("Missing required database configuration keys: %s", missing_keys)
                raise ValueError(f"Database configuration missing required keys: {missing_keys}")

        if self.request_timeout <= 0:
            logger.error("Request timeout must be positive, got %d", self.request_timeout)
            raise ValueError("Request timeout must be positive")
        if self.request_delay < 0:
            logger.error("Request delay must be non-negative, got %.2f", self.request_delay)
            raise ValueError("Request delay must be non-negative")
        if self.intra_request_delay < 0:
            logger.error("Intra-request delay must be non-negative, got %.2f", self.intra_request_delay)
            raise ValueError("Intra-request delay must be non-negative")

        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            logger.error("Invalid log level '%s', must be one of %s", self.log_level, valid_log_levels)
            raise ValueError(f"Invalid log level '{self.log_level}', must be one of {valid_log_levels}")

        if self.theme not in THEMES:
            logger.error("Invalid theme '%s', must be one of %s", self.theme, list(THEMES.keys()))
            raise ValueError(f"Invalid theme '{self.theme}', must be one of {list(THEMES.keys())}")

        if self.concurrency_limit <= 0:
            logger.error("Concurrency limit must be positive, got %d", self.concurrency_limit)
            raise ValueError("Concurrency limit must be positive")

    def _get_default_db_config(self) -> Dict[str, str]:
        """
        Provides default database configuration settings.

        Returns:
            Dict[str, str]: Default database configuration with SSL settings.
        """
        return {
            "database": "instagram_bot",
            "user": "rtx",
            "password": "pixel4Loud3total",
            "host": "localhost",
            "port": "5432",
            "ssl": True,          # Boolean True to enable SSL
            "ssl_verify": False,  # Boolean False to disable verification for localhost
        }
