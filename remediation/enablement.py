"""Persist owner approvals separately from webhook-controlled job input."""

import sqlite3


class Enablement:
    def __init__(self, db: sqlite3.Connection, owner_id: int):
        if type(owner_id) is not int or owner_id <= 0:
            raise ValueError('Explicit owner identity required')
        self.db, self.owner_id = db, owner_id
        db.executescript('''
            CREATE TABLE IF NOT EXISTS owner_approvals (
                kind TEXT NOT NULL, reference TEXT NOT NULL,
                owner INTEGER NOT NULL, PRIMARY KEY(kind,reference)
            );
            CREATE TABLE IF NOT EXISTS automation_switch (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO automation_switch VALUES(1,0);
        ''')

    def approve(self, authenticated_owner, kind, reference):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        if kind not in {'app', 'endpoint', 'agent', 'budget', 'dry_run'}:
            raise ValueError('Unknown approval kind')
        if not isinstance(reference, str) or not reference or len(reference) > 200:
            raise ValueError('Immutable approval reference required')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO owner_approvals VALUES(?,?,?)',
                            (kind, reference, authenticated_owner))

    def ready(self):
        rows = self.db.execute('SELECT kind,reference FROM owner_approvals WHERE owner=?',
                               (self.owner_id,)).fetchall()
        kinds = {kind for kind, _ in rows}
        return {'app', 'endpoint', 'agent', 'budget'}.issubset(kinds) and sum(
            kind == 'dry_run' for kind, _ in rows) >= 5

    def set_enabled(self, authenticated_owner, enabled):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        if type(enabled) is not bool:
            raise ValueError('Explicit switch value required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if enabled and not self.ready():
                raise PermissionError('Setup approvals and five distinct approved dry runs required')
            self.db.execute('UPDATE automation_switch SET enabled=? WHERE singleton=1', (int(enabled),))

    def enabled(self):
        return self.ready() and self.db.execute(
            'SELECT enabled FROM automation_switch WHERE singleton=1').fetchone()[0] == 1

    def revoke(self, authenticated_owner):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        with self.db:
            self.db.execute('UPDATE automation_switch SET enabled=0 WHERE singleton=1')
            self.db.execute('DELETE FROM owner_approvals')
