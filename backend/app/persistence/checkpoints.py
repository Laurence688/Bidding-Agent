import sqlite3
from contextlib import contextmanager
from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def sqlite_checkpointer(data_dir):
    connection = sqlite3.connect(data_dir / 'checkpoints.db', check_same_thread=False)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    saver = SqliteSaver(connection)
    saver.setup()
    try:
        yield saver
    finally:
        connection.close()
