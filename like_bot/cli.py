import asyncio
import os
import sys
import signal
import logging
from datetime import datetime
from getpass import getpass
from collections import deque
from typing import Optional

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
import inspect

# Debug prints to confirm module-level imports.
print(f"Imported Database from: {Database.__module__}")
print(f"Database.__aenter__ location: {Database.__aenter__.__code__.co_filename}")
print(f"Database.__aenter__ signature: {inspect.signature(Database.__aenter__)}")
print(f"Imported InstagramClient from: {InstagramClient.__module__}")

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
def cli(ctx: click.Context, log_level: str, verbose: bool, theme: str, request_delay: float, intra_request_delay: float, concurrency_limit: int) -> None:
    """
    Main CLI group for the Instagram Like Bot.

    This function initializes logging, configuration, and shared objects that are passed to subcommands.
    If no subcommand is provided, it displays a welcome message and exits.
    """
    if ctx.invoked_subcommand is None:
        console.print(
            Markdown(
                "# Welcome to Instagram Like Bot\nRun `like_bot --help` for usage details.",
                style=f"on {THEMES[theme]['bg']}"
            )
        )
        ctx.exit(0)

    # Setup logging and configuration.
    log_buffer = deque(maxlen=50)
    logger, async_logger, stop_event = setup_logging(log_level, verbose, theme, log_buffer)
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

    This command reads usernames from a specified file, logs into Instagram, and processes users
    by liking their posts according to the defined parameters.
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

async def run_async(ctx: click.Context, file: str, limit: Optional[int]) -> None:
    """
    Asynchronous main execution for processing Instagram users.

    This function initializes the Instagram client, database, and session, then logs in to Instagram,
    reads usernames from a file, inserts them into the database, and processes pending users.
    It also handles periodic metrics updates and graceful shutdown on signal interruption.

    :param ctx: Click context with shared configuration and logging objects.
    :param file: Path to the file containing Instagram usernames.
    :param limit: Optional limit on the number of usernames to process.
    """
    config = ctx.obj["config"]
    log_buffer = ctx.obj["log_buffer"]
    async_logger = ctx.obj["async_logger"]
    stop_event = ctx.obj["stop_event"]

    await async_logger.info("Starting async execution", extra={"phase": "Setup"})

    client = InstagramClient(config, async_logger)
    client.log_buffer = log_buffer

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(client.shutdown(stop_event)))

    try:
        # Debug the Database instance at runtime.
        db_instance = Database(config, async_logger)
        print(f"Runtime Database.__aenter__ signature: {inspect.signature(db_instance.__aenter__)}")
        print(f"Runtime Database.__aenter__ location: {db_instance.__aenter__.__code__.co_filename}")

        async with db_instance as db:
            async with InstagramSession(config, async_logger) as session:
                console.print(
                    f"🔑 Please enter your Instagram password for {config.instagram_username}",
                    style="yellow"
                )
                password = getpass("Password: ")
                await session.login(password)
                await async_logger.info("Logged in successfully", extra={"phase": "Login"})

                with Live(console=console, refresh_per_second=4, transient=False) as live:
                    client.current_op = f"Checking {file} existence"
                    live.update(client.generate_dashboard(0))
                    if not os.path.exists(file):
                        console.print(
                            Panel(
                                f"✘ File {file} does not exist.",
                                title="Error",
                                border_style=THEMES[config.theme]["error"],
                                box=HEAVY
                            )
                        )
                        sys.exit(1)

                    client.current_op = f"Reading usernames from {file}"
                    live.update(client.generate_dashboard(0))
                    try:
                        with open(file, "r", encoding="utf-8") as f:
                            usernames = [
                                line.strip()
                                for i, line in enumerate(f)
                                if line.strip() and (limit is None or i < limit)
                            ]
                        if not usernames:
                            console.print(
                                Panel(
                                    f"✘ File {file} is empty or contains no valid usernames.",
                                    title="Error",
                                    border_style=THEMES[config.theme]["error"],
                                    box=HEAVY
                                )
                            )
                            sys.exit(1)
                    except Exception as e:
                        console.print(
                            Panel(
                                f"✘ Error reading {file}: {str(e)}.",
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

                    client.current_op = "Inserting users into database"
                    live.update(client.generate_dashboard(0))
                    await db.insert_users(usernames)

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

                    metrics_task = asyncio.create_task(client.periodic_update_metrics(db, session, METRICS_UPDATE_INTERVAL))
                    tasks = [
                        client.process_user(username, progress, task_id, live, db, session)
                        for username in pending_users
                    ]
                    await asyncio.gather(*tasks)

                    # Batch end animation.
                    for i in range(3):
                        client.current_op = BATCH_END_ANIM[i % len(BATCH_END_ANIM)]
                        live.update(client.generate_dashboard(0, "N/A", progress, anim_frame=i))
                        await asyncio.sleep(0.3)

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

    This command drops existing tables and recreates them using the configured schema.
    """
    console.print(
        f"Resetting database schema...",
        style=f"bold {THEMES[ctx.obj['config'].theme]['warning']}"
    )
    asyncio.run(migrate_async(ctx))

async def migrate_async(ctx: click.Context) -> None:
    """
    Asynchronous migration process that resets the database schema.

    :param ctx: Click context containing configuration and logging objects.
    """
    config = ctx.obj["config"]
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
    stop_event.set()

if __name__ == "__main__":
    cli()

