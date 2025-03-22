# SocialSense/shared/database.py
import psycopg2
from psycopg2 import pool
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

class Database:
    _central_pool = None
    _app_pools = {}

    @staticmethod
    def init_central_db(min_conn=1, max_conn=10):
        """Initialize connection pool for the centralized database."""
        if not Database._central_pool:
            Database._central_pool = psycopg2.pool.SimpleConnectionPool(
                min_conn,
                max_conn,
                database=os.getenv("CENTRAL_DB_NAME", "socialsense_central"),
                user=os.getenv("CENTRAL_DB_USER", "user"),
                password=os.getenv("CENTRAL_DB_PASSWORD", "password"),
                host=os.getenv("CENTRAL_DB_HOST", "localhost"),
                port=os.getenv("CENTRAL_DB_PORT", "5432")
            )
        return Database._central_pool

    @staticmethod
    def init_app_db(app_name, min_conn=1, max_conn=10):
        """Initialize connection pool for an app-specific database."""
        if app_name not in Database._app_pools:
            env_prefix = f"{app_name.upper()}_DB_"
            Database._app_pools[app_name] = psycopg2.pool.SimpleConnectionPool(
                min_conn,
                max_conn,
                database=os.getenv(f"{env_prefix}NAME", f"{app_name}_db"),
                user=os.getenv(f"{env_prefix}USER", "user"),
                password=os.getenv(f"{env_prefix}PASSWORD", "password"),
                host=os.getenv(f"{env_prefix}HOST", "localhost"),
                port=os.getenv(f"{env_prefix}PORT", "5432")
            )
        return Database._app_pools[app_name]

    @staticmethod
    def get_central_connection():
        """Get a connection from the centralized database pool."""
        pool = Database.init_central_db()
        return pool.getconn()

    @staticmethod
    def get_app_connection(app_name):
        """Get a connection from an app-specific database pool."""
        pool = Database.init_app_db(app_name)
        return pool.getconn()

    @staticmethod
    def release_connection(conn, app_name=None):
        """Release a connection back to its pool."""
        if app_name:
            Database._app_pools[app_name].putconn(conn)
        else:
            Database._central_pool.putconn(conn)

# Example usage
if __name__ == "__main__":
    # Central DB connection
    central_conn = Database.get_central_connection()
    with central_conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        print(cursor.fetchone())
    Database.release_connection(central_conn)

    # App-specific connection (e.g., instagram_app)
    app_conn = Database.get_app_connection("instagram_app")
    with app_conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        print(cursor.fetchone())
    Database.release_connection(app_conn, "instagram_app")