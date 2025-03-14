# like_bot/instagram_session.py
import ssl
import os
import random
import asyncio
import logging
from typing import Optional, Type, Any, List

import aiohttp
import instaloader
from rich.console import Console
from rich.panel import Panel
from rich.box import HEAVY
from dotenv import load_dotenv
from aiograpi import Client as AiograpiClient

# Load environment variables from .env file
load_dotenv()

from like_bot.config import Config, THEMES
from like_bot.logging import AsyncLogger

console = Console()

class InstagramSession:
    """
    Manages an Instagram session using Instaloader and aiograpi for API interactions.

    Handles login with Instaloader, either by reusing a saved session or performing an interactive login
    with optional two-factor authentication (2FA). After successful login, it initializes an aiograpi client.
    The session is managed as an async context manager to ensure proper resource cleanup.
    """

    _USER_AGENTS: List[str] = [
        "Instagram 219.0.0.12.117",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
    ]

    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        """
        Initialize the InstagramSession with configuration and logging.

        Args:
            config (Config): Configuration settings for the session.
            async_logger (AsyncLogger): Asynchronous logger for session events.
        """
        self._config: Config = config
        self._async_logger: AsyncLogger = async_logger
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._session_file: str = f"{self._config.instagram_username}.session"
        self.loader: Optional[instaloader.Instaloader] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.ig_client: Optional[AiograpiClient] = None

    @property
    def config(self) -> Config:
        """Get the configuration object."""
        return self._config

    @property
    def async_logger(self) -> AsyncLogger:
        """Get the asynchronous logger."""
        return self._async_logger

    async def __aenter__(self) -> "InstagramSession":
        """
        Enter the async context manager, initializing the session with a custom SSL context.

        Returns:
            InstagramSession: The initialized session instance.

        Raises:
            RuntimeError: If session initialization fails.
        """
        try:
            await self._async_logger.info(
                "Initializing Instagram session with custom SSL context",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            self.loader = instaloader.Instaloader(request_timeout=120)
            self.loader.context.user_agent = random.choice(self._USER_AGENTS)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                connector=aiohttp.TCPConnector(ssl=ssl_context)
            )
            await asyncio.sleep(2)
            await self._async_logger.info(
                "Instagram session initialized",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            return self
        except Exception as e:
            await self._async_logger.error(
                f"Failed to initialize Instagram session: {str(e)}",
                extra={"function": "__aenter__", "phase": "Setup"},
                exc_info=True
            )
            raise RuntimeError(f"Failed to initialize Instagram session: {str(e)}") from e

    async def __aexit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                       exc_tb: Optional[Any]) -> None:
        """
        Exit the async context manager, ensuring proper cleanup of resources.

        Args:
            exc_type (Optional[Type[BaseException]]): Type of the exception, if any.
            exc_val (Optional[BaseException]): Exception instance, if any.
            exc_tb (Optional[Any]): Traceback, if any.
        """
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                await self._async_logger.debug(
                    "aiohttp ClientSession closed",
                    extra={"function": "__aexit__", "phase": "Cleanup"}
                )
            if self.loader and getattr(self.loader.context, "_session", None):
                self.loader.context._session.close()
                await self._async_logger.debug(
                    "Instaloader requests session closed",
                    extra={"function": "__aexit__", "phase": "Cleanup"}
                )
            await self._async_logger.info(
                "Instagram session closed",
                extra={"function": "__aexit__", "phase": "Cleanup"}
            )
        except Exception as e:
            await self._async_logger.error(
                f"Error during Instagram session cleanup: {str(e)}",
                extra={"function": "__aexit__", "phase": "Cleanup"},
                exc_info=True
            )
        finally:
            self.session = None
            self.loader = None
            self.ig_client = None

    async def save_session(self, filepath: Optional[str] = None) -> None:
        """
        Save the current Instaloader session to a file.

        Args:
            filepath (Optional[str]): Path to the session file. Defaults to the default session file.

        Raises:
            RuntimeError: If the session cannot be saved or no Instaloader instance is available.
        """
        if not self.loader:
            raise RuntimeError("No Instaloader instance available to save session")
        
        filepath = filepath or self._session_file
        try:
            self.loader.save_session_to_file(filename=filepath)
            self._logger.info(
                "Session saved",
                extra={"function": "save_session", "file": filepath, "phase": "Session"}
            )
        except Exception as e:
            self._logger.error(
                f"Failed to save session to {filepath}: {str(e)}",
                extra={"function": "save_session", "file": filepath, "phase": "Session"}
            )
            raise RuntimeError(f"Failed to save session to {filepath}: {str(e)}") from e

    async def load_session(self, filepath: Optional[str] = None) -> bool:
        """
        Load an Instaloader session from a file.

        Args:
            filepath (Optional[str]): Path to the session file. Defaults to the default session file.

        Returns:
            bool: True if the session was loaded successfully, False otherwise.
        """
        if not self.loader:
            return False
        
        filepath = filepath or self._session_file
        try:
            self.loader.load_session_from_file(self._config.instagram_username, filename=filepath)
            self._logger.info(
                "Session loaded",
                extra={"function": "load_session", "file": filepath, "phase": "Session"}
            )
            return True
        except FileNotFoundError:
            self._logger.info(
                f"No session file found at {filepath}",
                extra={"function": "load_session", "file": filepath, "phase": "Session"}
            )
            return False
        except Exception as e:
            self._logger.warning(
                f"Failed to load session from {filepath}: {str(e)}. Proceeding with login.",
                extra={"function": "load_session", "file": filepath, "phase": "Session"}
            )
            return False

    async def login(self, password: str) -> None:
        """
        Log in to Instagram using Instaloader, reusing a session if available or performing an interactive login.

        Args:
            password (str): The password for the Instagram account (not used in interactive login).

        Raises:
            instaloader.exceptions.LoginException: If login fails due to generic login issues.
            instaloader.exceptions.BadCredentialsException: If credentials are invalid.
            instaloader.exceptions.ConnectionException: If a network error occurs during login.
        """
        username = os.getenv("INSTAGRAM_USERNAME", self._config.instagram_username)
        self._config.instagram_username = username
        self._session_file = f"{username}.session"

        if os.path.exists(self._session_file):
            if await self.load_session(self._session_file):
                self._display_panel("✅ Instaloader session loaded successfully!", "Success", "success")
                self._logger.info(
                    "Instaloader session loaded successfully",
                    extra={"function": "login", "username": username, "phase": "Login"}
                )
                await self._initialize_ig_client()
                return
            else:
                self._logger.warning(
                    "Session file exists but failed to load. Proceeding with interactive login.",
                    extra={"function": "login", "phase": "Login"}
                )

        try:
            self.loader.interactive_login(username)
            await self.save_session()
            self._display_panel("✅ Instaloader login successful!", "Success", "success")
            self._logger.info(
                "Instaloader logged in successfully",
                extra={"function": "login", "username": username, "phase": "Login"}
            )
            await self._initialize_ig_client()
        except instaloader.exceptions.LoginException as e:
            if "two-factor" in str(e).lower():
                await self._handle_two_factor_auth(password)
            else:
                self._display_panel(f"✘ Instaloader login failed: {str(e)}", "Error", "error")
                self._logger.error(
                    "Instaloader login failed",
                    extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
                )
                raise
        except instaloader.exceptions.BadCredentialsException as e:
            self._display_panel("✘ Instaloader login failed: Bad credentials", "Error", "error")
            self._logger.error(
                "Instaloader login failed due to bad credentials",
                extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
            )
            raise
        except instaloader.exceptions.ConnectionException as e:
            self._display_panel(f"✘ Network error: {str(e)}", "Error", "error")
            self._logger.error(
                "Network error during login",
                extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
            )
            raise

    async def _handle_two_factor_auth(self, password: str) -> None:
        """
        Handle two-factor authentication for Instaloader login.

        Args:
            password (str): The password (not used directly, kept for compatibility).

        Raises:
            instaloader.exceptions.LoginException: If 2FA verification fails.
        """
        try:
            code = input("Enter 2FA code for Instaloader: ")
            console.print("🔐 Processing 2FA code for Instaloader...", style="yellow")
            with console.status("Verifying 2FA...", spinner="dots"):
                self.loader.two_factor_login(code)
            self._display_panel("✅ Instaloader logged in with 2FA!", "Success", "success")
            self._logger.info(
                "Instaloader logged in with 2FA",
                extra={"function": "login", "username": self._config.instagram_username, "phase": "Login"}
            )
            await self.save_session()
            await self._initialize_ig_client()
        except instaloader.exceptions.LoginException as e:
            self._logger.error(
                f"Failed to verify 2FA: {str(e)}",
                extra={"function": "_handle_two_factor_auth", "phase": "Login"}
            )
            raise
        except Exception as e:
            self._logger.error(
                f"Unexpected error during 2FA: {str(e)}",
                extra={"function": "_handle_two_factor_auth", "phase": "Login"}
            )
            raise instaloader.exceptions.LoginException(f"Unexpected error during 2FA: {str(e)}") from e

    async def _initialize_ig_client(self) -> None:
        """Initialize the aiograpi client after successful login."""
        try:
            self.ig_client = AiograpiClient()
            await self._async_logger.debug(
                "Aiograpi client initialized",
                extra={"function": "_initialize_ig_client", "phase": "Login"}
            )
        except Exception as e:
            await self._async_logger.error(
                f"Failed to initialize aiograpi client: {str(e)}",
                extra={"function": "_initialize_ig_client", "phase": "Login"}
            )
            raise RuntimeError(f"Failed to initialize aiograpi client: {str(e)}") from e

    def _display_panel(self, message: str, title: str, theme_key: str) -> None:
        """
        Display a styled panel message in the console.

        Args:
            message (str): The message to display.
            title (str): The title of the panel.
            theme_key (str): The key for the theme style in THEMES.
        """
        panel = Panel(
            message,
            title=title,
            border_style=THEMES[self._config.theme][theme_key],
            box=HEAVY
        )
        console.print(panel)
