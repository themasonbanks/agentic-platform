"""Allocate durable per-call token caps from an already reserved execution budget."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class InferencePolicy:
    model: str
    price_reference: str
    input_microusd_per_million: int
    output_microusd_per_million: int
    max_input_tokens: int
    max_output_tokens: int
    max_calls: int
    valid_until: int

    def __post_init__(self):
        for value in (self.model, self.price_reference):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
                raise ValueError('Trusted explicit model and immutable price reference required')
        for amount in (self.input_microusd_per_million, self.output_microusd_per_million):
            if type(amount) is not int or not 0 < amount <= 1_000_000_000_000:
                raise ValueError('Known bounded integer token prices required')
        for value, low, high in ((self.max_input_tokens, 1, 1_000_000),
                                 (self.max_output_tokens, 16, 1_000_000), (self.max_calls, 1, 20),
                                 (self.valid_until, 1, 253_402_300_798)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Bounded token/call limits and policy expiry required')

    def fingerprint(self):
        return 'sha256:' + hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class InferenceAllowance:
    def __init__(self, supervisor, policy):
        if not isinstance(policy, InferencePolicy) or supervisor.configuration['budget'] != policy.fingerprint():
            raise ValueError('Inference policy must match the owner-approved budget reference')
        self.supervisor, self.policy, self.db = supervisor, policy, supervisor.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS inference_calls (
            identity TEXT PRIMARY KEY,run TEXT NOT NULL,digest TEXT NOT NULL,policy TEXT NOT NULL,
            input_limit INTEGER NOT NULL,output_limit INTEGER NOT NULL,reserved INTEGER NOT NULL,
            status TEXT NOT NULL,input_used INTEGER,output_used INTEGER)''')
        self.db.commit()

    def _active(self, run):
        row = self.db.execute('SELECT request,status FROM execution_runs WHERE run=?', (run,)).fetchone()
        return (row is not None and row[1] == 'running'
                and self.supervisor._now() < self.policy.valid_until
                and json.loads(row[0])['deadline'] <= self.policy.valid_until
                and self.supervisor.can_continue(run))

    def reserve(self, run, identity, digest, input_tokens, output_tokens):
        """Reserve a unique call using trusted exact input count and configured prices."""
        if (not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity)
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or type(input_tokens) is not int or not 0 <= input_tokens <= self.policy.max_input_tokens
                or type(output_tokens) is not int or not 16 <= output_tokens <= self.policy.max_output_tokens):
            raise ValueError('Unique call identity, request digest and bounded trusted token counts required')
        cost = (input_tokens * self.policy.input_microusd_per_million
                + output_tokens * self.policy.output_microusd_per_million + 999999) // 1_000_000
        self.supervisor.budget._amount(cost)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self._active(run) or self.db.execute('SELECT 1 FROM inference_calls WHERE identity=?', (identity,)).fetchone():
                return False
            limit = self.db.execute("SELECT reserved FROM spend_reservations WHERE run=? AND status='reserved'",
                                    (run,)).fetchone()
            count, used = self.db.execute("SELECT count(*),COALESCE(SUM(reserved),0) FROM inference_calls "
                                          "WHERE run=? AND status!='cancelled'", (run,)).fetchone()
            if limit is None or count >= self.policy.max_calls or used + cost > limit[0]:
                return False
            self.db.execute('INSERT INTO inference_calls VALUES(?,?,?,?,?,?,?,\'reserved\',NULL,NULL)',
                            (identity, run, digest, self.policy.fingerprint(), input_tokens, output_tokens, cost))
        return True

    def dispatch(self, identity, digest):
        """Claim one dispatch after fresh execution, expiry and request-binding checks."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT run,digest,policy,status FROM inference_calls WHERE identity=?', (identity,)).fetchone()
            if (row is None or row[1] != digest or row[2] != self.policy.fingerprint()
                    or row[3] != 'reserved' or not self._active(row[0])):
                return False
            self.db.execute("UPDATE inference_calls SET status='dispatched' WHERE identity=?", (identity,))
        return True

    def cancel(self, identity):
        """Cancel only a reservation that has never been dispatched."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT status FROM inference_calls WHERE identity=?', (identity,)).fetchone()
            if row != ('reserved',):
                raise ValueError('Only undispatched calls can be cancelled')
            self.db.execute("UPDATE inference_calls SET status='cancelled' WHERE identity=?", (identity,))

    def complete(self, identity, input_tokens, output_tokens):
        """Record trusted provider usage without releasing worst-case call capacity."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT run,input_limit,output_limit,status FROM inference_calls WHERE identity=?',
                                  (identity,)).fetchone()
            if row is None or row[3] != 'dispatched':
                raise ValueError('One dispatched call required')
            valid = all(type(value) is int and 0 <= value <= 9_223_372_036_854_775_807
                        for value in (input_tokens, output_tokens))
            status = ('complete' if input_tokens <= row[1] and output_tokens <= row[2] else 'overrun') if valid else 'unknown'
            self.db.execute('UPDATE inference_calls SET status=?,input_used=?,output_used=? WHERE identity=?',
                            (status, input_tokens if valid else None, output_tokens if valid else None, identity))
            if status != 'complete':
                self.supervisor._hold(row[0], 'inference_usage_overrun' if status == 'overrun' else 'inference_unknown')
        return status == 'complete'

    def uncertain(self, identity):
        """Hold the run after an ambiguous dispatched request; never retry or refund it."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT run,status FROM inference_calls WHERE identity=?', (identity,)).fetchone()
            if row is None or row[1] != 'dispatched':
                raise ValueError('One dispatched call required')
            self.db.execute("UPDATE inference_calls SET status='unknown' WHERE identity=?", (identity,))
            self.supervisor._hold(row[0], 'inference_unknown')
