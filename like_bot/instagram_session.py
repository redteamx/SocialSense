import ssl
import os
import json
import random
import asyncio
import logging
from typing import Any, Optional

import aiohttp
import instaloader
from rich.console import Console
from rich.panel import Panel
from rich.box import HEAVY
from dotenv import load_dotenv

# Load environment variables from .env file.
load_dotenv()

from like_bot.config import Config, THEMES
from like_bot.logging import AsyncLogger

# Import the AIograpi Client following best practices.
from aiograpi import Client as AiograpiClient

console = Console()


class InstagramSession:
    """
    Manages an Instagram session using Instaloader.

    This class handles login using Instaloader. It first attempts to load an existing session
    from a file (named "<username>.session"). If the session does not exist or fails to load,
    it performs an interactive login which prompts for credentials and handles two-factor authentication.
    After a successful login, the session is saved for future use and an aiograpi client is instantiated.
    This approach aligns with best practices from Instaloader and aiograpi.
    """

    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        """
        Initializes the InstagramSession with configuration and an async logger.

        :param config: Configuration settings.
        :param async_logger: An asynchronous logger for session events.
        """
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
        # Use a stable set of user agents.
        self.user_agents = [
            "Instagram 219.0.0.12.117",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
        ]
        self.loader: Optional[instaloader.Instaloader] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._logger = logging.getLogger(__name__)
        # Use a session file named as "<username>.session"
        self.session_file: str = f"{self.config.instagram_username}.session"
        # Placeholder for the aiograpi client.
        self.ig_client: Optional[AiograpiClient] = None

    async def __aenter__(self) -> "InstagramSession":
        """
        Initializes the Instagram session with a custom SSL context.

        :return: An instance of InstagramSession with initialized resources.
        :raises: Exceptions on failure.
        """
        try:
            await self.async_logger.info(
                "Initializing Instagram session with custom SSL context",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            self.loader = instaloader.Instaloader(request_timeout=120)
            # Set a stable user agent.
            self.loader.context.user_agent = random.choice(self.user_agents)

            # Configure SSL context.
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED

            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                connector=aiohttp.TCPConnector(ssl=ssl_context)
            )

            # Brief delay to ensure resources are ready.
            await asyncio.sleep(2)

            await self.async_logger.info(
                "Instagram session initialized",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            return self
        except ssl.SSLError as e:
            await self.async_logger.error(
                "SSL error during session initialization",
                extra={"function": "__aenter__", "error": str(e), "phase": "Setup"},
                exc_info=True
            )
            raise
        except Exception as e:
            await self.async_logger.error(
                "Failed to initialize Instagram session",
                extra={"function": "__aenter__", "error": str(e), "phase": "Setup"},
                exc_info=True
            )
            raise

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Cleans up the Instagram session resources.
        """
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                await self.async_logger.debug(
                    "aiohttp ClientSession closed",
                    extra={"function": "__aexit__", "phase": "Cleanup"}
                )
            if self.loader and getattr(self.loader.context, "_session", None):
                self.loader.context._session.close()
                await self.async_logger.debug(
                    "Instaloader requests session closed",
                    extra={"function": "__aexit__", "phase": "Cleanup"}
                )
            await self.async_logger.info(
                "Instagram session closed",
                extra={"function": "__aexit__", "phase": "Cleanup"}
            )
        except Exception as e:
            await self.async_logger.error(
                "Error during Instagram session cleanup",
                extra={"function": "__aexit__", "error": str(e), "phase": "Cleanup"},
                exc_info=True
            )
        finally:
            self.session = None
            self.loader = None
            self.ig_client = None

    async def save_session(self, filepath: str = None) -> None:
        """
        Saves the current Instaloader session to a file.

        :param filepath: Destination file path for the session data. If None, uses self.session_file.
        """
        if self.loader:
            if filepath is None:
                filepath = self.session_file
            # Save session using only the username.
            self.loader.save_session_to_file(self.config.instagram_username)
            self._logger.info(
                "Session saved",
                extra={"function": "save_session", "file": filepath, "phase": "Session"}
            )

    async def load_session(self, filepath: str = None) -> bool:
        """
        Loads an Instaloader session from a file.

        :param filepath: The file from which to load the session. If None, uses self.session_file.
        :return: True if the session is loaded successfully, False otherwise.
        """
        if filepath is None:
            filepath = self.session_file
        try:
            self.loader.load_session_from_file(self.config.instagram_username, filepath)
            self._logger.info(
                "Session loaded",
                extra={"function": "load_session", "file": filepath, "phase": "Session"}
            )
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            self._logger.warning(
                f"Failed to load session: {e}. Proceeding with login.",
                extra={"function": "load_session", "file": filepath, "phase": "Session"}
            )
            return False

    async def login(self, password: str) -> None:
        """
        Logs in to Instagram using Instaloader. If a session file exists, attempts to load it;
        otherwise, performs an interactive login which prompts for credentials and handles two-factor authentication,
        and then saves the session. After a successful login, the aiograpi client is instantiated.
        The credentials are read from the environment variables.

        :param password: Instagram account password (not used in interactive login).
        :raises: Exceptions from Instaloader on login failure.
        """
        logger = self._logger
        # Override username from environment if available.
        username = os.getenv("INSTAGRAM_USERNAME", self.config.instagram_username)
        self.config.instagram_username = username  # Update config if needed.
        self.session_file = f"{username}.session"  # Update session file name accordingly.

        # Check if a session file exists and attempt to load it.
        if os.path.exists(self.session_file):
            if await self.load_session(self.session_file):
                self._display_panel("✅ Instaloader session loaded successfully!", "Success", "success")
                logger.info(
                    "Instaloader session loaded successfully",
                    extra={"function": "login", "username": username, "phase": "Login"}
                )
                # Instantiate the aiograpi client.
                self.ig_client = AiograpiClient()
                return
            else:
                logger.warning(
                    "Session file exists but failed to load. Proceeding with interactive login.",
                    extra={"function": "login", "phase": "Login"}
                )
        # Use interactive login to prompt for credentials and handle 2FA.
        try:
            self.loader.interactive_login(username)
            self.loader.save_session_to_file(username)
            self._display_panel("✅ Instaloader login successful!", "Success", "success")
            logger.info(
                "Instaloader logged in successfully",
                extra={"function": "login", "username": username, "phase": "Login"}
            )
            # Instantiate the aiograpi client after login.
            self.ig_client = AiograpiClient()
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            await self._handle_two_factor_auth(password)
        except instaloader.exceptions.BadCredentialsException as e:
            self._display_panel("✘ Instaloader login failed: Bad credentials", "Error", "error")
            logger.error(
                "Instaloader login failed due to bad credentials",
                extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
            )
            raise
        except instaloader.exceptions.ConnectionException as e:
            self._display_panel(f"✘ Network error: {str(e)}", "Error", "error")
            logger.error(
                "Network error during login",
                extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
            )
            raise

    async def _handle_two_factor_auth(self, password: str) -> None:
        """
        Handles two-factor authentication for Instaloader.

        Prompts the user for the 2FA code, performs the 2FA login, and saves the session.
        :param password: Instagram account password (unused in this flow).
        """
        code = input("Enter 2FA code for Instaloader: ")
        console.print("🔐 Processing 2FA code for Instaloader...", style="yellow")
        with console.status("Verifying 2FA...", spinner="dots"):
            self.loader.two_factor_login(code)
        self._display_panel("✅ Instaloader logged in with 2FA!", "Success", "success")
        self._logger.info(
            "Instaloader logged in with 2FA",
            extra={"function": "login", "username": self.config.instagram_username, "phase": "Login"}
        )
        self.loader.save_session_to_file(self.config.instagram_username)
        # Instantiate the aiograpi client after 2FA login.
        self.ig_client = AiograpiClient()

    def _display_panel(self, message: str, title: str, theme_key: str) -> None:
        """
        Displays a message in a formatted Rich Panel.

        :param message: The message text.
        :param title: The panel title.
        :param theme_key: Key to select the theme color.
        """
        panel = Panel(
            message,
            title=title,
            border_style=THEMES[self.config.theme][theme_key],
            box=HEAVY
        )
        console.print(panel)

