"""Persist a run's approved task, attempt reservations and verification manifests."""

from dataclasses import asdict
import json
import re
import time

from task_contract.preflight import validate_task
from task_contract.verification import identify_candidate, verify_candidate


class AttemptStore:
    def __init__(self, db, clock=time.time_ns):
        self.db, self.clock = db, clock
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS task_verification_runs (
                run_id TEXT PRIMARY KEY NOT NULL, task TEXT NOT NULL, expires_ns INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS task_verification_attempts (
                run_id TEXT NOT NULL, number INTEGER NOT NULL, manifest TEXT,
                PRIMARY KEY(run_id, number))''')

    def start(self, run_id, task, policy):
        """Register a validated task once and retain its original expiry across restarts."""
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run_id):
            raise ValueError('Invalid run identifier')
        validate_task(task, policy)
        snapshot = json.dumps(task, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute(
                'SELECT task FROM task_verification_runs WHERE run_id=?', (run_id,),
            ).fetchone()
            if row:
                if row[0] != snapshot:
                    raise ValueError('Run already belongs to a different task snapshot')
                return False
            expires = self.clock() + task['limits']['runtime_seconds'] * 1_000_000_000
            self.db.execute('INSERT INTO task_verification_runs VALUES(?,?,?)', (run_id, snapshot, expires))
        return True

    def reserve(self, run_id, number):
        """Claim the next permitted attempt before dispatching work."""
        if type(number) is not int or number < 1:
            raise ValueError('Invalid attempt number')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            task, expires = self._run(run_id)
            latest = self.current(run_id)
            if latest and number == latest['attempt']:
                return False
            if self.clock() >= expires:
                raise ValueError('Run time limit reached')
            if number > task['limits']['attempts']:
                raise ValueError('Attempt limit reached')
            if latest is None:
                if number != 1:
                    raise ValueError('First attempt must be numbered one')
            elif (number != latest['attempt'] + 1 or latest['manifest'] is None
                  or latest['manifest']['decision'] != 'repair'):
                raise ValueError('Previous attempt has not authorized another repair')
            self.db.execute(
                'INSERT INTO task_verification_attempts (run_id, number) VALUES (?,?)', (run_id, number),
            )
        return True

    def record(self, run_id, number, commit, checks, evidence):
        """Record the controller's evidence for a reserved attempt."""
        if type(number) is not int or number < 1:
            raise ValueError('Invalid attempt number')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            task, expires = self._run(run_id)
            latest = self.current(run_id)
            if latest is None or number != latest['attempt']:
                raise ValueError('Attempt is not current')
            candidate = identify_candidate(task, commit)
            result = verify_candidate(task, candidate, checks, evidence, attempts_used=number)
            manifest = asdict(result)
            manifest.update(run_id=run_id, attempt=number, stop_reason=None)
            saved = latest['manifest']
            expired = saved['stop_reason'] == 'runtime-limit' if saved else self.clock() >= expires
            if expired:
                manifest.update(decision='escalate', stop_reason='runtime-limit')
            encoded = json.dumps(manifest)
            manifest = json.loads(encoded)
            if saved is not None:
                if manifest != saved:
                    raise ValueError('Attempt already has a different verification manifest')
                return saved
            self.db.execute(
                'UPDATE task_verification_attempts SET manifest=? WHERE run_id=? AND number=?',
                (encoded, run_id, number),
            )
            return manifest

    def current(self, run_id):
        row = self.db.execute(
            'SELECT number, manifest FROM task_verification_attempts WHERE run_id=? ORDER BY number DESC LIMIT 1',
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {'attempt': row[0], 'manifest': json.loads(row[1]) if row[1] is not None else None}

    def _run(self, run_id):
        row = self.db.execute(
            'SELECT task, expires_ns FROM task_verification_runs WHERE run_id=?', (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError('Unknown verification run')
        return json.loads(row[0]), row[1]
