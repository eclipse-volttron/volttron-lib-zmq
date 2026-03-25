import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
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
    def __init__(self, db_file='message_cache.db', throttle_seconds=1, max_mem_cache_mb=None,
                 cache_limit_hours=120, cache_limit_gb=None):
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
        self.logger.debug(f"cache limit hours is {cache_limit_hours}")
        self.logger.debug(f"cache limit GB is {cache_limit_gb}")
        self.cache_limit_delta = None
        if cache_limit_hours:
            self.cache_limit_delta = timedelta(hours=cache_limit_hours)
        self.max_pages = None
        if cache_limit_gb:
            cursor.execute('''PRAGMA page_size''')
            page_size = cursor.fetchone()[0]
            max_storage_bytes = float(cache_limit_gb) * 1024 ** 3
            self.max_pages = max_storage_bytes / page_size
            self.logger.debug(f"Max pages is {self.max_pages}")
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
                self.logger.debug(f"Cache limit hours(delta) {self.cache_limit_delta} and max pages is {self.max_pages}")
                if self.cache_limit_delta or self.max_pages:
                    # every time we flush data from memory to db,
                    # check if there are records older than configured hours
                    # or size of db exceeds any configured limit
                    self.manage_db_size()
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
            self.flush_to_db(platform_id)
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
        self.logger.debug(f"platform id : {platform_id}")
        self.logger.debug(f"timestamps: {timestamps}  type: {type(timestamps)}")
        result = cursor.executemany("DELETE from missed_messages WHERE platform_id = ? "
                                "AND cached_time = ?",
                                 [(platform_id, t) for t in timestamps ])
        self.logger.debug(f"**********************DELETED {cursor.rowcount}")
        conn.commit()

    def manage_db_size(self):
        self.logger.debug(f"In manage cache. cache_limit_delta is {self.cache_limit_delta}")
        commit = False
        _connection = None
        try:
            _connection = self.get_connection()
            c = _connection.cursor()
            if self.cache_limit_delta is not None:
                cache_limit_timestamp = get_aware_utc_now() - self.cache_limit_delta
                self.logger.debug(f"In manage cache. cache_limit_ts is {cache_limit_timestamp}")
                c.execute(
                    '''DELETE FROM missed_messages '''
                    ''' WHERE cached_time < ?''', (cache_limit_timestamp,))

                if c.rowcount > 0:
                    self.logger.debug(f"Deleted {c.rowcount} old items from missed_message")
                    commit = True
                else:
                    self.logger.debug(f"NO records older than {cache_limit_timestamp}")

            if self.max_pages is not None:
                def page_count():
                    c.execute("PRAGMA page_count")
                    return c.fetchone()[0]

                def free_count():
                    c.execute("PRAGMA freelist_count")
                    return c.fetchone()[0]

                p = page_count()
                current_free_pages = free_count()

                # Now check if we are above the limit, if so start deleting in batches of 100
                # page count doesnt update even after deleting all records
                # and record count becomes zero. If we have deleted all record
                # exit.
                # self.logger.debug(f"record count before check is {self._record_count} page count is {p}"
                #            f" free count is {f}")
                # max_pages  gets updated based on inserts but freelist_count doesn't
                # enter delete loop based on page_count
                min_free_pages = p - self.max_pages
                self.logger.debug(f"max pages allowed is {self.max_pages} current free pages {current_free_pages} "
                                 f"min free pages: {min_free_pages}")

                # page_count doesn't update till commit but
                # freelist count does. So using that to break from loop
                while current_free_pages < min_free_pages:
                    # error record count is 0, sp set time_error_records to False
                    self.time_error_records = False
                    self.logger.debug("cache size exceeded limit. Deleting data from federation cache")
                    self.logger.debug(f"current free pages is {current_free_pages} min free pages: {min_free_pages}")

                    c.execute(
                        '''DELETE FROM missed_messages
                        WHERE ROWID IN
                        (SELECT ROWID FROM missed_messages
                        ORDER BY ROWID ASC LIMIT 100)''')
                    if c.rowcount > 0:
                        commit = True

                    current_free_pages = free_count()

                    self.logger.debug(f" Cleaning cache since we are over the limit. "
                                      f"After delete of 100 records from cache "
                                      f"number of free pages is{current_free_pages}")
        except Exception:
            self.logger.exception(f"Exception when checking page count and deleting federation cache to manage size")

        if commit and _connection:
            try:
                self.logger.debug("Done cache size reduction. commit")
                _connection.commit()
            except Exception:
                self.logger.exception(f"Exception in committing after back db storage")

