import json
import time
import asyncio
import random
import sys
import threading
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import instaloader
from aiograpi.exceptions import ClientError
from rich.box import HEAVY
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn
)
from rich.spinner import Spinner
from rich.text import Text
from rich.tree import Tree

# Configuration and constants imports
from like_bot.config import (
    Config,
    MAX_RETRIES,
    NETWORK_MAX_RETRIES,
    MIN_DELAY,
    RATE_LIMIT_DELAY,
    MAX_DELAY,
    SUMMARY_FILE,
    METRICS_UPDATE_INTERVAL,
    THEMES,
    BATCH_START_ANIM,
    BATCH_END_ANIM
)
from like_bot.logging import AsyncLogger
from like_bot.enums.processing_status import ProcessingStatus
from like_bot.decorators.retry import async_retrying
from .database import Database
from .instagram_session import InstagramSession

console = Console()


class InstagramClient:
    """
    A client for handling Instagram operations such as fetching profiles, liking posts, 
    tracking metrics, and updating follower statuses.

    This version encapsulates the post-liking logic in a dedicated method, enhances error 
    handling (including rate-limit detection), and logs debug information at key steps.
    """

    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
        self.stats: Dict[str, int] = defaultdict(int)
        self.log_buffer: deque = deque(maxlen=50)
        self.status_buffer: deque = deque(maxlen=50)
        self.start_time: float = time.time()
        self.current_op: str = "Initializing..."
        self.current_phase: str = "Setup"
        self.processed_count: int = 0
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        self.total_likes: int = 0
        self.new_followers: int = 0
        self.followers_gained_year: int = 0
        self.followers_gained_month: int = 0
        self.followers_gained_week: int = 0
        self.followers_gained_today: int = 0
        self.followers_lost_year: int = 0
        self.followers_lost_month: int = 0
        self.followers_lost_week: int = 0
        self.followers_lost_today: int = 0
        self.likes_per_follower_ratio: float = 0.0
        self.last_clock_update: float = 0.0

    def debug_state(self) -> None:
        """
        Logs the current internal state for debugging purposes.
        """
        state = {
            "processed_count": self.processed_count,
            "total_likes": self.total_likes,
            "new_followers": self.new_followers,
            "stats": dict(self.stats)
        }
        asyncio.create_task(
            self.async_logger.debug(f"Internal State: {state}", extra={"phase": "DebugState"})
        )

    @async_retrying(max_retries=NETWORK_MAX_RETRIES, initial_delay=MIN_DELAY)
    async def fetch_profile(self, username: str, context: instaloader.InstaloaderContext) -> instaloader.Profile:
        """
        Fetches the Instagram profile for the given username.
        """
        await self.async_logger.debug(f"Fetching profile for user: {username}", extra={"phase": "FetchProfileStart"})
        profile = instaloader.Profile.from_username(context, username)
        await self.async_logger.debug(f"Fetched profile for {username} with userid {profile.userid}", extra={"phase": "FetchProfileEnd"})
        return profile

    async def update_follower_status(self, db: Database, session: InstagramSession) -> None:
        """
        Updates the follow status of target users by comparing the current followers
        of the bot's own profile.
        """
        await self.async_logger.debug("Starting update_follower_status", extra={"phase": "UpdateFollowerStatusStart"})
        own_profile = await self.fetch_profile(self.config.instagram_username, session.loader.context)
        await self.async_logger.debug(
            f"Fetched own profile: {self.config.instagram_username} with userid {own_profile.userid}",
            extra={"phase": "UpdateFollowerStatus"}
        )
        current_followers = {follower.userid for follower in own_profile.get_followers()}
        await self.async_logger.debug(
            f"Found {len(current_followers)} followers for own profile",
            extra={"phase": "UpdateFollowerStatus"}
        )
        async with db.pool.acquire() as conn:
            target_users = await conn.fetch("SELECT id, profile_id FROM target_users WHERE profile_id IS NOT NULL")
            await self.async_logger.debug(
                f"Fetched {len(target_users)} target users with non-null profile_id",
                extra={"phase": "UpdateFollowerStatus"}
            )
            for user in target_users:
                target_user_id, profile_id = user["id"], user["profile_id"]
                is_following = profile_id in current_followers
                await self.async_logger.debug(
                    f"Target user id {target_user_id}: profile_id={profile_id} is_following={is_following}",
                    extra={"phase": "UpdateFollowerStatus"}
                )
                await conn.execute(
                    """
                    INSERT INTO target_user_follow_status (target_user_id, first_followed_at, is_currently_following)
                    VALUES ($1, CASE WHEN $2 AND NOT EXISTS (
                        SELECT 1 FROM target_user_follow_status WHERE target_user_id = $1 AND first_followed_at IS NOT NULL
                    ) THEN CURRENT_TIMESTAMP ELSE NULL END, $2)
                    ON CONFLICT (target_user_id) DO UPDATE SET
                        is_currently_following = $2,
                        first_followed_at = COALESCE(target_user_follow_status.first_followed_at, 
                            CASE WHEN $2 THEN CURRENT_TIMESTAMP ELSE NULL END)
                    """,
                    target_user_id, is_following
                )
        await self.async_logger.debug("Completed update_follower_status", extra={"phase": "UpdateFollowerStatusEnd"})
        await self.async_logger.info(
            "Follower status updated",
            extra={"function": "update_follower_status", "phase": "Follower Tracking"}
        )

    async def update_metrics(self, db: Database) -> None:
        """
        Updates various metrics including total likes, follower gains/losses, and ratios.
        """
        await self.async_logger.debug("Starting update_metrics", extra={"phase": "UpdateMetricsStart"})
        async with db.pool.acquire() as conn:
            self.total_likes = await conn.fetchval("SELECT COUNT(*) FROM likes") or 0
            self.new_followers = await conn.fetchval("SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at IS NOT NULL") or 0
            now = datetime.now()
            start_of_year = datetime(now.year, 1, 1)
            start_of_month = datetime(now.year, now.month, 1)
            start_of_week = now - timedelta(days=now.weekday())
            start_of_today = datetime(now.year, now.month, now.day)

            self.followers_gained_year = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_year
            ) or 0
            self.followers_gained_month = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_month
            ) or 0
            self.followers_gained_week = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_week
            ) or 0
            self.followers_gained_today = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_today
            ) or 0

            self.followers_lost_year = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_year
            ) or 0
            self.followers_lost_month = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_month
            ) or 0
            self.followers_lost_week = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_week
            ) or 0
            self.followers_lost_today = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_today
            ) or 0

            self.likes_per_follower_ratio = self.total_likes / self.new_followers if self.new_followers > 0 else 0.0

        await self.async_logger.debug("Completed update_metrics", extra={"phase": "UpdateMetricsEnd"})

    async def periodic_update_metrics(self, db: Database, session: InstagramSession, interval: int) -> None:
        """
        Periodically updates follower status and metrics.
        """
        while True:
            await self.update_follower_status(db, session)
            await self.update_metrics(db)
            await asyncio.sleep(interval)

    async def process_user(self, username: str, progress: Progress, task_id: int, live: Live, db: Database, session: InstagramSession) -> None:
        """
        Processes an individual user by fetching their profile, determining if the user's
        posts need to be liked, and handling the like operation with retries and error handling.
        """
        async with self.semaphore:
            start_time = time.perf_counter()
            await self.async_logger.debug(
                f"Starting processing for user: {username}",
                extra={"phase": "ProcessUserStart"}
            )
            self.current_phase = "Processing"
            self.current_op = f"Processing {username}"

            async with db.pool.acquire() as conn:
                retry_count: int = await conn.fetchval(
                    "SELECT retry_count FROM processed_users WHERE target_user_id = (SELECT id FROM target_users WHERE username = $1)",
                    username
                ) or 0

            if retry_count >= MAX_RETRIES:
                await db.update_user_status(username, ProcessingStatus.ERROR.value, retry_count)
                self.stats["errors"] += 1
                self.status_buffer.append((Text(f"✘ Max retries reached for {username}", style="red"), time.time()))
                progress.update(task_id, advance=1)
                self.processed_count += 1
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return

            await self.async_logger.info(
                "Starting user processing",
                extra={"function": "process_user", "username": username, "retry_count": retry_count, "phase": "Processing"}
            )
            progress.update(task_id, description=f"Processing {username}")
            live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))

            profile: Optional[instaloader.Profile] = None
            try:
                profile = await self.fetch_profile(username, session.loader.context)
                async with db.pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE target_users SET profile_id = $1 WHERE username = $2",
                        profile.userid, username
                    )
                await self.async_logger.info(
                    "Profile fetched",
                    extra={
                        "function": "process_user",
                        "username": username,
                        "profile_id": profile.userid,
                        "post_count": profile.mediacount,
                        "phase": "Processing"
                    }
                )
            except instaloader.exceptions.ProfileNotExistsException as e:
                await self.async_logger.info(
                    "Profile does not exist, skipping",
                    extra={"function": "process_user", "username": username, "error": str(e), "phase": "Processing"}
                )
                status = ProcessingStatus.SKIPPED.value
                self.stats["skipped"] += 1
                self.status_buffer.append((Text(f"⏸️ Skipped {username} (profile does not exist)", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self.processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return
            except Exception as e:
                await self.async_logger.warning(
                    "Transient error fetching profile",
                    extra={"function": "process_user", "username": username, "error": str(e), "phase": "Processing"}
                )
                status = ProcessingStatus.RETRY.value
                retry_count += 1
                self.stats["retries"] += 1
                delay = min(RATE_LIMIT_DELAY * (2 ** retry_count) + random.uniform(0, 1), MAX_DELAY)
                self.status_buffer.append((Text(f"Retryable error for {username}: {str(e)}. Retrying after {delay:.1f}s ⏳", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self.processed_count += 1
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return

            if profile.is_private or profile.mediacount == 0:
                self.current_op = f"Skipping {username} ({'private' if profile.is_private else 'no posts'})"
                status = ProcessingStatus.SKIPPED.value
                self.stats["skipped"] += 1
                self.status_buffer.append((Text(f"⏸️ Skipped {username} ({'private' if profile.is_private else 'no posts'})", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self.processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return

            own_profile = await self.fetch_profile(self.config.instagram_username, session.loader.context)
            follows_us = profile.userid in {follower.userid for follower in own_profile.get_followers()}
            if follows_us:
                self.current_op = f"Skipping {username} (already follows us)"
                status = ProcessingStatus.SKIPPED.value
                self.stats["skipped"] += 1
                self.status_buffer.append((Text(f"⏸️ Skipped {username} (already follows us)", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self.processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return

            posts = profile.get_posts()
            post_iterator = iter(posts)
            post_to_like = None
            post_number = 0
            while True:
                try:
                    post = next(post_iterator)
                    post_number += 1
                    if not post.viewer_has_liked:
                        post_to_like = post
                        break
                except StopIteration:
                    self.current_op = f"Skipping {username} (all posts liked)"
                    status = ProcessingStatus.SKIPPED.value
                    self.stats["skipped"] += 1
                    self.status_buffer.append((Text(f"⏸️ Skipped {username} (all posts liked)", style="yellow"), time.time()))
                    await db.update_user_status(username, status, retry_count)
                    progress.update(task_id, advance=1)
                    self.processed_count += 1
                    await asyncio.sleep(self.config.request_delay)
                    live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                    return

            await self.async_logger.info(
                f"Fetched post #{post_number} to like",
                extra={
                    "function": "process_user",
                    "username": username,
                    "shortcode": post_to_like.shortcode,
                    "phase": "Processing"
                }
            )
            await asyncio.sleep(self.config.intra_request_delay)

            self.current_op = f"Liking post #{post_number} for {username}"
            liked, retry_count, status = await self._like_post(username, post_to_like, retry_count, session)
            if liked:
                self.stats["liked"] += 1
                self.status_buffer.append((Text(f"✅ Liked {username}'s post #{post_number}", style="green"), time.time()))
                await self.async_logger.info(
                    f"Post #{post_number} liked",
                    extra={"function": "process_user", "username": username, "phase": "Processing"}
                )
                async with db.pool.acquire() as conn:
                    target_user_id = await conn.fetchval("SELECT id FROM target_users WHERE username = $1", username)
                    if target_user_id:
                        await conn.execute(
                            "INSERT INTO likes (target_user_id, post_shortcode) VALUES ($1, $2)",
                            target_user_id, post_to_like.shortcode
                        )
            else:
                self.stats["errors"] += 1
                self.status_buffer.append((Text(f"✘ Failed to like {username}'s post", style="red"), time.time()))
                await self.async_logger.error(
                    "Failed to like post",
                    extra={"function": "process_user", "username": username, "phase": "Processing"}
                )

            await db.update_user_status(username, status, retry_count)
            progress.update(task_id, advance=1)
            live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
            self.processed_count += 1
            elapsed = time.perf_counter() - start_time
            await self.async_logger.debug(f"Finished processing {username} in {elapsed:.2f} seconds", extra={"phase": "ProcessUserEnd"})
            self.debug_state()
            await asyncio.sleep(self.config.request_delay)

    async def _like_post(self, username: str, post_to_like: Any, retry_count: int, session: InstagramSession) -> (bool, int, str):
        """
        Attempts to like a given post with retries and error handling.
        Returns a tuple of (liked: bool, updated_retry_count: int, status: str).
        """
        for attempt in range(MAX_RETRIES):
            await self.async_logger.debug(
                f"Attempt {attempt + 1} to like post {post_to_like.shortcode} for {username}",
                extra={"phase": "LikePostAttempt"}
            )
            try:
                success = await session.ig_client.media_like(post_to_like.mediaid)
                if success:
                    return True, retry_count, ProcessingStatus.LIKED.value
                else:
                    return False, retry_count, ProcessingStatus.ERROR.value
            except ClientError as e:
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait_time = min((2 ** attempt) * RATE_LIMIT_DELAY + random.uniform(0, 1), MAX_DELAY)
                    await self.async_logger.warning(
                        "Rate limited when liking post",
                        extra={
                            "username": username,
                            "attempt": attempt + 1,
                            "max_retries": MAX_RETRIES,
                            "delay": wait_time,
                            "phase": "Processing"
                        }
                    )
                    self.status_buffer.append((Text(f"Rate limited for {username}, waiting {wait_time:.1f}s ⏳", style="yellow"), time.time()))
                    await asyncio.sleep(wait_time)
                else:
                    await self.async_logger.warning(
                        "Transient error liking post",
                        extra={"function": "process_user", "username": username, "error": str(e), "phase": "Processing"}
                    )
                    retry_count += 1
                    if attempt == MAX_RETRIES - 1:
                        self.status_buffer.append((Text(f"✘ Max retries reached for {username} liking post", style="red"), time.time()))
                        return False, retry_count, ProcessingStatus.ERROR.value
                    delay = min((2 ** attempt) * RATE_LIMIT_DELAY + random.uniform(0, 1), MAX_DELAY)
                    self.status_buffer.append((Text(f"Retryable error for {username}: {str(e)}. Retrying after {delay:.1f}s ⏳", style="yellow"), time.time()))
                    await asyncio.sleep(delay)
        return False, retry_count, ProcessingStatus.ERROR.value

    def generate_dashboard(self, pending_users: int, current_username: str = "N/A", progress: Optional[Progress] = None, anim_frame: int = 0) -> Layout:
        """
        Generates a live dashboard layout displaying stats, logs, and current operation status.
        """
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="status", size=3),
            Layout(name="footer", size=5)
        )
        layout["main"].split_row(
            Layout(name="stats", ratio=2),
            Layout(name="logs_and_status", ratio=3)
        )
        layout["logs_and_status"].split_row(
            Layout(name="log", ratio=1),
            Layout(name="processing_status", ratio=1)
        )

        now = time.time()
        if now - self.last_clock_update >= 60:
            self.last_clock_update = now
        current_time = time.strftime('%H:%M')
        current_date = time.strftime('%Y-%m-%d')

        # Header
        header_layout = Layout()
        header_layout.split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1)
        )
        left_text = Text.assemble(
            (f"INSTAGRAM LIKE BOT v1.0\n", f"bold {THEMES[self.config.theme]['primary']}"),
            ("Use --help for options", f"italic {THEMES[self.config.theme]['primary']}")
        )
        header_layout["left"].update(left_text)
        right_text = Text(f"{current_date} {current_time}", style=f"bold {THEMES[self.config.theme]['success']}")
        right_text.align("right", width=console.width // 3)
        header_layout["right"].update(right_text)
        layout["header"].update(Panel(header_layout, border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Stats Section with Progress Bar
        stats = Columns([
            Panel(f"✔ {self.stats['liked']}", title="Liked", border_style=THEMES[self.config.theme]["success"], box=HEAVY),
            Panel(f"⏸ {self.stats['skipped']}", title="Skipped", border_style=THEMES[self.config.theme]["warning"], box=HEAVY),
            Panel(f"✘ {self.stats['errors']}", title="Errors", border_style=THEMES[self.config.theme]["error"], box=HEAVY),
            Panel(f"🔄 {self.stats['retries']}", title="Retries", border_style=THEMES[self.config.theme]["warning"], box=HEAVY),
            Panel(f"⏳ {pending_users}", title="Pending", border_style=THEMES[self.config.theme]["primary"], box=HEAVY),
        ], expand=True)

        if progress:
            progress_bar = Panel(
                progress,
                title="Progress",
                border_style=THEMES[self.config.theme]["success"],
                box=HEAVY,
                padding=(0, 1),
                width=console.width // 2
            )
            layout["stats"].update(Group(progress_bar, stats))
        else:
            layout["stats"].update(stats)

        # Logs
        log_lines = []
        now_time = time.time()
        for entry, timestamp in reversed(list(self.log_buffer)):
            age = now_time - timestamp
            opacity = max(0.3, 1.0 - (age / 60.0))
            entry_copy = entry.copy()
            entry_copy.stylize(f"opacity({opacity})")
            log_lines.append(entry_copy)
        log_content = Text("\n").join(reversed(log_lines)) if log_lines else Text("No logs yet...", style="dim")
        layout["log"].update(Panel(log_content, title="Logs", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Processing Status
        status_lines = []
        for entry, timestamp in reversed(list(self.status_buffer)):
            age = now_time - timestamp
            opacity = max(0.3, 1.0 - (age / 60.0))
            entry_copy = entry.copy()
            entry_copy.stylize(f"opacity({opacity})")
            status_lines.append(entry_copy)
        status_content = Text("\n").join(reversed(status_lines)) if status_lines else Text("No status yet...", style="dim")
        layout["processing_status"].update(Panel(status_content, title="Processing Status", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Operation Status
        spinner = Spinner("dots", style=THEMES[self.config.theme]["primary"])
        status_text = Text(f" {self.current_op}", style=f"bold {THEMES[self.config.theme]['primary']}")
        layout["status"].update(Panel(Group(spinner, status_text), title="Operation", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Footer with Metrics
        runtime = time.time() - self.start_time
        anim_text = BATCH_START_ANIM[anim_frame % len(BATCH_START_ANIM)] if runtime < 3 else f"Runtime: {runtime:.1f}s"
        metric_text = Text.assemble(
            (f"{anim_text}  ", f"italic {THEMES[self.config.theme]['primary']}"),
            ("Likes: ", f"bold {THEMES[self.config.theme]['success']}"),
            (f"{self.total_likes}  ", "white"),
            ("New: ", f"bold {THEMES[self.config.theme]['success']}"),
            (f"{self.new_followers}  ", "white"),
            ("Y: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self.followers_gained_year}/-{self.followers_lost_year}  ", f"{THEMES[self.config.theme]['success']}"),
            ("M: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self.followers_gained_month}/-{self.followers_lost_month}  ", f"{THEMES[self.config.theme]['success']}"),
            ("W: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self.followers_gained_week}/-{self.followers_lost_week}  ", f"{THEMES[self.config.theme]['success']}"),
            ("T: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self.followers_gained_today}/-{self.followers_lost_today}  ", f"{THEMES[self.config.theme]['success']}"),
            ("Ratio: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"{self.likes_per_follower_ratio:.2f}", "white")
        )
        layout["footer"].update(Panel(metric_text, border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        return layout

    def generate_summary(self) -> Dict[str, Any]:
        """
        Generates and saves a summary report of the processing metrics.
        """
        self.current_op = "Generating summary report"
        summary = {
            "total_processed": self.processed_count,
            "liked": self.stats["liked"],
            "skipped": self.stats["skipped"],
            "errors": self.stats["errors"],
            "retries": self.stats["retries"],
            "total_likes": self.total_likes,
            "new_followers": self.new_followers,
            "followers_gained_year": self.followers_gained_year,
            "followers_gained_month": self.followers_gained_month,
            "followers_gained_week": self.followers_gained_week,
            "followers_gained_today": self.followers_gained_today,
            "followers_lost_year": self.followers_lost_year,
            "followers_lost_month": self.followers_lost_month,
            "followers_lost_week": self.followers_lost_week,
            "followers_lost_today": self.followers_lost_today,
            "likes_per_follower_ratio": self.likes_per_follower_ratio,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        logger = logging.getLogger(__name__)
        try:
            with open(SUMMARY_FILE, "w") as f:
                json.dump(summary, f, indent=2)
            logger.info(
                "Summary report saved",
                extra={"function": "generate_summary", "file": SUMMARY_FILE, "phase": "Summary"}
            )
        except IOError as e:
            logger.error(
                "Failed to write summary file",
                extra={"function": "generate_summary", "file": SUMMARY_FILE, "error": str(e), "phase": "Summary"}
            )

        tree = Tree("Processing Summary", style=f"bold {THEMES[self.config.theme]['primary']}")
        for key, value in summary.items():
            tree.add(f"{key.replace('_', ' ').title()}: {value}", style=f"bold {THEMES[self.config.theme]['success']}")
        console.print(Panel(tree, border_style=THEMES[self.config.theme]["success"], box=HEAVY))

        return summary

    async def shutdown(self, stop_event: threading.Event) -> None:
        """
        Gracefully shuts down the client by generating a summary report and signaling termination.
        """
        self.current_op = "Shutting down"
        await self.async_logger.info(
            "Shutting down gracefully",
            extra={"function": "shutdown", "phase": "Shutdown"}
        )
        self.generate_summary()
        console.print(Panel(
            f"Shutting down... Summary saved to {SUMMARY_FILE}",
            title="Shutdown",
            border_style=THEMES[self.config.theme]["warning"],
            box=HEAVY
        ))
        stop_event.set()
        sys.exit(0)

