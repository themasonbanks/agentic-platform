"""Reserve known worst-case costs before work and reconcile actual usage."""

import sqlite3


class Budget:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS spend_limits (
                period TEXT PRIMARY KEY, ceiling INTEGER NOT NULL CHECK(ceiling >= 0)
            );
            CREATE TABLE IF NOT EXISTS spend_reservations (
                run TEXT PRIMARY KEY, period TEXT NOT NULL,
                reserved INTEGER NOT NULL CHECK(reserved > 0),
                actual INTEGER CHECK(actual >= 0), status TEXT NOT NULL
            );
        """)

    def configure(self, period, ceiling):
        """Record an owner-approved ceiling once; changes require external reconciliation."""
        self._period(period)
        self._amount(ceiling, allow_zero=True)
        with self.db:
            self.db.execute("INSERT INTO spend_limits VALUES(?,?)", (period, ceiling))

    @staticmethod
    def _period(period):
        import re
        if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
            raise ValueError("Use a UTC calendar month")

    @staticmethod
    def _amount(amount, allow_zero=False):
        if type(amount) is not int or amount < (0 if allow_zero else 1):
            raise ValueError("Known integer micro-USD costs required")

    def reserve(self, run, period, worst_case):
        self._period(period)
        self._amount(worst_case)
        if not isinstance(run, str) or not run or len(run) > 200:
            raise ValueError("Explicit unique run identity required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT period,reserved,status FROM spend_reservations WHERE run=?", (run,)
            ).fetchone()
            if existing:
                self.db.rollback()
                return False
            limit = self.db.execute("SELECT ceiling FROM spend_limits WHERE period=?", (period,)).fetchone()
            used = self.db.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual ELSE reserved END),0) "
                "FROM spend_reservations WHERE period=?", (period,)
            ).fetchone()[0]
            if limit is None or used + worst_case > limit[0]:
                self.db.rollback()
                return False
            self.db.execute("INSERT INTO spend_reservations VALUES(?,?,?,NULL,'reserved')", (run, period, worst_case))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, run, actual):
        """Charge actual usage, retaining the reservation when cost is unknown."""
        self._amount(actual, allow_zero=True)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT actual,status FROM spend_reservations WHERE run=?", (run,)).fetchone()
            if row is None:
                raise ValueError("No reservation")
            if row[1] == 'settled':
                if row[0] != actual:
                    raise ValueError("Conflicting cost reconciliation")
                return
            self.db.execute("UPDATE spend_reservations SET actual=?,status='settled' WHERE run=?", (actual, run))
