#!/usr/bin/env python3
import asyncio
import os
import sys
import signal
from getpass import getpass
from typing import Optional, List, Deque
from collections import deque

import click
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.box import HEAVY
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeRemainingColumn

from like_bot.config import Config, THEMES, FOLLOWERS_FILE, LOG_FILE, BATCH_END_ANIM, METRICS_UPDATE_INTERVAL
from like_bot.logging import setup_logging
from like_bot.utils import validate_positive, validate_concurrency
from like_bot.database import Database
from like_bot.instagram_session import InstagramSession
from like_bot.instagram_client import InstagramClient

console = Console()

@click.group(invoke_without_command=True)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Set logging level"
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable verbose output"
)
@click.option(
    "--theme",
    default="dark",
    type=click.Choice(list(THEMES.keys()), case_sensitive=False),
    help="Set UI theme"
)
@click.option(
    "--request-delay",
    default=Config.request_delay,
    type=float,
    callback=validate_positive,
    help=f"Delay between processing users in seconds (default: {Config.request_delay})"
)
@click.option(
    "--intra-request-delay",
    default=Config.intra_request_delay,
    type=float,
    callback=validate_positive,
    help=f"Delay between requests within a user in seconds (default: {Config.intra_request_delay})"
)
@click.option(
    "--concurrency-limit",
    default=Config.concurrency_limit,
    type=int,
    callback=validate_concurrency,
    help=f"Maximum concurrent user processing (default: {Config.concurrency_limit})"
)
@click.pass_context
def cli(
    ctx: click.Context,
    log_level: str,
    verbose: bool,
    theme: str,
    request_delay: float,
    intra_request_delay: float,
    concurrency_limit: int
) -> None:
    """
    Main CLI group for the Instagram Like Bot.

    Initializes logging, configuration, and shared objects that are passed to subcommands.
    If no subcommand is provided, displays a welcome message and exits.

    Args:
        ctx (click.Context): The Click context object for passing data to subcommands.
        log_level (str): The logging level ("DEBUG", "INFO", "WARNING", "ERROR").
        verbose (bool): Whether to enable verbose output.
        theme (str): The UI theme to use.
        request_delay (float): Delay between processing users in seconds.
        intra_request_delay (float): Delay between requests within a user in seconds.
        concurrency_limit (int): Maximum concurrent user processing tasks.

    Returns:
        None
    """
    if ctx.invoked_subcommand is None:
        console.print(
            Markdown(
                "# Welcome to Instagram Like Bot\nRun `like_bot --help` for usage details.",
                style=f"on {THEMES[theme]['bg']}"
            )
        )
        ctx.exit(0)

    # Setup logging and configuration
    log_buffer: Deque[str] = deque(maxlen=50)
    try:
        logger, async_logger, stop_event = setup_logging(log_level, verbose, theme, log_buffer)
    except Exception as e:
        console.print(f"ERROR: Failed to setup logging: {str(e)}", style="red")
        sys.exit(1)
    ctx.obj = {
        "config": Config(
            log_level=log_level,
            verbose=verbose,
            theme=theme,
            request_delay=request_delay,
            intra_request_delay=intra_request_delay,
            concurrency_limit=concurrency_limit
        ),
        "log_buffer": log_buffer,
        "logger": logger,
        "async_logger": async_logger,
        "stop_event": stop_event
    }

@cli.command()
@click.option(
    "--file",
    default=FOLLOWERS_FILE,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
    help=f"File containing Instagram usernames (default: {FOLLOWERS_FILE})"
)
@click.option(
    "--limit",
    default=None,
    type=click.IntRange(min=1),
    help="Limit the number of users to process"
)
@click.pass_context
def run(ctx: click.Context, file: str, limit: Optional[int]) -> None:
    """
    Runs the Instagram Like Bot.

    Reads usernames from a specified file, logs into Instagram, and processes users
    by liking their posts according to the defined parameters.

    Args:
        ctx (click.Context): The Click context containing configuration and logging objects.
        file (str): Path to the file containing Instagram usernames.
        limit (Optional[int]): Maximum number of users to process; None means no limit.

    Raises:
        SystemExit: If an error occurs during async execution.
    """
    console.print(
        f"Starting bot with file: {file}",
        style=f"bold {THEMES[ctx.obj['config'].theme]['primary']}"
    )
    try:
        asyncio.run(run_async(ctx, file, limit))
    except Exception as e:
        console.print(f"ERROR: Failed to run async loop: {str(e)}", style="red")
        asyncio.run(ctx.obj["async_logger"].error(
            "Failed to start async execution",
            extra={"error": str(e), "phase": "Run"},
            exc_info=True
        ))
        sys.exit(1)

