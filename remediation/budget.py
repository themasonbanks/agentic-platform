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
        if type(amount) is not int or not (0 if allow_zero else 1) <= amount <= 9_223_372_036_854_775_807:
            raise ValueError("Known integer micro-USD costs required")

    def assert_calls_stopped(self, run):
        """Keep reservations held while any inference dispatch remains unresolved."""
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inference_calls'").fetchone()
        if exists and self.db.execute("SELECT 1 FROM inference_calls WHERE run=? "
                                      "AND status NOT IN ('complete','cancelled') LIMIT 1", (run,)).fetchone():
            raise ValueError('Inference calls must be stopped and reconciled before closing the run')

    def _used(self, period):
        return sum(row[0] for row in self.db.execute(
            "SELECT CASE WHEN status='settled' THEN actual ELSE reserved END "
            "FROM spend_reservations WHERE period=?", (period,)))

    def period_safe(self, period, *, additional=0):
        """Block further execution after overspend or a provider-limit violation."""
        limit = self.db.execute('SELECT ceiling FROM spend_limits WHERE period=?', (period,)).fetchone()
        if limit is None or self._used(period) + additional > limit[0]:
            return False
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inference_calls'").fetchone()
        return not (exists and self.db.execute('''SELECT 1 FROM inference_calls c
            JOIN spend_reservations b ON b.run=c.run
            WHERE b.period=? AND c.status='overrun' LIMIT 1''', (period,)).fetchone())

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
            if not self.period_safe(period, additional=worst_case):
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
            self.assert_calls_stopped(run)
            row = self.db.execute("SELECT actual,status FROM spend_reservations WHERE run=?", (run,)).fetchone()
            if row is None:
                raise ValueError("No reservation")
            if row[1] == 'settled':
                if row[0] != actual:
                    raise ValueError("Conflicting cost reconciliation")
                return
            self.db.execute("UPDATE spend_reservations SET actual=?,status='settled' WHERE run=?", (actual, run))
