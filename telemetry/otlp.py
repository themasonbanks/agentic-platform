"""Export SDK spans as OTLP protobuf to one explicitly configured destination."""
from urllib.parse import urlsplit
import requests
from google.protobuf.message import DecodeError
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class OtlpExporter(SpanExporter):
    def __init__(self, endpoint, authorization, expected_host, *, local_fixture=False):
        url = urlsplit(endpoint)
        local = local_fixture and url.scheme == 'http' and url.hostname == '127.0.0.1' and not authorization
        if (not expected_host or url.hostname != expected_host or url.username or url.password
                or url.query or url.fragment or not url.path.endswith('/v1/traces')
                or (not local and (url.scheme != 'https' or url.port not in (None, 443)))
                or (not local and (not isinstance(authorization, str) or not authorization
                                   or len(authorization) > 8192
                                   or any(ord(c) < 32 or ord(c) > 126 for c in authorization)))):
            raise ValueError('Explicit HTTPS traces endpoint and credential required')
        self.endpoint = endpoint
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Content-Type': 'application/x-protobuf'})
        if authorization:
            self.session.headers['Authorization'] = authorization
        self.closed = False
        self.permanent_failure = False

    def export(self, spans):
        self.permanent_failure = False
        if self.closed:
            return SpanExportResult.FAILURE
        body = encode_spans(spans).SerializeToString()
        if len(body) > 1_048_576:
            self.permanent_failure = True
            return SpanExportResult.FAILURE
        try:
            with self.session.post(self.endpoint, data=body, timeout=(3, 5),
                                   allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    self.permanent_failure = response.status_code not in {429, 502, 503, 504}
                    return SpanExportResult.FAILURE
                result = bytearray()
                for chunk in response.iter_content(4096):
                    result.extend(chunk)
                    if len(result) > 65536:
                        self.permanent_failure = True
                        return SpanExportResult.FAILURE
                acknowledgement = ExportTraceServiceResponse.FromString(bytes(result))
                if acknowledgement.partial_success.rejected_spans:
                    self.permanent_failure = True
                    return SpanExportResult.FAILURE
        except (requests.RequestException, DecodeError):
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.closed = True
        self.session.close()
