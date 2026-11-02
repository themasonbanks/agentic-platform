"""Expose bounded webhook requests to authenticated telemetry receivers."""
import sqlite3


def application(receivers):
    """Build a WSGI app from path-to-receiver callbacks returning durable results."""
    def app(environ, start_response):
        def reply(status):
            body = b'accepted\n' if status == '200 OK' else b'request rejected\n'
            start_response(status, [('Content-Type', 'text/plain'),
                                    ('Content-Length', str(len(body)))])
            return [body]

        receiver = receivers.get(environ.get('PATH_INFO'))
        if receiver is None:
            return reply('404 Not Found')
        if environ.get('REQUEST_METHOD') != 'POST':
            return reply('405 Method Not Allowed')
        if environ.get('CONTENT_TYPE', '').split(';')[0].strip().lower() != 'application/json':
            return reply('415 Unsupported Media Type')
        length = environ.get('CONTENT_LENGTH', '')
        if not isinstance(length, str) or not length.isascii() or not length.isdecimal():
            return reply('400 Bad Request')
        if len(length) > 7 or not 0 < int(length) <= 1_048_576:
            return reply('413 Payload Too Large')
        try:
            body = environ['wsgi.input'].read(int(length))
            if len(body) != int(length):
                return reply('400 Bad Request')
            receiver(body, environ)
        except (ValueError, KeyError, TypeError, UnicodeError):
            return reply('400 Bad Request')
        except (sqlite3.Error, OSError):
            return reply('503 Service Unavailable')
        return reply('200 OK')
    return app
