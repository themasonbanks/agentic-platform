"""Persist allowlisted metadata with stable correlation and durable export state."""
import hashlib
import json
import re
import sqlite3

SOURCES = {'agent', 'ci', 'github', 'linear'}
KINDS = {'queued', 'running', 'success', 'failure', 'opened', 'merged', 'closed',
         'backlog', 'todo', 'in_progress', 'done', 'cancelled', 'skipped', 'neutral', 'unknown'}
NUMBERS = {'pr', 'duration_ms', 'retry_count', 'input_tokens', 'output_tokens', 'cost_microusd'}
IDENTIFIERS = {'run_id', 'sha', 'model'}


class EventStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute('''CREATE TABLE IF NOT EXISTS engineering_events (
            source TEXT NOT NULL, event_id TEXT NOT NULL, observed_ns INTEGER NOT NULL,
            trace_id TEXT NOT NULL, span_id TEXT NOT NULL, payload TEXT NOT NULL,
            exported INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source,event_id))''')
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
            self.db.execute('INSERT INTO engineering_events VALUES(?,?,?,?,?,?,0)',
                            (source, event_id, observed_ns, trace_id, span_id, encoded))
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
