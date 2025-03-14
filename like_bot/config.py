from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

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

# Batch Animation Constants (converted to tuples to support indexing)
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
        db_config (Dict[str, str]): Database configuration parameters.
        instagram_username (str): The Instagram username to use.
        log_level (str): Logging level.
        request_timeout (int): Timeout for requests in seconds.
        request_delay (float): Delay between requests.
        intra_request_delay (float): Delay within a request cycle.
        verbose (bool): Verbosity flag.
        theme (str): UI theme to use, corresponding to THEMES.
        concurrency_limit (int): Maximum number of concurrent operations.
    """
    db_config: Optional[Dict[str, str]] = None
    instagram_username: str = "afadesign.official"
    log_level: str = "INFO"
    request_timeout: int = FETCH_TIMEOUT
    request_delay: float = REQUEST_DELAY
    intra_request_delay: float = INTRA_REQUEST_DELAY
    verbose: bool = False
    theme: str = "dark"
    concurrency_limit: int = CONCURRENCY_LIMIT

    def __post_init__(self) -> None:
        """
        Ensures that db_config has default values if not provided or explicitly set to None.
        """
        if self.db_config is None:
            self.db_config = {
                "database": "instagram_bot",
                "user": "rtx",
                "password": "pixel4Loud3total",
                "host": "localhost",
                "port": "5432"
            }

