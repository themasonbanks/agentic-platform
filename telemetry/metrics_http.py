"""Serve metric projections only to an authenticated scraper."""
from contextlib import closing
import hmac
import sqlite3
from telemetry.metrics import render_metrics


def create_metrics_application(connect, token, repositories):
    if (not isinstance(token, str) or not 32 <= len(token) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError('Explicit scraper credential required')
    repositories = tuple(repositories)

    def app(environ, start_response):
        status, body, content_type = '401 Unauthorized', b'request rejected\n', 'text/plain'
        if environ.get('PATH_INFO') != '/metrics':
            status = '404 Not Found'
        elif environ.get('REQUEST_METHOD') != 'GET':
            status = '405 Method Not Allowed'
        else:
            supplied = environ.get('HTTP_AUTHORIZATION', '')
            if isinstance(supplied, str) and supplied.isascii() and hmac.compare_digest(supplied, 'Bearer ' + token):
                try:
                    with closing(connect()) as db:
                        body = render_metrics(db, repositories).encode()
                    status = '200 OK'
                    content_type = 'text/plain; version=0.0.4; charset=utf-8'
                except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
                    status = '503 Service Unavailable'
                    body = b'metrics unavailable\n'
        start_response(status, [('Content-Type', content_type),
                                ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
        return [body]
    return app
