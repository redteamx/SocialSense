#!/usr/bin/env python3
"""
Database module for the Instagram Like Bot.

Manages an asynchronous PostgreSQL connection pool and database operations.
"""
import asyncio
import asyncpg
import ssl
from typing import Any, List, Optional, Tuple

from like_bot.config import Config
from like_bot.logging import AsyncLogger
from like_bot.enums.processing_status import ProcessingStatus
from like_bot.decorators.retry import RetryHandler


class Database:
    """
    Manages an asynchronous PostgreSQL connection pool and database operations for the Instagram Like Bot.

    Provides methods to initialize the schema, insert users, update user statuses, and retrieve pending users.
    Uses an asyncpg connection pool with retry logic and configurable SSL handling for robustness.

    Attributes:
        config (Config): Configuration object with database settings.
        async_logger (AsyncLogger): Asynchronous logger for database events.
        pool (Optional[asyncpg.Pool]): Connection pool, initialized in async context.
    """
    def __init__(self, config: Config, async_logger: AsyncLogger) -> None:
        """
        Initialize the Database instance with configuration and logging.

        Args:
            config (Config): Configuration object with database settings.
            async_logger (AsyncLogger): Asynchronous logger for database events.
        """
        self.config: Config = config
        self.async_logger: AsyncLogger = async_logger
        self.pool: Optional[asyncpg.Pool] = None

    @RetryHandler(max_retries=3, initial_delay=5.0).retry
    async def __aenter__(self) -> "Database":
        """
        Enter the async context, creating a connection pool and initializing the schema.

        Configures SSL based on db_config: uses SSL with verification by default, disables verification
        for localhost if ssl_verify=False, or skips SSL if ssl=False.

        Returns:
            Database: Self, with an active connection pool.

        Raises:
            asyncio.TimeoutError: If connection or schema initialization times out.
            asyncpg.PostgresError: If database connection fails after retries.
            ValueError: If SSL configuration is invalid.
        """
        await self.async_logger.info("Initializing database connection", extra={"phase": "Database"})
        try:
            # Extract SSL-related config and prepare a clean db_config for asyncpg
            db_config = dict(self.config.db_config)  # Copy to avoid modifying original
            ssl_config = db_config.pop("ssl", True)  # Remove ssl from db_config
            ssl_verify = db_config.pop("ssl_verify", True)  # Remove ssl_verify
            host = db_config.get("host", "localhost")
            port = db_config.get("port", "5432")
            ssl_context = None

            # Normalize ssl_config
            ssl_enabled = ssl_config is True or str(ssl_config).lower() == "true"
            ssl_verify_enabled = ssl_verify is True or str(ssl_verify).lower() == "true"

            if ssl_enabled:
                ssl_context = ssl.create_default_context()
                if host in ("localhost", "127.0.0.1") and not ssl_verify_enabled:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    await self.async_logger.debug(
                        "SSL verification disabled for localhost",
                        extra={"phase": "Database", "host": host},
                    )
            elif not ssl_enabled:
                ssl_context = None
                await self.async_logger.debug(
                    "SSL disabled for database connection",
                    extra={"phase": "Database", "host": host},
                )
            else:
                raise ValueError(f"Invalid ssl config in db_config: {ssl_config}")

            await self.async_logger.debug(
                "Creating database pool",
                extra={
                    "phase": "Database",
                    "host": host,
                    "port": port,
                    "database": db_config.get("database"),
                    "user": db_config.get("user"),
                    "ssl_enabled": ssl_enabled,
                    "ssl_verify": ssl_verify_enabled,
                },
            )
            self.pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    **db_config,
                    min_size=1,
                    max_size=5,
                    timeout=self.config.request_timeout,
                    ssl=ssl_context,
                ),
                timeout=15.0,
            )
            if not self.pool:
                raise asyncpg.PoolClosedError("Failed to create connection pool")
            await self.async_logger.debug(
                "Database pool created",
                extra={"phase": "Database", "host": host, "ssl": bool(ssl_context)},
            )
            await self._init_schema()
            await self.async_logger.info(
                "Database connection established",
                extra={"phase": "Database", "host": host},
            )
            return self
        except asyncio.TimeoutError as e:
            await self.async_logger.error(
                "Timeout creating database pool",
                extra={
                    "phase": "Database",
                    "error": str(e),
                    "db_config": {k: v for k, v in db_config.items() if k != "password"},
                    "ssl_enabled": ssl_enabled,
                    "ssl_verify": ssl_verify_enabled,
                },
                exc_info=True,
            )
            raise
        except asyncpg.PostgresError as e:
            await self.async_logger.error(
                "PostgreSQL error during pool creation",
                extra={
                    "phase": "Database",
                    "error": str(e),
                    "db_config": {k: v for k, v in db_config.items() if k != "password"},
                    "ssl_enabled": ssl_enabled,
                    "ssl_verify": ssl_verify_enabled,
                },
                exc_info=True,
            )
            raise
        except Exception as e:
            await self.async_logger.error(
                "Unexpected error initializing database connection",
                extra={
                    "phase": "Database",
                    "error": str(e),
                    "db_config": {k: v for k, v in db_config.items() if k != "password"},
                    "ssl_enabled": ssl_enabled,
                    "ssl_verify": ssl_verify_enabled,
                },
                exc_info=True,
            )
            raise
        finally:
            if self.pool and any(isinstance(e, (asyncio.TimeoutError, asyncpg.PostgresError, Exception)) for e in locals().values()):
                try:
                    await self.pool.close()
                except Exception:
                    self.pool.terminate()
                finally:
                    self.pool = None

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Optional[Any],
    ) -> None:
        """
        Exit the async context, closing the connection pool gracefully.

        Args:
            exc_type (Optional[type]): Type of exception raised, if any.
            exc_val (Optional[Exception]): Exception instance raised, if any.
            exc_tb (Optional[Any]): Traceback of exception, if any.
        """
        if not self.pool:
            return

        pool_stats = {
            "size": self.pool.get_size(),
            "idle": self.pool.get_idle_size(),
            "max_size": self.pool._maxsize,  # Corrected from _max_size
        }
        await self.async_logger.debug(
            "Preparing to close database pool",
            extra={"phase": "Database", "pool_stats": pool_stats},
        )
        try:
            await asyncio.wait_for(self.pool.expire_connections(), timeout=2.0)
            await self.async_logger.debug(
                "Connections expired",
                extra={
                    "phase": "Database",
                    "pool_stats": {
                        "size": self.pool.get_size(),
                        "idle": self.pool.get_idle_size(),
                    },
                },
            )
            await asyncio.wait_for(self.pool.close(), timeout=5.0)
            await self.async_logger.debug(
                "Database pool closed successfully",
                extra={"phase": "Database"},
            )
        except asyncio.TimeoutError:
            await self.async_logger.warning(
                "Timeout during pool closure; forcing termination",
                extra={"phase": "Database", "pool_stats": pool_stats},
            )
            self.pool.terminate()
        except Exception as e:
            await self.async_logger.error(
                "Error closing database pool",
                extra={"phase": "Database", "error": str(e), "pool_stats": pool_stats},
                exc_info=True,
            )
            self.pool.terminate()
        finally:
            self.pool = None
            await self.async_logger.debug(
                "Pool set to None",
                extra={"phase": "Database"},
            )

    @RetryHandler(max_retries=3, initial_delay=5.0).retry
    async def _init_schema(self) -> None:
        """
        Initialize the database schema by dropping and recreating tables.

        Raises:
            asyncpg.PostgresError: If schema initialization fails after retries.
        """
        if not self.pool:
            raise asyncpg.PoolClosedError("Database pool not initialized")
        await self.async_logger.info("Initializing database schema", extra={"phase": "Database"})
        conn = await self._acquire_connection()
        try:
            async with conn.transaction():
                await self._drop_tables(conn)
                await self._create_tables(conn)
        finally:
            await self.pool.release(conn)
        await self.async_logger.info("Database schema initialized", extra={"phase": "Database"})

    async def _acquire_connection(self) -> asyncpg.Connection:
        """
        Acquire a connection from the pool with timeout.

        Returns:
            asyncpg.Connection: A database connection.

        Raises:
            asyncpg.PoolClosedError: If the pool is not initialized or closed.
            asyncio.TimeoutError: If acquiring a connection times out.
        """
        if not self.pool:
            raise asyncpg.PoolClosedError("Database pool is not initialized")
        try:
            conn = await asyncio.wait_for(self.pool.acquire(), timeout=10.0)
            await self.async_logger.debug(
                "Connection acquired",
                extra={
                    "phase": "Database",
                    "pool_size": self.pool.get_size(),
                    "idle": self.pool.get_idle_size(),
                },
            )
            return conn
        except asyncio.TimeoutError:
            await self.async_logger.error(
                "Timeout acquiring database connection",
                extra={"phase": "Database"},
            )
            raise

    async def _drop_tables(self, conn: asyncpg.Connection) -> None:
        """Drop existing tables in reverse dependency order."""
        tables = ["processed_users", "likes", "target_user_follow_status", "target_users"]
        for table in tables:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            await self.async_logger.debug(
                f"Dropped table {table}", extra={"phase": "Database", "table": table}
            )

    async def _create_tables(self, conn: asyncpg.Connection) -> None:
        """Create the database schema tables."""
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
        await conn.execute(schema_sql)

    async def insert_users(self, usernames: List[str]) -> None:
        """
        Insert usernames into the target_users table, ignoring duplicates.

        Args:
            usernames (List[str]): List of usernames to insert.

        Raises:
            asyncpg.PostgresError: If insertion fails due to database issues.
        """
        conn = await self._acquire_connection()
        try:
            async with conn.transaction():
                await conn.executemany(
                    "INSERT INTO target_users (username) VALUES ($1) ON CONFLICT (username) DO NOTHING",
                    [(username,) for username in usernames],
                )
                total_count = await conn.fetchval("SELECT COUNT(*) FROM target_users")
            await self.async_logger.info(
                "Users inserted into database",
                extra={"phase": "Database", "username_count": len(usernames), "total_count": total_count},
            )
        finally:
            await self.pool.release(conn)

    async def update_user_status(self, username: str, status: str, retry_count: int) -> None:
        """
        Update the processing status and retry count for a user.

        Args:
            username (str): Username to update.
            status (str): New processing status.
            retry_count (int): Number of retries attempted.

        Raises:
            asyncpg.PostgresError: If update fails due to database issues.
        """
        conn = await self._acquire_connection()
        try:
            async with conn.transaction():
                target_user_id = await conn.fetchval(
                    "SELECT id FROM target_users WHERE username = $1", username
                )
                if target_user_id is None:
                    await self.async_logger.warning(
                        "User not found for status update",
                        extra={"phase": "Database", "username": username},
                    )
                    return
                await conn.execute(
                    """
                    INSERT INTO processed_users (target_user_id, status, retry_count)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (target_user_id)
                    DO UPDATE SET status = $2, retry_count = $3, processed_at = CURRENT_TIMESTAMP
                    """,
                    target_user_id,
                    status,
                    retry_count,
                )
                await conn.execute(
                    "UPDATE target_users SET last_checked = CURRENT_TIMESTAMP WHERE id = $1",
                    target_user_id,
                )
            await self.async_logger.debug(
                "User status updated",
                extra={"phase": "Database", "username": username, "status": status, "retry_count": retry_count},
            )
        finally:
            await self.pool.release(conn)

    async def get_pending_users(self) -> List[str]:
        """
        Retrieve usernames pending processing or retry.

        Returns:
            List[str]: List of usernames that are unprocessed, pending, or due for retry.

        Raises:
            asyncpg.PostgresError: If query fails due to database issues.
        """
        query = """
            SELECT t.username
            FROM target_users t
            LEFT JOIN processed_users p ON t.id = p.target_user_id
            WHERE p.id IS NULL
               OR p.status = $1
               OR (p.status = $2 AND p.processed_at < CURRENT_TIMESTAMP - INTERVAL '1 hour')
            ORDER BY t.added_at ASC
        """
        conn = await self._acquire_connection()
        try:
            rows = await conn.fetch(query, ProcessingStatus.PENDING.value, ProcessingStatus.RETRY.value)
            usernames = [row["username"] for row in rows]
            await self.async_logger.info(
                "Fetched pending users",
                extra={"phase": "Database", "count": len(usernames)},
            )
            return usernames
        finally:
            await self.pool.release(conn)
