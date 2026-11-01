"""Coordinate execution leases, owner enablement and durable cost reservations."""
from datetime import datetime, timezone
import json
import time
import uuid
from remediation.process import ProcessStopped


class Supervisor:
    def __init__(self, intake, budget, enablement, configuration, worst_case_microusd,
                 clock=time.time):
        if intake.db is not budget.db or intake.db is not enablement.db:
            raise ValueError('Supervisor components must share one durable connection')
        budget._amount(worst_case_microusd)
        enablement.fingerprint(configuration)
        self.db, self.intake, self.budget = intake.db, intake, budget
        self.enablement, self.configuration = enablement, dict(configuration)
        self.worst_case, self.clock = worst_case_microusd, clock
        self.db.execute('''CREATE TABLE IF NOT EXISTS execution_runs (
            run TEXT PRIMARY KEY, attempt INTEGER NOT NULL UNIQUE,
            request TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL)''')
        self.db.commit()

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not 0 <= value <= 253_402_300_798:
            raise ValueError('Trusted UTC clock required')
        return int(value)

    def prepare(self, job):
        if not self.enablement.enabled(self.configuration):
            return None
        now = self._now()
        current = datetime.fromtimestamp(now, timezone.utc)
        boundary = datetime(current.year + (current.month == 12),
                            1 if current.month == 12 else current.month + 1, 1, tzinfo=timezone.utc)
        timeout = min(300, int(boundary.timestamp()) - now)
        request = self.intake._claim(job, now, timeout, 'execution')
        if request is None:
            return None
        run = uuid.uuid4().hex
        request.update(run=run, period=current.strftime('%Y-%m'), spend_limit=self.worst_case)
        with self.db:
            self.db.execute('INSERT INTO execution_runs VALUES(?,?,?,\'preparing\',\'\')',
                            (run, request['attempt'], json.dumps(request, sort_keys=True)))
        if not self.budget.reserve(run, request['period'], self.worst_case):
            self._finish(run, False, 'budget_unavailable')
            return None
        if not self.can_continue(run):
            self._finish(run, False, 'disabled_or_expired')
            return None
        with self.db:
            self.db.execute("UPDATE execution_runs SET status='reserved' WHERE run=?", (run,))
        return dict(request)

    def can_continue(self, run):
        row = self.db.execute('''SELECT r.request,r.status,a.status,a.mode,b.status
            FROM execution_runs r JOIN attempts a ON a.id=r.attempt
            JOIN spend_reservations b ON b.run=r.run WHERE r.run=?''', (run,)).fetchone()
        return (row is not None and row[1] in {'preparing', 'reserved', 'running'} and row[2] == 'running'
                and row[3] == 'execution' and row[4] == 'reserved'
                and self._now() < json.loads(row[0])['deadline']
                and self.enablement.enabled(self.configuration))

    def execute(self, run, worker):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT request,status FROM execution_runs WHERE run=?', (run,)).fetchone()
            if row is None or row[1] != 'reserved' or not self.can_continue(run):
                raise ValueError('Fresh reserved execution required')
            self.db.execute("UPDATE execution_runs SET status='running' WHERE run=?", (run,))
        request = json.loads(row[0])
        try:
            return worker.run(request, request['deadline'] - self.clock(), lambda: self.can_continue(run))
        except ProcessStopped:
            with self.db:
                self.db.execute('''UPDATE execution_runs SET status='needs_human',reason='worker_stopped_cost_unknown'
                    WHERE run=?''', (run,))
                self.db.execute('''UPDATE jobs SET status='needs_human',reason='worker_stopped_cost_unknown'
                    WHERE id=(SELECT job FROM attempts WHERE id=?)''', (request['attempt'],))
            raise

    def finish(self, run, successful, actual_microusd=None):
        if type(successful) is not bool:
            raise ValueError('Explicit execution outcome required')
        row = self.db.execute('SELECT request,status FROM execution_runs WHERE run=?', (run,)).fetchone()
        if row is None or row[1] not in {'reserved', 'running'}:
            raise ValueError('Unknown or already reconciled execution')
        request = json.loads(row[0])
        reason = 'execution_complete'
        allowed = self.can_continue(run)
        if actual_microusd is None:
            reason = 'unknown_cost'
        else:
            self.budget.settle(run, actual_microusd)
            if actual_microusd > request['spend_limit']:
                reason = 'cost_overrun'
        if reason == 'execution_complete' and (not successful or not allowed):
            reason = 'execution_failed' if not successful else 'disabled_or_expired'
        self._finish(run, reason == 'execution_complete', reason)
        return reason

    def _finish(self, run, successful, reason):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT attempt FROM execution_runs WHERE run=?', (run,)).fetchone()
            if row is None:
                raise ValueError('Unknown execution')
            status = 'execution_complete' if successful else 'needs_human'
            self.db.execute('UPDATE execution_runs SET status=?,reason=? WHERE run=?', (status, reason, run))
            self.db.execute('UPDATE attempts SET status=? WHERE id=? AND mode=\'execution\'', (status, row[0]))
            self.db.execute('UPDATE jobs SET status=?,reason=? WHERE id=(SELECT job FROM attempts WHERE id=?)',
                            (status, reason, row[0]))
