"""Send durable metadata events through the OpenTelemetry SDK without raw payloads."""
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class ConfirmingExporter(SpanExporter):
    def __init__(self, target):
        self.target, self.result = target, None

    def export(self, spans):
        self.result = self.target.export(spans)
        return self.result


def export_pending(store, exporter, limit=100):
    confirmed = ConfirmingExporter(exporter)
    provider = TracerProvider(resource=Resource({'service.name': 'agentic-platform'}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(confirmed))
    tracer = provider.get_tracer('agentic.engineering', '1')
    delivered = 0
    for event in store.pending(limit):
        parent = trace.SpanContext(
            trace_id=int(event['trace_id'], 16), span_id=int(event['trace_id'][16:], 16) or 1,
            is_remote=True, trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED))
        attributes = {'repository': event['repository'], 'issue': event['issue'],
                      'source': event['source'], 'event_id': event['event_id'],
                      'event_span_key': event['span_id'], 'cost_known': event['cost_known'],
                      'usage_known': event['usage_known']}
        attributes.update({k: v for k, v in event['attributes'].items() if v is not None})
        confirmed.result = None
        span = tracer.start_span('engineering.' + event['source'] + '.' + event['kind'],
                                 context=trace.set_span_in_context(trace.NonRecordingSpan(parent)),
                                 start_time=event['observed_ns'], attributes=attributes)
        if event['kind'] == 'failure':
            span.set_status(trace.StatusCode.ERROR)
        duration = event['attributes'].get('duration_ms') or 0
        span.end(end_time=event['observed_ns'] + max(duration * 1_000_000, 1))
        if confirmed.result != SpanExportResult.SUCCESS:
            break
        store.acknowledge(event['source'], event['event_id'])
        delivered += 1
    return delivered
