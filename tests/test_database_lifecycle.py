import sqlite3

import pytest

from resilience import ClosingConnection


def test_transactions_commit_rollback_and_release_connection(tmp_path):
    path = tmp_path / 'lifecycle.sqlite3'
    with sqlite3.connect(path, factory=ClosingConnection) as connection:
        connection.execute('CREATE TABLE items(value INTEGER)')
        connection.execute('INSERT INTO items VALUES(1)')
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')
    with pytest.raises(ValueError):
        with sqlite3.connect(path, factory=ClosingConnection) as failed:
            failed.execute('INSERT INTO items VALUES(2)')
            raise ValueError('rollback')
    with pytest.raises(sqlite3.ProgrammingError):
        failed.execute('SELECT 1')
    with sqlite3.connect(path, factory=ClosingConnection) as verified:
        assert verified.execute('SELECT value FROM items').fetchall() == [(1,)]
    path.unlink()
