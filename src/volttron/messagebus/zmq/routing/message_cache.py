import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from threading import Lock
from threading import local

import psutil

from volttron.utils import get_aware_utc_now


def calculate_max_cache_bytes():
    """
    Dynamically calculate the maximum deque size based on system resources.
    """
    # Fetch system memory stats
    memory = psutil.virtual_memory()
    return memory.available / 3  # go up to one third of available memory?


class MessageCache:
    def __init__(self, db_file='message_cache.db', throttle_seconds=1, max_mem_cache_mb=None):
        self.lock = Lock()
        self.logger = logging.getLogger("MessageCache")
        self.in_memory_cache = defaultdict(list)
        self.db_file = db_file
        self.local_storage = local()  # Thread-local storage for SQLite connections
        self.throttle_seconds = throttle_seconds
        if max_mem_cache_mb:
            self.max_mem_cache_bytes = max_mem_cache_mb * 1024 * 1024
        else:
            self.max_mem_cache_bytes = calculate_max_cache_bytes()  # Use passed value or calculate dynamically
        self.logger.info(f"max deque size in bytes {self.max_mem_cache_bytes}")
        # Ensure database table exists (use a temporary connection)
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        # in case of zmq what we cache is zmq frames
        cursor.execute("""
                    CREATE TABLE IF NOT EXISTS missed_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform_id varchar(30),
                        message TEXT, 
                        cached_time DATETIME
                    )
                """)
        # TODO - create index
        # cursor.execute("CREATE INDEX IF NOT EXISTS ")
        conn.commit()
        conn.close()

        self.logger.info("Initialized MessageCache with database: {} and max_deque_size: {}".
                         format(self.db_file, self.max_mem_cache_bytes))

    def get_connection(self):
        """
        Get or create a thread-local SQLite connection.
        """
        if not hasattr(self.local_storage, "connection"):
            self.local_storage.connection = sqlite3.connect(self.db_file)
            self.logger.debug("Created SQLite connection for thread.")
        return self.local_storage.connection

    def write_to_cache(self, platform_id, message: str):
        """Write a message to the cache for the given server."""
        self.in_memory_cache[platform_id].append((platform_id, message, get_aware_utc_now()))
        if sys.getsizeof(self.in_memory_cache) >= self.max_mem_cache_bytes:
            self.flush_to_db()
        self.logger.debug(f"Cached message for {platform_id}: frames:{message}")

    def flush_to_db(self, platform_id=None):
        """
        Batch flush undelivered messages for a subscriber from in-memory cache to SQLite database.
        Useful for backup or reconciliation during disconnection.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        with self.lock:
            try:
                if platform_id:
                    cached_messages = self.in_memory_cache[platform_id]
                    cursor.executemany("INSERT INTO missed_messages (platform_id, message, cached_time) "
                                       "VALUES (?, ?, ?)",
                                       cached_messages)
                    conn.commit()
                    del self.in_memory_cache[platform_id]
                else:
                    for p, cached_messages in self.in_memory_cache.items():
                        cursor.executemany("INSERT INTO missed_messages (platform_id, message, cached_time) "
                                           "VALUES (?, ?, ?)",
                                           cached_messages)
                    conn.commit()
                    self.in_memory_cache = defaultdict(list)
                cursor.close()
                self.logger.debug(f"Flushed message to db. Now in memory cache is  {self.in_memory_cache}")
            except Exception as e:
                self.logger.exception("Exception inserting cache:", e)
                raise

    def read_from_cache(self, platform_id, count) -> [[str, datetime]]:
        """Read a batch of messages (up to `count`) from the cache."""
        # headers = {TIMESTAMP: format_timestamp(get_aware_utc_now())}

        # first flush to db so that we can read from db in order
        if self.in_memory_cache[platform_id]:
            self.flush_to_db()
        self.logger.info(f"Reading cache for {platform_id}")
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT message, cached_time from missed_messages "
                           "WHERE platform_id = ? "
                           "ORDER BY cached_time ASC "
                           "LIMIT ?",
                           (platform_id, count)
                           )
            r = cursor.fetchall()
            return r
        except Exception as e:
            self.logger.exception("Exception reading from cache:", e)
            raise e

    def delete_from_cache(self, platform_id, timestamps):
        """Delete specific messages from the cache for the given server."""
        self.logger.info(f"Deleting cache for {platform_id}")
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executemany("DELETE from missed_messages WHERE platform_id = ? "
                           "AND cached_time = ?",
                           [(platform_id, t) for t in timestamps])
        conn.commit()
