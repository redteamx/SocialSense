import asyncio
import asyncpg
from typing import Any, List, Optional

from like_bot.config import Config
from like_bot.logging import AsyncLogger
from like_bot.enums.processing_status import ProcessingStatus
from like_bot.decorators.retry import async_retrying


class Database:
    """
    A class for managing the asynchronous connection pool, schema initialization, and database operations.

    This class provides methods to connect to the database, initialize its schema,
    insert users, update user statuses, and retrieve pending users. The public interface
    remains unchanged while internal improvements enhance readability, maintainability,
    and error handling.
    """

    def __init__(self, config: Config, async_logger: AsyncLogger):
        """
        Initializes the Database instance with configuration and async logger.

        :param config: Config object containing database and other configuration parameters.
        :param async_logger: An asynchronous logger for recording database events.
        """
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
        self.pool: Optional[asyncpg.Pool] = None

    @async_retrying(max_retries=3, initial_delay=5.0)
    async def __aenter__(self) -> "Database":
        """
        Asynchronous context manager entry that creates the database connection pool
        and initializes the schema.

        :return: The Database instance with an active connection pool.
        :raises: asyncio.TimeoutError or other exceptions on failure.
        """
        await self.async_logger.info(
            "Connecting to database",
            extra={"function": "__aenter__", "phase": "Database"}
        )
        try:
            self.pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    **self.config.db_config,
                    min_size=1,
                    max_size=5,
                    timeout=self.config.request_timeout
                ),
                timeout=15.0
            )
            await self.async_logger.info(
                "Database pool created",
                extra={"function": "__aenter__", "phase": "Database"}
            )
            await self.init_schema()
            return self
        except asyncio.TimeoutError:
            await self.async_logger.error(
                "Timeout connecting to database or initializing schema",
                extra={"function": "__aenter__", "phase": "Database"},
                exc_info=True
            )
            raise
        except Exception as e:
            await self.async_logger.error(
                "Database connection or initialization failed",
                extra={"function": "__aenter__", "error": str(e), "phase": "Database"},
                exc_info=True
            )
            raise

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Asynchronous context manager exit that gracefully closes the database connection pool.

        :param exc_type: Exception type if raised.
        :param exc_val: Exception value if raised.
        :param exc_tb: Exception traceback if raised.
        """
        if self.pool:
            try:
                await asyncio.wait_for(self.pool.close(), timeout=10.0)
                await self.async_logger.info(
                    "Database pool closed",
                    extra={"function": "__aexit__", "phase": "Database"}
                )
            except asyncio.TimeoutError:
                await self.async_logger.error(
                    "Timeout closing database pool",
                    extra={"function": "__aexit__", "phase": "Database"}
                )
                self.pool.terminate()
            except Exception as e:
                await self.async_logger.error(
                    "Error closing database pool",
                    extra={"function": "__aexit__", "error": str(e), "phase": "Database"},
                    exc_info=True
                )
            finally:
                self.pool = None

    @async_retrying(max_retries=3, initial_delay=5.0)
    async def init_schema(self) -> None:
        """
        Initializes the database schema by dropping existing tables (if any) and creating new ones.

        :raises: asyncio.TimeoutError or other exceptions on failure.
        """
        await self.async_logger.info(
            "Initializing database schema",
            extra={"function": "init_schema", "phase": "Database"}
        )
        schema_sql = """
            CREATE TABLE target_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                profile_id BIGINT,
                added_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMPTZ
            );
            CREATE TABLE processed_users (
                id SERIAL PRIMARY KEY,
                target_user_id INT UNIQUE REFERENCES target_users(id),
                status TEXT NOT NULL,
                processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                retry_count INT DEFAULT 0
            );
            CREATE TABLE likes (
                id SERIAL PRIMARY KEY,
                target_user_id INT NOT NULL REFERENCES target_users(id),
                post_shortcode TEXT NOT NULL,
                like_timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                was_following_before_like BOOLEAN NOT NULL DEFAULT FALSE
            );
            CREATE TABLE target_user_follow_status (
                target_user_id INT PRIMARY KEY REFERENCES target_users(id),
                first_followed_at TIMESTAMPTZ,
                is_currently_following BOOLEAN
            );
        """
        try:
            async with self.pool.acquire() as conn:
                # Drop existing tables to start with a clean slate.
                for table in ["processed_users", "target_users", "likes", "target_user_follow_status"]:
                    await asyncio.wait_for(
                        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE"),
                        timeout=15.0
                    )
                    await self.async_logger.debug(
                        f"Dropped table {table}",
                        extra={"function": "init_schema", "table": table, "phase": "Database"}
                    )
                # Create tables using the defined schema SQL.
                await asyncio.wait_for(
                    conn.execute(schema_sql),
                    timeout=15.0
                )
                await self.async_logger.info(
                    "Database schema initialized",
                    extra={"function": "init_schema", "phase": "Database"}
                )
        except asyncio.TimeoutError:
            await self.async_logger.error(
                "Timeout during schema initialization",
                extra={"function": "init_schema", "phase": "Database"},
                exc_info=True
            )
            raise
        except Exception as e:
            await self.async_logger.error(
                "Schema initialization failed",
                extra={"function": "init_schema", "error": str(e), "phase": "Database"},
                exc_info=True
            )
            raise

    async def insert_users(self, usernames: List[str]) -> None:
        """
        Inserts a list of usernames into the target_users table, ignoring duplicates.

        :param usernames: List of username strings to insert.
        """
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(
                        "INSERT INTO target_users (username) VALUES ($1) ON CONFLICT (username) DO NOTHING",
                        [(u,) for u in usernames]
                    )
                count = await conn.fetchval("SELECT COUNT(*) FROM target_users")
                await self.async_logger.info(
                    "Users inserted into database",
                    extra={
                        "function": "insert_users",
                        "username_count": len(usernames),
                        "total_count": count,
                        "phase": "Database"
                    }
                )
        except asyncpg.exceptions.PostgresConnectionError as e:
            await self.async_logger.error(
                "Database connection error during user insertion",
                extra={"function": "insert_users", "error": str(e), "phase": "Database"}
            )
            raise

    async def update_user_status(self, username: str, status: str, retry_count: int) -> None:
        """
        Updates the processing status and retry count of a target user in the database.

        :param username: The username whose status is to be updated.
        :param status: The new status value.
        :param retry_count: The current retry count.
        """
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    target_user_id = await conn.fetchval(
                        "SELECT id FROM target_users WHERE username = $1",
                        username
                    )
                    if target_user_id is None:
                        await self.async_logger.warning(
                            "No target user found for status update",
                            extra={"function": "update_user_status", "username": username, "phase": "Database"}
                        )
                        return
                    await conn.execute(
                        """
                        INSERT INTO processed_users (target_user_id, status, retry_count)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (target_user_id) DO UPDATE SET status = $2, retry_count = $3, processed_at = CURRENT_TIMESTAMP
                        """,
                        target_user_id, status, retry_count
                    )
                    await conn.execute(
                        "UPDATE target_users SET last_checked = CURRENT_TIMESTAMP WHERE username = $1",
                        username
                    )
                await self.async_logger.debug(
                    "User status updated",
                    extra={
                        "function": "update_user_status",
                        "username": username,
                        "status": status,
                        "retry_count": retry_count,
                        "phase": "Database"
                    }
                )
        except asyncpg.exceptions.PostgresConnectionError as e:
            await self.async_logger.error(
                "Database connection error during status update",
                extra={"function": "update_user_status", "username": username, "error": str(e), "phase": "Database"}
            )
            raise

    async def get_pending_users(self) -> List[str]:
        """
        Retrieves a list of pending usernames from the target_users table that have not been processed
        or require reprocessing.

        :return: A list of username strings.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT t.username 
                    FROM target_users t 
                    LEFT JOIN processed_users p ON t.id = p.target_user_id 
                    WHERE p.id IS NULL 
                       OR p.status = $1 
                       OR (p.status = $2 AND p.processed_at < CURRENT_TIMESTAMP - INTERVAL '1 hour')
                    ORDER BY t.added_at ASC
                    """,
                    ProcessingStatus.PENDING.value,
                    ProcessingStatus.RETRY.value
                )
                usernames = [row["username"] for row in rows]
                await self.async_logger.info(
                    "Fetched pending users",
                    extra={"function": "get_pending_users", "count": len(rows), "phase": "Database"}
                )
                return usernames
        except asyncpg.exceptions.PostgresConnectionError as e:
            await self.async_logger.error(
                "Database connection error while fetching pending users",
                extra={"function": "get_pending_users", "error": str(e), "phase": "Database"}
            )
            raise

