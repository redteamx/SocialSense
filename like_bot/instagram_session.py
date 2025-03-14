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

# Import the AIograpi Client per its documentation (no initialize() method).
from aiograpi import Client as AiograpiClient

console = Console()


class InstagramSession:
    """
    Manages an Instagram session using Instaloader.

    This class handles login using Instaloader. It first attempts to load an existing session
    from a file (named "<username>.session"). If the session does not exist or fails to load,
    it performs an interactive login (which prompts for credentials and handles two-factor authentication)
    and then saves the session for future use. After a successful login, it instantiates an aiograpi client.
    This implementation follows best practices from Instaloader and aiograpi.
    """

    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        """
        Initializes the InstagramSession with configuration and an async logger.

        :param config: Configuration settings.
        :param async_logger: An asynchronous logger for session events.
        """
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
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
        self.ig_client: Optional[AiograpiClient] = None

    async def __aenter__(self) -> "InstagramSession":
        try:
            await self.async_logger.info(
                "Initializing Instagram session with custom SSL context",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            self.loader = instaloader.Instaloader(request_timeout=120)
            self.loader.context.user_agent = random.choice(self.user_agents)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                connector=aiohttp.TCPConnector(ssl=ssl_context)
            )
            await asyncio.sleep(2)
            await self.async_logger.info(
                "Instagram session initialized",
                extra={"function": "__aenter__", "phase": "Setup"}
            )
            return self
        except Exception as e:
            await self.async_logger.error(
                f"Failed to initialize Instagram session: {e}",
                extra={"function": "__aenter__", "phase": "Setup"},
                exc_info=True
            )
            raise

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
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
                f"Error during Instagram session cleanup: {e}",
                extra={"function": "__aexit__", "phase": "Cleanup"},
                exc_info=True
            )
        finally:
            self.session = None
            self.loader = None
            self.ig_client = None

    async def save_session(self, filepath: str = None) -> None:
        if self.loader:
            if filepath is None:
                filepath = self.session_file
            self.loader.save_session_to_file(self.config.instagram_username)
            self._logger.info(
                "Session saved",
                extra={"function": "save_session", "file": filepath, "phase": "Session"}
            )

    async def load_session(self, filepath: str = None) -> bool:
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
        Uses interactive login (which worked previously) to log in and then saves the session.
        If a session file exists and loads successfully, it reuses the session.
        If a LoginException occurs with a message about two-factor authentication, it triggers the 2FA flow.
        After login, it instantiates the aiograpi client.
        Credentials are loaded from the environment.
        """
        logger = self._logger
        username = os.getenv("INSTAGRAM_USERNAME", self.config.instagram_username)
        self.config.instagram_username = username
        self.session_file = f"{username}.session"

        if os.path.exists(self.session_file):
            if await self.load_session(self.session_file):
                self._display_panel("✅ Instaloader session loaded successfully!", "Success", "success")
                logger.info(
                    "Instaloader session loaded successfully",
                    extra={"function": "login", "username": username, "phase": "Login"}
                )
                self.ig_client = AiograpiClient()
                return
            else:
                logger.warning(
                    "Session file exists but failed to load. Proceeding with interactive login.",
                    extra={"function": "login", "phase": "Login"}
                )
        try:
            self.loader.interactive_login(username)
            self.loader.save_session_to_file(username)
            self._display_panel("✅ Instaloader login successful!", "Success", "success")
            logger.info(
                "Instaloader logged in successfully",
                extra={"function": "login", "username": username, "phase": "Login"}
            )
            self.ig_client = AiograpiClient()
        except instaloader.exceptions.LoginException as e:
            if "two-factor" in str(e).lower():
                await self._handle_two_factor_auth(password)
            else:
                self._display_panel(f"✘ Instaloader login failed: {str(e)}", "Error", "error")
                logger.error(
                    "Instaloader login failed",
                    extra={"function": "login", "username": username, "error": str(e), "phase": "Login"}
                )
                raise
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
        self.ig_client = AiograpiClient()

    def _display_panel(self, message: str, title: str, theme_key: str) -> None:
        panel = Panel(
            message,
            title=title,
            border_style=THEMES[self.config.theme][theme_key],
            box=HEAVY
        )
        console.print(panel)

