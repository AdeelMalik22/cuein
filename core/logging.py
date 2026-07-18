"""Request correlation and safe JSON logging helpers."""

import json
import logging
import uuid
from datetime import datetime, timezone


class RequestIdMiddleware:
    """Attach a validated correlation ID to each request and response."""

    header_name = 'HTTP_X_REQUEST_ID'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = self._request_id(request.META.get(self.header_name))
        request.request_id = request_id

        response = self.get_response(request)
        response['X-Request-ID'] = request_id
        return response

    @staticmethod
    def _request_id(value: str | None) -> str:
        try:
            return str(uuid.UUID(value))
        except (AttributeError, TypeError, ValueError):
            return str(uuid.uuid4())


class RequestIdFilter(logging.Filter):
    """Expose the request correlation ID to every configured formatter."""

    def filter(self, record):
        request = getattr(record, 'request', None)
        request_id = getattr(request, 'request_id', None) or getattr(record, 'request_id', None)
        record.request_id = request_id or '-'
        return True


class JsonFormatter(logging.Formatter):
    """Emit a compact, machine-readable log event without request contents."""

    def format(self, record):
        event = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec='milliseconds',
            ).replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        request_id = getattr(record, 'request_id', None)
        if request_id and request_id != '-':
            event['request_id'] = request_id
        if record.exc_info:
            event['exception'] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False)
