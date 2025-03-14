#!/usr/bin/env python3
"""
Entry point for the like_bot package.

This module invokes the command-line interface for the Instagram Like Bot.
It is executed when the package is run as a script (e.g., `python -m like_bot`).
"""
import sys
from typing import NoReturn

from like_bot.cli import cli
from like_bot.config import Config
from like_bot.logging import AsyncLogger, setup_logging


def main() -> NoReturn:
    """
    Run the Instagram Like Bot command-line interface.

    This function serves as the entry point for the application, executing the CLI and handling
    unexpected errors by logging them and exiting with an appropriate status code.

    Raises:
        SystemExit: Exits with code 0 on success or 1 on failure.
    """
    # Initialize configuration and logging
    config = Config()
    async_logger = AsyncLogger(config)
    setup_logging(config, async_logger)

    # Log startup (synchronous call since cli() is sync)
    async_logger.sync_info("Starting LikeBot CLI", extra={"phase": "CLI"})
    try:
        cli()  # Synchronous call to cli()
        async_logger.sync_info("LikeBot CLI completed successfully", extra={"phase": "CLI"})
        sys.exit(0)
    except KeyboardInterrupt:
        async_logger.sync_warning("Operation cancelled by user", extra={"phase": "CLI"})
        print("Operation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        async_logger.sync_error(
            "Error running LikeBot CLI",
            extra={"phase": "CLI", "error": str(e)},
            exc_info=True,
        )
        print(f"Error running Like Bot CLI: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
