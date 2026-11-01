"""Persist allowlisted metadata with stable correlation and durable export state."""
import hashlib
import json
import re
import sqlite3
import time

SOURCES = {'agent', 'ci', 'github', 'linear'}
KINDS = {'queued', 'running', 'success', 'failure', 'opened', 'merged', 'closed',
         'backlog', 'todo', 'in_progress', 'done', 'cancelled', 'skipped', 'neutral', 'unknown', 'reviewed'}
NUMBERS = {'pr', 'duration_ms', 'retry_count', 'input_tokens', 'output_tokens', 'cost_microusd', 'review_id'}
IDENTIFIERS = {'run_id', 'sha', 'model', 'review_state', 'review_classification'}


class EventStore:
    def __init__(self, db: sqlite3.Connection, clock=time.time_ns):
        if not callable(clock):
            raise ValueError('Trusted receiver clock required')
        self.db, self.clock = db, clock
        db.execute('''CREATE TABLE IF NOT EXISTS engineering_events (
            source TEXT NOT NULL, event_id TEXT NOT NULL, observed_ns INTEGER NOT NULL,
            trace_id TEXT NOT NULL, span_id TEXT NOT NULL, payload TEXT NOT NULL,
            exported INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source,event_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS engineering_export_claims (
            source TEXT NOT NULL, event_id TEXT NOT NULL, worker TEXT NOT NULL,
            expires_ns INTEGER NOT NULL, PRIMARY KEY(source,event_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS engineering_receipts (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            event_id TEXT NOT NULL, received_ns INTEGER NOT NULL,
            UNIQUE(source,event_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS engineering_export_quarantine (
            source TEXT NOT NULL,event_id TEXT NOT NULL,quarantined_ns INTEGER NOT NULL,
            PRIMARY KEY(source,event_id))''')
        db.commit()

    def append(self, source, event_id, repository, issue, kind, observed_ns, attributes):
        if source not in SOURCES or kind not in KINDS:
            raise ValueError('Unknown event source or kind')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event_id):
            raise ValueError('Invalid provider event identity')
        if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Explicit repository required')
        if not re.fullmatch(r'[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*', issue):
            raise ValueError('Verified issue mapping required')
        if type(observed_ns) is not int or not 0 <= observed_ns <= 9_223_372_036_854_775_807:
            raise ValueError('Integer source timestamp required')
        if set(attributes) - NUMBERS - IDENTIFIERS:
            raise ValueError('Unapproved telemetry fields')
        for key, value in attributes.items():
            if key in NUMBERS and value is not None and (type(value) is not int or not 0 <= value <= 9_223_372_036_854_775_807):
                raise ValueError('Nonnegative integer metadata required')
            if key in IDENTIFIERS and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)):
                raise ValueError('Invalid metadata identifier')
            if key == 'sha' and not re.fullmatch('[0-9a-f]{40}', value):
                raise ValueError('Exact commit SHA required')
        if kind == 'reviewed':
            if (source != 'github' or set(attributes) != {'pr', 'sha', 'review_id', 'review_state', 'review_classification'}
                    or not attributes.get('pr') or not attributes.get('review_id')
                    or attributes['review_state'] not in {'approved', 'changes_requested', 'commented'}
                    or attributes['review_classification'] not in (
                        {'decision'} if attributes['review_state'] != 'commented' else {'inline', 'unclassified'})):
                raise ValueError('Explicit review identity, state and evidence classification required')
        trace_id = hashlib.sha256((repository + '\0' + issue).encode()).hexdigest()[:32]
        span_id = hashlib.sha256((source + '\0' + event_id).encode()).hexdigest()[:16]
        payload = {'source': source, 'event_id': event_id, 'repository': repository,
                   'issue': issue, 'kind': kind, 'observed_ns': observed_ns,
                   'attributes': dict(attributes), 'cost_known': attributes.get('cost_microusd') is not None,
                   'usage_known': attributes.get('input_tokens') is not None and attributes.get('output_tokens') is not None}
        encoded = json.dumps(payload, sort_keys=True)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.db.execute('SELECT payload FROM engineering_events WHERE source=? AND event_id=?',
                                       (source, event_id)).fetchone()
            if existing:
                if existing[0] != encoded:
                    raise ValueError('Conflicting duplicate event')
                return False
            received_ns = self.clock()
            if type(received_ns) is not int or not 0 <= received_ns <= 9_223_372_036_854_775_807:
                raise ValueError('Bounded integer receiver timestamp required')
            self.db.execute('INSERT INTO engineering_events VALUES(?,?,?,?,?,?,0)',
                            (source, event_id, observed_ns, trace_id, span_id, encoded))
            self.db.execute('INSERT INTO engineering_receipts(source,event_id,received_ns) VALUES(?,?,?)',
                            (source, event_id, received_ns))
        return True

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Bounded batch required')
        rows = self.db.execute('SELECT trace_id,span_id,payload FROM engineering_events '
                               'WHERE exported=0 ORDER BY observed_ns,source,event_id LIMIT ?', (limit,)).fetchall()
        return [dict(json.loads(payload), trace_id=trace, span_id=span) for trace, span, payload in rows]

    def acknowledge(self, source, event_id):
        with self.db:
            self.db.execute('UPDATE engineering_events SET exported=1 WHERE source=? AND event_id=?', (source, event_id))

    def claim_next(self, worker, now_ns, lease_ns=300_000_000_000):
        if (not isinstance(worker, str) or not re.fullmatch('[0-9a-f]{32}', worker)
                or type(now_ns) is not int or now_ns < 0
                or type(lease_ns) is not int or not 0 < lease_ns <= 300_000_000_000
                or now_ns + lease_ns > 9_223_372_036_854_775_807):
            raise ValueError('Bounded worker lease required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('''SELECT e.source,e.event_id,e.trace_id,e.span_id,e.payload
                FROM engineering_events e LEFT JOIN engineering_export_claims c
                ON e.source=c.source AND e.event_id=c.event_id
                LEFT JOIN engineering_export_quarantine q ON e.source=q.source AND e.event_id=q.event_id
                WHERE e.exported=0 AND q.source IS NULL AND (c.worker IS NULL OR c.expires_ns<=?)
                ORDER BY e.observed_ns,e.source,e.event_id LIMIT 1''', (now_ns,)).fetchone()
            if row is None:
                return None
            source, identity, trace_id, span_id, payload = row
            self.db.execute('INSERT OR REPLACE INTO engineering_export_claims VALUES(?,?,?,?)',
                            (source, identity, worker, now_ns + lease_ns))
        return dict(json.loads(payload), trace_id=trace_id, span_id=span_id)

    def acknowledge_claim(self, source, event_id, worker, now_ns):
        if type(now_ns) is not int or not 0 <= now_ns <= 9_223_372_036_854_775_807:
            raise ValueError('Trusted receiver timestamp required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            claim = self.db.execute('SELECT worker,expires_ns FROM engineering_export_claims '
                                    'WHERE source=? AND event_id=?', (source, event_id)).fetchone()
            if claim is None or claim[0] != worker or claim[1] <= now_ns:
                return False
            self.db.execute('UPDATE engineering_events SET exported=1 WHERE source=? AND event_id=?',
                            (source, event_id))
            self.db.execute('DELETE FROM engineering_export_claims WHERE source=? AND event_id=?',
                            (source, event_id))
        return True

    def release_claim(self, source, event_id, worker):
        with self.db:
            self.db.execute('DELETE FROM engineering_export_claims WHERE source=? AND event_id=? AND worker=?',
                            (source, event_id, worker))

    def quarantine_claim(self, source, event_id, worker, now_ns):
        if type(now_ns) is not int or not 0 <= now_ns <= 9_223_372_036_854_775_807:
            raise ValueError('Trusted receiver timestamp required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            claim = self.db.execute('''SELECT c.worker,c.expires_ns,e.exported
                FROM engineering_export_claims c JOIN engineering_events e
                ON c.source=e.source AND c.event_id=e.event_id WHERE c.source=? AND c.event_id=?''',
                (source, event_id)).fetchone()
            if claim is None or claim[0] != worker or claim[1] <= now_ns or claim[2] != 0:
                return False
            self.db.execute('INSERT OR IGNORE INTO engineering_export_quarantine VALUES(?,?,?)',
                            (source, event_id, now_ns))
            self.db.execute('DELETE FROM engineering_export_claims WHERE source=? AND event_id=?', (source, event_id))
        return True