async def _read_usernames_from_file(file_path: str, limit: Optional[int], async_logger) -> List[str]:
    """
    Reads usernames from the specified file with an optional limit.

    Args:
        file_path (str): Path to the file containing usernames.
        limit (Optional[int]): Maximum number of usernames to read; None means no limit.
        async_logger: Logger instance for logging errors and info.

    Returns:
        List[str]: List of usernames read from the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        IOError: If there's an error reading the file.
        ValueError: If the file is empty or contains no valid usernames.
    """
    if not os.path.exists(file_path):
        await async_logger.error(
            "File does not exist",
            extra={"file": file_path, "phase": "Setup"}
        )
        raise FileNotFoundError(f"File {file_path} does not exist")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            usernames = [
                line.strip()
                for i, line in enumerate(f)
                if line.strip() and (limit is None or i < limit)
            ]
        if not usernames:
            await async_logger.error(
                "File is empty or contains no valid usernames",
                extra={"file": file_path, "phase": "Setup"}
            )
            raise ValueError(f"File {file_path} is empty or contains no valid usernames")
        return usernames
    except IOError as e:
        await async_logger.error(
            "Error reading file",
            extra={"file": file_path, "error": str(e), "phase": "Setup"}
        )
        raise IOError(f"Error reading {file_path}: {str(e)}") from e

async def _prompt_for_password(config: Config, async_logger) -> str:
    """
    Prompts the user for their Instagram password securely.

    Args:
        config (Config): Configuration object containing the Instagram username.
        async_logger: Logger instance for logging errors and info.

    Returns:
        str: The password entered by the user.

    Raises:
        KeyboardInterrupt: If the user interrupts the password prompt.
        EOFError: If an EOF occurs during input.
        ValueError: If the password is empty.
    """
    try:
        console.print(
            f"🔑 Please enter your Instagram password for {config.instagram_username}",
            style="yellow"
        )
        password = getpass("Password: ")
        if not password:
            await async_logger.error(
                "No password provided",
                extra={"phase": "Login"}
            )
            raise ValueError("Password cannot be empty")
        return password
    except (KeyboardInterrupt, EOFError) as e:
        await async_logger.error(
            "Password prompt interrupted",
            extra={"error": str(e), "phase": "Login"},
            exc_info=True
        )
        raise

async def run_async(ctx: click.Context, file: str, limit: Optional[int]) -> None:
    """
    Asynchronous main execution for processing Instagram users.

    Initializes the Instagram client, database, and session, logs in to Instagram,
    reads usernames from a file, inserts them into the database, and processes pending users.
    Handles periodic metrics updates and graceful shutdown on signal interruption.

    Args:
        ctx (click.Context): Click context with shared configuration and logging objects.
        file (str): Path to the file containing Instagram usernames.
        limit (Optional[int]): Optional limit on the number of usernames to process.

    Raises:
        SystemExit: If a critical error occurs during execution.
    """
    config: Config = ctx.obj["config"]
    log_buffer: Deque[str] = ctx.obj["log_buffer"]
    async_logger = ctx.obj["async_logger"]
    stop_event = ctx.obj["stop_event"]

    await async_logger.info("Starting async execution", extra={"phase": "Setup"})

    client = InstagramClient(config, async_logger)
    client.log_buffer = log_buffer

    # Setup signal handlers for graceful shutdown
    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(client.shutdown(stop_event)))
    except Exception as e:
        await async_logger.error(
            "Failed to setup signal handlers",
            extra={"error": str(e), "phase": "Setup"},
            exc_info=True
        )
        console.print(f"ERROR: Failed to setup signal handlers: {str(e)}", style="red")
        sys.exit(1)

    try:
        async with Database(config, async_logger) as db:
            async with InstagramSession(config, async_logger) as session:
                # Prompt for Instagram password and login
                password = await _prompt_for_password(config, async_logger)
                await session.login(password)
                await async_logger.info("Logged in successfully", extra={"phase": "Login"})

                with Live(console=console, refresh_per_second=4, transient=False) as live:
                    # Read usernames from file
                    client.current_op = f"Reading usernames from {file}"
                    live.update(client.generate_dashboard(0))
                    try:
                        usernames = await _read_usernames_from_file(file, limit, async_logger)
                    except (FileNotFoundError, IOError, ValueError) as e:
                        console.print(
                            Panel(
                                f"✘ {str(e)}.",
                                title="Error",
                                border_style=THEMES[config.theme]["error"],
                                box=HEAVY
                            )
                        )
                        sys.exit(1)
                    await async_logger.info(
                        "Usernames read from file",
                        extra={"function": "run_async", "file": file, "count": len(usernames), "phase": "Setup"}
                    )

                    # Insert users into database
                    client.current_op = "Inserting users into database"
                    live.update(client.generate_dashboard(0))
                    await db.insert_users(usernames)

                    # Fetch pending users
                    client.current_op = "Fetching pending users"
                    live.update(client.generate_dashboard(0))
                    pending_users = await db.get_pending_users()
                    if not pending_users:
                        console.print(
                            Panel(
                                f"⏸️ No new users to process.",
                                title="Info",
                                border_style=THEMES[config.theme]["warning"],
                                box=HEAVY
                            )
                        )
                        return

                    # Setup progress bar for user processing
                    progress = Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TimeRemainingColumn(),
                        console=console
                    )
                    task_id = progress.add_task(f"Processing {len(pending_users)} users...", total=len(pending_users))

                    client.current_op = "Starting user processing"
                    live.update(client.generate_dashboard(len(pending_users), progress=progress))

                    # Start metrics update task and process users concurrently
                    metrics_task = asyncio.create_task(client.periodic_update_metrics(db, session, METRICS_UPDATE_INTERVAL))
                    tasks = [
                        client.process_user(username, progress, task_id, live, db, session)
                        for username in pending_users
                    ]
                    await asyncio.gather(*tasks)

                    # Batch end animation
                    for i in range(3):
                        client.current_op = BATCH_END_ANIM[i % len(BATCH_END_ANIM)]
                        live.update(client.generate_dashboard(0, "N/A", progress, anim_frame=i))
                        await asyncio.sleep(0.3)

                    # Finalize processing
                    await client.update_follower_status(db, session)
                    await client.update_metrics(db)
                    client.generate_summary()
                    console.print(
                        Panel(
                            f"Processing complete! 🎉 Check {LOG_FILE} for details.",
                            title="Success",
                            border_style=THEMES[config.theme]["success"],
                            box=HEAVY
                        )
                    )
                    metrics_task.cancel()

    except Exception as e:
        client.current_op = "Execution failed"
        await async_logger.error(
            "Unexpected error in main execution",
            extra={"function": "run_async", "error": str(e), "phase": "Main"},
            exc_info=True
        )
        console.print(
            Panel(
                f"Error: {str(e)}. See {LOG_FILE} for details.",
                title="Error",
                border_style=THEMES[config.theme]["error"],
                box=HEAVY,
                style=f"on {THEMES[config.theme]['bg']}"
            )
        )
        sys.exit(1)
    finally:
        stop_event.set()

