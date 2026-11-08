"""Expose signed review intake through a bounded WSGI endpoint."""

import json


class Webhook:
    def __init__(self, intake):
        self.intake = intake

    def __call__(self, environment, start_response):
        status, result = '404 Not Found', 'not_found'
        if environment.get('PATH_INFO') == '/webhooks/github':
            if environment.get('REQUEST_METHOD') != 'POST':
                status, result = '405 Method Not Allowed', 'method_not_allowed'
            elif environment.get('CONTENT_TYPE', '').split(';')[0].strip() != 'application/json':
                status, result = '415 Unsupported Media Type', 'json_required'
            else:
                try:
                    length = int(environment.get('CONTENT_LENGTH', ''))
                    if not 1 <= length <= 1_048_576:
                        status, result = '413 Content Too Large', 'size_limit'
                    else:
                        body = environment['wsgi.input'].read(length)
                        if len(body) != length:
                            status, result = '400 Bad Request', 'incomplete_body'
                        else:
                            result = self.intake.receive(
                                environment.get('HTTP_X_GITHUB_EVENT', ''),
                                environment.get('HTTP_X_HUB_SIGNATURE_256', ''), body)
                            status = '202 Accepted'
                except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
                    status, result = '400 Bad Request', 'invalid_delivery'
                except Exception:
                    status, result = '503 Service Unavailable', 'retry_later'
        response = json.dumps({'result': result}).encode()
        start_response(status, [('Content-Type', 'application/json'),
                                ('Content-Length', str(len(response))),
                                ('Cache-Control', 'no-store')])
        return [response]
