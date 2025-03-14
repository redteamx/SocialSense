# like_bot/instagram_client.py
import json
import time
import asyncio
import random
import sys
import threading
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List, Deque, Tuple

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

    Blocking operations (e.g., Instaloader API calls) are offloaded using asyncio.to_thread.
    """

    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        """
        Initialize the InstagramClient with configuration and logging.

        Args:
            config (Config): Configuration object containing settings.
            async_logger (AsyncLogger): Logger for asynchronous logging.
        """
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
        self._stats: Dict[str, int] = defaultdict(int)
        self._log_buffer: Deque[Tuple[Text, float]] = deque(maxlen=50)
        self._status_buffer: Deque[Tuple[Text, float]] = deque(maxlen=50)
        self._start_time: float = time.time()
        self._current_op: str = "Initializing..."
        self._current_phase: str = "Setup"
        self._processed_count: int = 0
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        self._metrics: Dict[str, Any] = self._initialize_metrics()
        self._last_clock_update: float = 0.0
        self._loader: Optional[instaloader.Instaloader] = None
        self._own_profile: Optional[instaloader.Profile] = None

    def _initialize_metrics(self) -> Dict[str, Any]:
        """Initialize metrics dictionary with default values."""
        return {
            "total_likes": 0,
            "new_followers": 0,
            "followers_gained_year": 0,
            "followers_gained_month": 0,
            "followers_gained_week": 0,
            "followers_gained_today": 0,
            "followers_lost_year": 0,
            "followers_lost_month": 0,
            "followers_lost_week": 0,
            "followers_lost_today": 0,
            "likes_per_follower_ratio": 0.0
        }

    @property
    def stats(self) -> Dict[str, int]:
        """Get the stats dictionary."""
        return self._stats

    @property
    def processed_count(self) -> int:
        """Get the number of processed users."""
        return self._processed_count

    @property
    def current_op(self) -> str:
        """Get the current operation description."""
        return self._current_op

    @current_op.setter
    def current_op(self, value: str) -> None:
        """Set the current operation description."""
        self._current_op = value

    @property
    def current_phase(self) -> str:
        """Get the current phase."""
        return self._current_phase

    @current_phase.setter
    def current_phase(self, value: str) -> None:
        """Set the current phase."""
        self._current_phase = value

    def debug_state(self) -> None:
        """Log the current internal state for debugging purposes."""
        state = {
            "processed_count": self._processed_count,
            "total_likes": self._metrics["total_likes"],
            "new_followers": self._metrics["new_followers"],
            "stats": dict(self._stats)
        }
        asyncio.create_task(
            self.async_logger.debug(f"Internal State: {state}", extra={"phase": "DebugState"})
        )

    async def login_to_instagram(self, password: str) -> None:
        """
        Log in to Instagram using Instaloader, reusing a saved session if available.

        Args:
            password (str): Password for the Instagram account.

        Raises:
            instaloader.exceptions.TwoFactorAuthRequiredException: If 2FA is required.
            instaloader.exceptions.ConnectionException: If login fails due to network issues.
        """
        def blocking_login() -> instaloader.Instaloader:
            L = instaloader.Instaloader()
            try:
                L.load_session_from_file(self.config.instagram_username)
                logging.info("Loaded session from file")
            except FileNotFoundError:
                logging.info("No saved session found; proceeding to login")
            if not L.context.is_logged_in:
                try:
                    L.login(self.config.instagram_username, password)
                    logging.info("Logged in successfully")
                except instaloader.TwoFactorAuthRequiredException:
                    print("Two-factor authentication is required.")
                    code = input("Enter 2FA code for Instaloader: ")
                    L.two_factor_login(code)
                    logging.info("Two-factor authentication completed")
            L.save_session_to_file()
            return L

        self._loader = await asyncio.to_thread(blocking_login)
        await self.async_logger.info(
            "Instagram login successful",
            extra={"username": self.config.instagram_username}
        )

    @async_retrying(max_retries=NETWORK_MAX_RETRIES, initial_delay=MIN_DELAY)
    async def fetch_profile(self, username: str, context: instaloader.InstaloaderContext) -> instaloader.Profile:
        """
        Fetch the Instagram profile for the given username.

        Args:
            username (str): The Instagram username to fetch.
            context (instaloader.InstaloaderContext): The Instaloader context for API calls.

        Returns:
            instaloader.Profile: The fetched profile object.

        Raises:
            instaloader.exceptions.ProfileNotExistsException: If the profile does not exist.
        """
        await self.async_logger.debug(
            f"Fetching profile for user: {username}",
            extra={"phase": "FetchProfileStart"}
        )
        profile = await asyncio.to_thread(instaloader.Profile.from_username, context, username)
        await self.async_logger.debug(
            f"Fetched profile for {username} with userid {profile.userid}",
            extra={"phase": "FetchProfileEnd"}
        )
        return profile

    async def get_own_profile(self, session: InstagramSession) -> instaloader.Profile:
        """
        Retrieve and cache your own Instagram profile.

        Args:
            session (InstagramSession): The session object containing the Instaloader instance.

        Returns:
            instaloader.Profile: The user's own profile object.
        """
        if self._own_profile is None:
            await self.async_logger.debug("Fetching own profile", extra={"phase": "GetOwnProfile"})
            self._own_profile = await self.fetch_profile(self.config.instagram_username, session.loader.context)
            await self.async_logger.debug(
                f"Fetched own profile with userid {self._own_profile.userid}",
                extra={"phase": "GetOwnProfile"}
            )
        else:
            await self.async_logger.debug("Using cached own profile", extra={"phase": "GetOwnProfile"})
        return self._own_profile

    async def update_follower_status(self, db: Database, session: InstagramSession) -> None:
        """
        Update the follower status of target users by comparing their profile IDs against your followers.

        Args:
            db (Database): The database instance for querying and updating user statuses.
            session (InstagramSession): The session object containing the Instaloader instance.
        """
        await self.async_logger.debug(
            "Starting update_follower_status",
            extra={"phase": "UpdateFollowerStatusStart"}
        )
        own_profile = await self.get_own_profile(session)
        own_followers = await asyncio.to_thread(lambda: list(own_profile.get_followers()))
        current_followers = {follower.userid for follower in own_followers}
        await self.async_logger.debug(
            f"Found {len(current_followers)} followers for own profile",
            extra={"phase": "UpdateFollowerStatus"}
        )

        async with db.pool.acquire() as conn:
            target_users = await conn.fetch(
                "SELECT id, profile_id FROM target_users WHERE profile_id IS NOT NULL"
            )
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

        await self.async_logger.debug(
            "Completed update_follower_status",
            extra={"phase": "UpdateFollowerStatusEnd"}
        )
        await self.async_logger.info(
            "Follower status updated",
            extra={"function": "update_follower_status", "phase": "Follower Tracking"}
        )

    async def update_metrics(self, db: Database) -> None:
        """
        Update metrics including total likes, follower gains/losses, and ratios.

        Args:
            db (Database): The database instance for fetching metrics data.
        """
        await self.async_logger.debug("Starting update_metrics", extra={"phase": "UpdateMetricsStart"})
        async with db.pool.acquire() as conn:
            self._metrics["total_likes"] = await conn.fetchval("SELECT COUNT(*) FROM likes") or 0
            self._metrics["new_followers"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at IS NOT NULL"
            ) or 0
            now = datetime.now()
            start_of_year = datetime(now.year, 1, 1)
            start_of_month = datetime(now.year, now.month, 1)
            start_of_week = now - timedelta(days=now.weekday())
            start_of_today = datetime(now.year, now.month, now.day)

            self._metrics["followers_gained_year"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_year
            ) or 0
            self._metrics["followers_gained_month"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_month
            ) or 0
            self._metrics["followers_gained_week"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_week
            ) or 0
            self._metrics["followers_gained_today"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND first_followed_at IS NOT NULL",
                start_of_today
            ) or 0

            self._metrics["followers_lost_year"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_year
            ) or 0
            self._metrics["followers_lost_month"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_month
            ) or 0
            self._metrics["followers_lost_week"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_week
            ) or 0
            self._metrics["followers_lost_today"] = await conn.fetchval(
                "SELECT COUNT(*) FROM target_user_follow_status WHERE first_followed_at >= $1 AND is_currently_following = FALSE",
                start_of_today
            ) or 0

            self._metrics["likes_per_follower_ratio"] = (
                self._metrics["total_likes"] / self._metrics["new_followers"]
                if self._metrics["new_followers"] > 0 else 0.0
            )

        await self.async_logger.debug("Completed update_metrics", extra={"phase": "UpdateMetricsEnd"})

    async def periodic_update_metrics(self, db: Database, session: InstagramSession, interval: int) -> None:
        """
        Periodically update follower status and metrics.

        Args:
            db (Database): The database instance.
            session (InstagramSession): The session object.
            interval (int): Interval in seconds between updates.
        """
        while True:
            await self.update_follower_status(db, session)
            await self.update_metrics(db)
            await asyncio.sleep(interval)

    async def custom_like_media(self, session: InstagramSession, mediaid: Any, timeout: int = 5) -> bool:
        """
        Like media using Instagram's API via aiograpi.

        Args:
            session (InstagramSession): The session object containing the Instagram client.
            mediaid (Any): The media ID to like.
            timeout (int, optional): Timeout in seconds for the API call. Defaults to 5.

        Returns:
            bool: True if the media was liked successfully, False otherwise.
        """
        media_id_str = str(mediaid)
        await self.async_logger.debug(
            f"Attempting to like media with mediaid: {media_id_str}",
            extra={"phase": "CustomLikeMedia"}
        )
        try:
            result = await asyncio.wait_for(session.ig_client.media_like(media_id_str), timeout=timeout)
            await self.async_logger.debug(
                f"Media like result for mediaid {media_id_str}: {result}",
                extra={"phase": "CustomLikeMedia"}
            )
            return result
        except Exception as e:
            await self.async_logger.error(
                "Error in custom_like_media",
                extra={"error": str(e), "mediaid": media_id_str}
            )
            return False

    async def process_user(self, username: str, progress: Progress, task_id: int, live: Live,
                           db: Database, session: InstagramSession) -> bool:
        """
        Process an individual user from the queue.

        Args:
            username (str): The username to process.
            progress (Progress): The progress bar instance.
            task_id (int): The task ID for the progress bar.
            live (Live): The live display instance.
            db (Database): The database instance.
            session (InstagramSession): The session object.

        Returns:
            bool: True if the user should be re-queued, False otherwise.
        """
        async with self._semaphore:
            start_time = time.perf_counter()
            await self.async_logger.debug(
                f"Starting processing for user: {username}",
                extra={"phase": "ProcessUserStart"}
            )
            self._current_phase = "Processing"
            self._current_op = f"Processing {username}"

            async with db.pool.acquire() as conn:
                retry_count = await conn.fetchval(
                    "SELECT retry_count FROM processed_users WHERE target_user_id = (SELECT id FROM target_users WHERE username = $1)",
                    username
                ) or 0

            if retry_count >= MAX_RETRIES:
                await db.update_user_status(username, ProcessingStatus.ERROR.value, retry_count)
                self._stats["errors"] += 1
                self._status_buffer.append((Text(f"✘ Max retries reached for {username}", style="red"), time.time()))
                progress.update(task_id, advance=1)
                self._processed_count += 1
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return False

            await self.async_logger.info(
                "Starting user processing",
                extra={"function": "process_user", "username": username, "retry_count": retry_count, "phase": "Processing"}
            )
            progress.update(task_id, description=f"Processing {username}")
            live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))

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
                self._stats["skipped"] += 1
                self._status_buffer.append((Text(f"⏸️ Skipped {username} (profile does not exist)", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self._processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return False
            except Exception as e:
                await self.async_logger.warning(
                    "Transient error fetching profile",
                    extra={"function": "process_user", "username": username, "error": str(e), "phase": "Processing"}
                )
                status = ProcessingStatus.RETRY.value
                retry_count += 1
                self._stats["retries"] += 1
                delay = min(RATE_LIMIT_DELAY * (2 ** retry_count) + random.uniform(0, 1), MAX_DELAY)
                self._status_buffer.append((Text(f"Retryable error for {username}: {str(e)}. Retrying after {delay:.1f}s ⏳", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self._processed_count += 1
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return False

            if profile.is_private or profile.mediacount == 0:
                self._current_op = f"Skipping {username} ({'private' if profile.is_private else 'no posts'})"
                status = ProcessingStatus.SKIPPED.value
                self._stats["skipped"] += 1
                self._status_buffer.append((Text(f"⏸️ Skipped {username} ({'private' if profile.is_private else 'no posts'})", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self._processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return False

            # Check if the target user follows you
            own_profile = await self.get_own_profile(session)
            own_followers = await asyncio.to_thread(lambda: list(own_profile.get_followers()))
            follower_ids = {follower.userid for follower in own_followers}
            if profile.userid in follower_ids:
                await self.async_logger.info(
                    f"User {username} already follows you; skipping",
                    extra={"phase": "ProcessUserFollowCheck"}
                )
                return False

            # Convert posts generator to a list
            posts_list = await asyncio.to_thread(list, profile.get_posts())
            await self.async_logger.debug(
                f"Total posts fetched for {username}: {len(posts_list)}",
                extra={"phase": "ProcessUserPostsList"}
            )

            post_to_like = None
            post_number = 0
            for idx, post in enumerate(posts_list, start=1):
                await self.async_logger.debug(
                    f"Checking post {idx} before reload: viewer_has_liked={post.viewer_has_liked}",
                    extra={"phase": "ProcessUserPostIteration"}
                )
                await asyncio.to_thread(post.reload)
                await self.async_logger.debug(
                    f"Post {idx} after reload: viewer_has_liked={post.viewer_has_liked}",
                    extra={"phase": "ProcessUserPostIteration"}
                )
                if not post.viewer_has_liked:
                    post_to_like = post
                    post_number = idx
                    await self.async_logger.debug(
                        f"Selected post {idx} for liking",
                        extra={"phase": "ProcessUserPostIteration"}
                    )
                    break

            if post_to_like is None:
                self._current_op = f"Skipping {username} (all posts liked)"
                status = ProcessingStatus.SKIPPED.value
                self._stats["skipped"] += 1
                self._status_buffer.append((Text(f"⏸️ Skipped {username} (all posts liked)", style="yellow"), time.time()))
                await db.update_user_status(username, status, retry_count)
                progress.update(task_id, advance=1)
                self._processed_count += 1
                await asyncio.sleep(self.config.request_delay)
                live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
                return True

            await self.async_logger.info(
                f"Fetched post #{post_number} to like",
                extra={"function": "process_user", "username": username, "shortcode": post_to_like.shortcode, "phase": "Processing"}
            )
            await asyncio.sleep(self.config.intra_request_delay)

            self._current_op = f"Liking post #{post_number} for {username}"
            await self.async_logger.debug(
                f"Attempting to like media for user {username} on post #{post_number} with mediaid: {post_to_like.mediaid}",
                extra={"phase": "ProcessUserLikeAttempt"}
            )
            liked = await self.custom_like_media(session, post_to_like.mediaid, timeout=5)
            if liked:
                self._stats["liked"] += 1
                self._status_buffer.append((Text(f"✅ Liked {username}'s post #{post_number}", style="green"), time.time()))
                await self.async_logger.info(
                    f"Post #{post_number} liked",
                    extra={"function": "process_user", "username": username, "phase": "Processing"}
                )
                async with db.pool.acquire() as conn:
                    target_user_id = await conn.fetchval(
                        "SELECT id FROM target_users WHERE username = $1", username
                    )
                    if target_user_id:
                        await conn.execute(
                            "INSERT INTO likes (target_user_id, post_shortcode) VALUES ($1, $2)",
                            target_user_id, post_to_like.shortcode
                        )
            else:
                self._stats["errors"] += 1
                self._status_buffer.append((Text(f"✘ Failed to like {username}'s post", style="red"), time.time()))
                await self.async_logger.error(
                    "Failed to like post",
                    extra={"function": "process_user", "username": username, "phase": "Processing"}
                )
                status = ProcessingStatus.ERROR.value

            await db.update_user_status(username, status, retry_count)
            progress.update(task_id, advance=1)
            live.update(self.generate_dashboard(progress.tasks[0].total - progress.tasks[0].completed, username, progress))
            self._processed_count += 1
            elapsed = time.perf_counter() - start_time
            await self.async_logger.debug(
                f"Finished processing {username} in {elapsed:.2f} seconds",
                extra={"phase": "ProcessUserEnd"}
            )
            self.debug_state()
            await asyncio.sleep(self.config.request_delay)

            # Re-check if the user now follows you
            updated_profile = await self.fetch_profile(username, session.loader.context)
            own_profile = await self.get_own_profile(session)
            own_followers = await asyncio.to_thread(lambda: list(own_profile.get_followers()))
            follower_ids = {follower.userid for follower in own_followers}
            if updated_profile.userid not in follower_ids:
                await self.async_logger.debug(
                    f"User {username} still does not follow you; will be requeued",
                    extra={"phase": "ProcessUserRequeueCheck"}
                )
                return True
            else:
                await self.async_logger.info(
                    f"User {username} now follows you; removing from queue",
                    extra={"phase": "ProcessUserRequeueCheck"}
                )
                return False

    async def _like_post(self, username: str, post_to_like: Any, retry_count: int, session: InstagramSession) -> Tuple[bool, int, str]:
        """
        Attempt to like a given post with retries and error handling.

        Args:
            username (str): The username associated with the post.
            post_to_like (Any): The post object to like.
            retry_count (int): Current retry count for the operation.
            session (InstagramSession): The session object.

        Returns:
            Tuple[bool, int, str]: (success, updated_retry_count, status)
        """
        for attempt in range(MAX_RETRIES):
            await self.async_logger.debug(
                f"Attempt {attempt + 1} to like post {post_to_like.shortcode} for {username}",
                extra={"phase": "LikePostAttempt"}
            )
            try:
                liked = await self.custom_like_media(session, post_to_like.mediaid, timeout=5)
                await self.async_logger.debug(
                    f"Result of like attempt {attempt + 1} for {username}: {liked}",
                    extra={"phase": "LikePostAttempt"}
                )
                if liked:
                    return True, retry_count, ProcessingStatus.LIKED.value
                else:
                    return False, retry_count, ProcessingStatus.ERROR.value
            except asyncio.TimeoutError:
                await self.async_logger.warning(
                    "Timeout while liking post",
                    extra={"username": username, "attempt": attempt + 1, "phase": "Processing"}
                )
                retry_count += 1
            except ClientError as e:
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait_time = min((2 ** attempt) * RATE_LIMIT_DELAY + random.uniform(0, 1), MAX_DELAY)
                    await self.async_logger.warning(
                        "Rate limited when liking post",
                        extra={"username": username, "attempt": attempt + 1, "max_retries": MAX_RETRIES, "delay": wait_time, "phase": "Processing"}
                    )
                    self._status_buffer.append((Text(f"Rate limited for {username}, waiting {wait_time:.1f}s ⏳", style="yellow"), time.time()))
                    await asyncio.sleep(wait_time)
                else:
                    await self.async_logger.warning(
                        "Transient error liking post",
                        extra={"function": "process_user", "username": username, "error": str(e), "phase": "Processing"}
                    )
                    retry_count += 1
                    if attempt == MAX_RETRIES - 1:
                        self._status_buffer.append((Text(f"✘ Max retries reached for {username} liking post", style="red"), time.time()))
                        return False, retry_count, ProcessingStatus.ERROR.value
                    delay = min((2 ** attempt) * RATE_LIMIT_DELAY + random.uniform(0, 1), MAX_DELAY)
                    self._status_buffer.append((Text(f"Retryable error for {username}: {str(e)}. Retrying after {delay:.1f}s ⏳", style="yellow"), time.time()))
                    await asyncio.sleep(delay)
        return False, retry_count, ProcessingStatus.ERROR.value

    def generate_dashboard(self, pending_users: int, current_username: str = "N/A", progress: Optional[Progress] = None, anim_frame: int = 0) -> Layout:
        """
        Generate a live dashboard layout displaying stats, logs, and current operation status.

        Args:
            pending_users (int): Number of users pending processing.
            current_username (str, optional): Current username being processed. Defaults to "N/A".
            progress (Progress, optional): Progress bar instance. Defaults to None.
            anim_frame (int, optional): Animation frame for dashboard. Defaults to 0.

        Returns:
            Layout: The generated dashboard layout.
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
        if now - self._last_clock_update >= 60:
            self._last_clock_update = now
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
            Panel(f"✔ {self._stats['liked']}", title="Liked", border_style=THEMES[self.config.theme]["success"], box=HEAVY),
            Panel(f"⏸ {self._stats['skipped']}", title="Skipped", border_style=THEMES[self.config.theme]["warning"], box=HEAVY),
            Panel(f"✘ {self._stats['errors']}", title="Errors", border_style=THEMES[self.config.theme]["error"], box=HEAVY),
            Panel(f"🔄 {self._stats['retries']}", title="Retries", border_style=THEMES[self.config.theme]["warning"], box=HEAVY),
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
        for entry, timestamp in reversed(list(self._log_buffer)):
            age = now_time - timestamp
            opacity = max(0.3, 1.0 - (age / 60.0))
            entry_copy = entry.copy()
            entry_copy.stylize(f"opacity({opacity})")
            log_lines.append(entry_copy)
        log_content = Text("\n").join(reversed(log_lines)) if log_lines else Text("No logs yet...", style="dim")
        layout["log"].update(Panel(log_content, title="Logs", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Processing Status
        status_lines = []
        for entry, timestamp in reversed(list(self._status_buffer)):
            age = now_time - timestamp
            opacity = max(0.3, 1.0 - (age / 60.0))
            entry_copy = entry.copy()
            entry_copy.stylize(f"opacity({opacity})")
            status_lines.append(entry_copy)
        status_content = Text("\n").join(reversed(status_lines)) if status_lines else Text("No status yet...", style="dim")
        layout["processing_status"].update(Panel(status_content, title="Processing Status", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Operation Status
        spinner = Spinner("dots", style=THEMES[self.config.theme]["primary"])
        status_text = Text(f" {self._current_op}", style=f"bold {THEMES[self.config.theme]['primary']}")
        layout["status"].update(Panel(Group(spinner, status_text), title="Operation", border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        # Footer with Metrics
        runtime = time.time() - self._start_time
        anim_text = BATCH_START_ANIM[anim_frame % len(BATCH_START_ANIM)] if runtime < 3 else f"Runtime: {runtime:.1f}s"
        metric_text = Text.assemble(
            (f"{anim_text}  ", f"italic {THEMES[self.config.theme]['primary']}"),
            ("Likes: ", f"bold {THEMES[self.config.theme]['success']}"),
            (f"{self._metrics['total_likes']}  ", "white"),
            ("New: ", f"bold {THEMES[self.config.theme]['success']}"),
            (f"{self._metrics['new_followers']}  ", "white"),
            ("Y: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self._metrics['followers_gained_year']}/-{self._metrics['followers_lost_year']}  ", f"{THEMES[self.config.theme]['success']}"),
            ("M: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self._metrics['followers_gained_month']}/-{self._metrics['followers_lost_month']}  ", f"{THEMES[self.config.theme]['success']}"),
            ("W: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self._metrics['followers_gained_week']}/-{self._metrics['followers_lost_week']}  ", f"{THEMES[self.config.theme]['success']}"),
            ("T: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"+{self._metrics['followers_gained_today']}/-{self._metrics['followers_lost_today']}  ", f"{THEMES[self.config.theme]['success']}"),
            ("Ratio: ", f"bold {THEMES[self.config.theme]['primary']}"),
            (f"{self._metrics['likes_per_follower_ratio']:.2f}", "white")
        )
        layout["footer"].update(Panel(metric_text, border_style=THEMES[self.config.theme]["primary"], box=HEAVY))

        return layout

    def generate_summary(self) -> Dict[str, Any]:
        """
        Generate and save a summary report of the processing metrics.

        Returns:
            Dict[str, Any]: The summary dictionary.
        """
        self._current_op = "Generating summary report"
        summary = {
            "total_processed": self._processed_count,
            "liked": self._stats["liked"],
            "skipped": self._stats["skipped"],
            "errors": self._stats["errors"],
            "retries": self._stats["retries"],
            "total_likes": self._metrics["total_likes"],
            "new_followers": self._metrics["new_followers"],
            "followers_gained_year": self._metrics["followers_gained_year"],
            "followers_gained_month": self._metrics["followers_gained_month"],
            "followers_gained_week": self._metrics["followers_gained_week"],
            "followers_gained_today": self._metrics["followers_gained_today"],
            "followers_lost_year": self._metrics["followers_lost_year"],
            "followers_lost_month": self._metrics["followers_lost_month"],
            "followers_lost_week": self._metrics["followers_lost_week"],
            "followers_lost_today": self._metrics["followers_lost_today"],
            "likes_per_follower_ratio": self._metrics["likes_per_follower_ratio"],
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
        Gracefully shut down the client by generating a summary report and signaling termination.

        Args:
            stop_event (threading.Event): Event to signal termination.
        """
        self._current_op = "Shutting down"
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

    async def process_queue(self, user_list: List[str], db: Database, session: InstagramSession) -> None:
        """
        Process a list of users in a round-robin fashion.

        Args:
            user_list (List[str]): List of usernames to process.
            db (Database): The database instance.
            session (InstagramSession): The session object.
        """
        queue: Deque[str] = deque(user_list)
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            SpinnerColumn("dots")
        )
        task_id = progress.add_task("Processing Users", total=len(user_list))
        async with Live(self.generate_dashboard(len(queue)), refresh_per_second=2) as live:
            while queue:
                username = queue.popleft()
                await self.async_logger.debug(
                    f"Dequeued user: {username}",
                    extra={"phase": "ProcessQueue"}
                )
                needs_requeue = await self.process_user(username, progress, task_id, live, db, session)
                if needs_requeue:
                    await self.async_logger.debug(
                        f"Re-queuing user: {username}",
                        extra={"phase": "ProcessQueue"}
                    )
                    queue.append(username)
                progress.update(task_id, completed=len(user_list) - len(queue))
                await asyncio.sleep(1)
            await self.async_logger.info(
                "All users now follow you; queue is empty",
                extra={"phase": "ProcessQueue"}
            )
