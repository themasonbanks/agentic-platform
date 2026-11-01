"""Persist owner approvals separately from webhook-controlled job input."""

import sqlite3
import hashlib
import json


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
            CREATE TABLE IF NOT EXISTS automation_configuration (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL
            );
        ''')

    @staticmethod
    def fingerprint(configuration):
        if (not isinstance(configuration, dict)
                or set(configuration) != {'app', 'endpoint', 'agent', 'budget'}
                or any(not isinstance(v, str) or not v or len(v) > 200 for v in configuration.values())):
            raise ValueError('Exact immutable configuration references required')
        return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()

    def approve(self, authenticated_owner, kind, reference, configuration=None):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        if kind not in {'app', 'endpoint', 'agent', 'budget', 'dry_run'}:
            raise ValueError('Unknown approval kind')
        if not isinstance(reference, str) or not reference or len(reference) > 200:
            raise ValueError('Immutable approval reference required')
        if kind == 'dry_run':
            reference = self.fingerprint(configuration) + ':' + reference
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO owner_approvals VALUES(?,?,?)',
                            (kind, reference, authenticated_owner))

    def ready(self, configuration):
        digest = self.fingerprint(configuration)
        rows = set(self.db.execute('SELECT kind,reference FROM owner_approvals WHERE owner=?',
                                   (self.owner_id,)).fetchall())
        return all((kind, reference) in rows for kind, reference in configuration.items()) and sum(
            kind == 'dry_run' and reference.startswith(digest + ':') for kind, reference in rows) >= 5

    def set_enabled(self, authenticated_owner, enabled, configuration=None):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        if type(enabled) is not bool:
            raise ValueError('Explicit switch value required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if enabled and not self.ready(configuration):
                raise PermissionError('Setup approvals and five distinct approved dry runs required')
            self.db.execute('DELETE FROM automation_configuration')
            if enabled:
                self.db.execute('INSERT INTO automation_configuration VALUES(1,?)', (self.fingerprint(configuration),))
            self.db.execute('UPDATE automation_switch SET enabled=? WHERE singleton=1', (int(enabled),))

    def enabled(self, configuration):
        return self.ready(configuration) and self.db.execute(
            'SELECT enabled FROM automation_switch WHERE singleton=1').fetchone()[0] == 1 and self.db.execute(
                'SELECT digest FROM automation_configuration WHERE singleton=1').fetchone() == (self.fingerprint(configuration),)

    def revoke(self, authenticated_owner):
        if type(authenticated_owner) is not int or authenticated_owner != self.owner_id:
            raise PermissionError('Owner authentication required')
        with self.db:
            self.db.execute('UPDATE automation_switch SET enabled=0 WHERE singleton=1')
            self.db.execute('DELETE FROM owner_approvals')
            self.db.execute('DELETE FROM automation_configuration')