@cli.command()
@click.pass_context
def migrate(ctx: click.Context) -> None:
    """
    Resets the database schema by performing a migration.

    Drops existing tables and recreates them using the configured schema.

    Args:
        ctx (click.Context): The Click context containing configuration and logging objects.

    Raises:
        SystemExit: If an error occurs during migration.
    """
    console.print(
        f"Resetting database schema...",
        style=f"bold {THEMES[ctx.obj['config'].theme]['warning']}"
    )
    try:
        asyncio.run(migrate_async(ctx))
    except Exception as e:
        console.print(f"ERROR: Failed to run migration: {str(e)}", style="red")
        asyncio.run(ctx.obj["async_logger"].error(
            "Failed to start migration",
            extra={"error": str(e), "phase": "Migrate"},
            exc_info=True
        ))
        sys.exit(1)

async def migrate_async(ctx: click.Context) -> None:
    """
    Asynchronous migration process that resets the database schema.

    Args:
        ctx (click.Context): Click context containing configuration and logging objects.

    Raises:
        SystemExit: If migration fails due to timeout or unexpected errors.
    """
    config: Config = ctx.obj["config"]
    async_logger = ctx.obj["async_logger"]
    stop_event = ctx.obj["stop_event"]

    async with Database(config, async_logger) as db:
        try:
            await async_logger.info(
                "Starting database migration",
                extra={"function": "migrate_async", "phase": "Migrate"}
            )
            await asyncio.wait_for(db.init_schema(), timeout=30.0)
            console.print(
                Panel(
                    f"Database schema reset successfully! ✅",
                    title="Success",
                    border_style=THEMES[config.theme]["success"],
                    box=HEAVY,
                    style=f"on {THEMES[config.theme]['bg']}"
                )
            )
        except asyncio.TimeoutError:
            await async_logger.error(
                "Database migration timed out",
                extra={"function": "migrate_async", "phase": "Migrate"}
            )
            console.print(
                Panel(
                    f"Error: Migration timed out after 30 seconds. Check database connectivity.",
                    title="Error",
                    border_style=THEMES[config.theme]["error"],
                    box=HEAVY,
                    style=f"on {THEMES[config.theme]['bg']}"
                )
            )
            sys.exit(1)
        except Exception as e:
            await async_logger.error(
                "Unexpected error during migration",
                extra={"function": "migrate_async", "error": str(e), "phase": "Migrate"},
                exc_info=True
            )
            console.print(
                Panel(
                    f"Error: {str(e)}. See {LOG_FILE} for details.",
                    title="Error",
                    border_style=THEMES[config.theme]["error"],
                    box=HEAVY,
                    style=f"on {THEMES[config.theme]['bg']}"
                )
            )
            sys.exit(1)
        finally:
            stop_event.set()

if __name__ == "__main__":
    cli()
