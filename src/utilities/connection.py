import logging
import threading
from datetime import datetime
from typing import Tuple, Any, List, Iterator

import psycopg2.pool

from paths import DATABASE_CONFIG_PATH
from utilities.ini_parser import parse

logger = logging.getLogger(__name__)


def synchronized(func):
    func.__lock__ = threading.Lock()

    def lock_func(*args, **kwargs):
        with func.__lock__:
            return func(*args, **kwargs)

    return lock_func


class Connection:
    _pool = None
    _sem_remaining = None  # type: threading.Semaphore

    @synchronized
    def __init__(self):
        self.conn = None
        if not Connection._pool:
            Connection._pool = psycopg2.pool.ThreadedConnectionPool(**self.config())
            Connection._sem_remaining = threading.Semaphore(int(self.config().get('maxconn', 4)))

    def __enter__(self, *args, **kwargs):
        """Context Manager enter point, returns an available connection from the _pool"""
        Connection._sem_remaining.acquire(blocking=True)
        self.conn = Connection._pool.getconn(*args, **kwargs)
        # self.get_connection_status(self.conn)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context Manager exit point, put the occupied connection back into the _pool"""
        Connection._pool.putconn(self.conn)
        Connection._sem_remaining.release()

    @staticmethod
    def __call__(*args, **kwargs):
        """returns a newly created connection, which is not maintained by the _pool"""
        connection = psycopg2.connect(*args, **parse(DATABASE_CONFIG_PATH, 'postgresql',
                                                     unwanted_fields=["minconn", "maxconn"]), **kwargs)
        # Connection.get_connection_status(connection)
        return connection

    @staticmethod
    def config():
        return parse(DATABASE_CONFIG_PATH, 'postgresql')

    @staticmethod
    def get_connection_status(connection):
        cursor = connection.cursor()
        cursor.execute("SELECT sum(numbackends) FROM pg_stat_database;")
        connection_count, = cursor.fetchone()
        cursor.execute("SHOW max_connections;")
        connection_max_count, = cursor.fetchone()
        # adding this log to show current database connection status
        logger.info(
            f"[DATABASE] HOST = {Connection.config().get('host')}, CONNECTION COUNT "
            f"= {connection_count}, MAXIMUM = {connection_max_count}")
        cursor.close()

    @staticmethod
    def sql_execute(sql: str) -> Iterator:
        """to execute an SQL query and fetch all results"""
        # logger.info(f"SQL: {sql}")
        if any([keyword in sql.upper() for keyword in ["INSERT", "UPDATE"]]):
            logger.error("You are running INSERT or UPDATE without committing, transaction aborted. Please retry with "
                         "sql_execute_commit")
            return iter([])
        with Connection() as connection:
            cursor = connection.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            cursor.close()
            return iter(rows)

    @staticmethod
    def sql_execute_commit(sql: object) -> None:
        """to execute and commit an SQL query"""
        # logger.info(f"SQL: {sql}")
        with Connection() as connection:
            cursor = connection.cursor()
            cursor.execute(sql)
            connection.commit()
            # logger.info(f"Affected rows:{cursor.rowcount}")
            cursor.close()

    @staticmethod
    def sql_execute_values(sql: str, value_tuples: List[Tuple[Any]], ignore_duplicate: bool = True) -> None:
        """to execute and commit an SQL query with multiple value tuples"""
        value_tuples_sql = ", ".join(
            [f"({', '.join([repr(str(entry)) if isinstance(entry, datetime) else repr(entry) for entry in entries])})"
             for entries in value_tuples])
        if value_tuples_sql:
            sql += " " + value_tuples_sql + f" ON CONFLICT DO NOTHING" if ignore_duplicate else ""
            Connection.sql_execute_commit(sql)
        else:
            logger.info("[DATABASE] Nothing to commit")


if __name__ == '__main__':
    # use as a context manager
    with Connection() as conn:
        cur = conn.cursor()
        cur.execute("select * from pg_tables")
        print(cur.fetchall())
        cur.close()

    # use a non-managed Connection, not maintained by connection pool, need to be closed manually.
    # TODO: remove the support for such usage, once other depended modules are updated.
    conn = Connection()()  # call the object to return a new connection
    cur = conn.cursor()
    cur.execute("select * from pg_tables")
    print(cur.fetchall())
    cur.close()
    conn.close()  # user should close it manually

    # execute sql directly, with output fetched iteratively
    for row in Connection.sql_execute("select * from pg_tables"):
        print(row)

    # remains supported for now.
    for row in Connection().sql_execute("select * from pg_tables"):
        print(row)
