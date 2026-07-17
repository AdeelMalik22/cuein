import json
import logging
import uuid
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.logging import JsonFormatter, RequestIdFilter, RequestIdMiddleware


class RequestIdMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_valid_request_id_is_propagated_to_the_response(self):
        request_id = 'a6bdf6e8-0c8c-4f8c-a2e4-43c8be58ad40'
        captured = []
        middleware = RequestIdMiddleware(lambda request: captured.append(request.request_id) or HttpResponse())

        response = middleware(self.factory.get('/', HTTP_X_REQUEST_ID=request_id))

        self.assertEqual(captured, [request_id])
        self.assertEqual(response['X-Request-ID'], request_id)

    @patch('core.logging.uuid.uuid4', return_value=uuid.UUID('8e48e0c5-bfcb-432f-a0f9-f2233524f3c3'))
    def test_invalid_request_id_is_replaced(self, generate_request_id):
        captured = []
        middleware = RequestIdMiddleware(lambda request: captured.append(request.request_id) or HttpResponse())

        response = middleware(self.factory.get('/', HTTP_X_REQUEST_ID='not-a-valid-request-id'))

        self.assertEqual(captured, ['8e48e0c5-bfcb-432f-a0f9-f2233524f3c3'])
        self.assertEqual(response['X-Request-ID'], '8e48e0c5-bfcb-432f-a0f9-f2233524f3c3')
        generate_request_id.assert_called_once_with()


class JsonLoggingTests(SimpleTestCase):
    def test_filter_and_formatter_include_a_request_id_without_request_contents(self):
        record = logging.LogRecord(
            name='cuein.audit',
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='Updated lead %s',
            args=('lead-42',),
            exc_info=None,
        )
        record.request_id = '7a8bf95a-1c76-4d12-ab35-391cbaa273c5'

        self.assertTrue(RequestIdFilter().filter(record))
        event = json.loads(JsonFormatter().format(record))

        self.assertEqual(event['level'], 'INFO')
        self.assertEqual(event['logger'], 'cuein.audit')
        self.assertEqual(event['message'], 'Updated lead lead-42')
        self.assertEqual(event['request_id'], '7a8bf95a-1c76-4d12-ab35-391cbaa273c5')
        self.assertTrue(event['timestamp'].endswith('Z'))
