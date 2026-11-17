"""Shared database helpers used by more than one service: the transaction wrapper and the time stamp."""

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class Tx:
    """BEGIN IMMEDIATE ... COMMIT, rolled back if anything raises.

    BEGIN IMMEDIATE takes the write lock at once, so two people changing the same rows
    cannot race each other (proved by the two-accepts test in tests/test_stories.py).
    """

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        if self.conn.in_transaction:
            self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False
