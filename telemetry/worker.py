"""Export one bounded outbox batch using operator-supplied runtime configuration."""
import json
import os
from pathlib import Path
import sqlite3
import stat
from telemetry.events import EventStore
from telemetry.otel import export_pending
from telemetry.otlp import OtlpExporter


def run_once(database, endpoint, authorization, expected_host, limit=100,
             exporter_factory=OtlpExporter):
    path = Path(database)
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
            or metadata.st_uid != os.getuid()):
        raise ValueError('Existing owner-only event database required')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Bounded export batch required')
    exporter = exporter_factory(endpoint, authorization, expected_host)
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
        try:
            db.execute('SELECT source,event_id,exported FROM engineering_events LIMIT 0')
            store = EventStore(db)
            delivered = export_pending(store, exporter, limit)
            remaining = db.execute('''SELECT COUNT(*) FROM engineering_events e WHERE exported=0
                AND NOT EXISTS(SELECT 1 FROM engineering_export_quarantine q
                               WHERE e.source=q.source AND e.event_id=q.event_id)''').fetchone()[0]
            quarantined = db.execute('SELECT COUNT(*) FROM engineering_export_quarantine').fetchone()[0]
        finally:
            db.close()
    finally:
        exporter.shutdown()
    return {'delivered': delivered, 'pending': remaining, 'quarantined': quarantined}


def main():
    try:
        result = run_once(os.environ['AGENTIC_TELEMETRY_DB'],
                          os.environ['AGENTIC_OTLP_TRACES_ENDPOINT'],
                          os.environ['AGENTIC_OTLP_AUTHORIZATION'],
                          os.environ['AGENTIC_OTLP_HOST'])
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        print(json.dumps({'status': 'unavailable'}))
        return 75
    print(json.dumps(result, sort_keys=True))
    return 75 if result['quarantined'] or result['pending'] and result['delivered'] < 100 else 0


if __name__ == '__main__':
    raise SystemExit(main())
