from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class HealthEndpointTests(SimpleTestCase):
    def test_liveness_does_not_require_dependencies(self):
        response = self.client.get(reverse('healthz'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    @patch('cuein.health._cache_is_available', return_value=True)
    @patch('cuein.health._database_is_available', return_value=True)
    def test_readiness_reports_success_when_dependencies_are_available(self, database_check, cache_check):
        response = self.client.get(reverse('readyz'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'status': 'ok', 'checks': {'database': 'ok', 'cache': 'ok'}},
        )
        database_check.assert_called_once_with()
        cache_check.assert_called_once_with()

    @patch('cuein.health._cache_is_available', return_value=False)
    @patch('cuein.health._database_is_available', return_value=True)
    def test_readiness_reports_unavailable_dependencies(self, database_check, cache_check):
        response = self.client.get(reverse('readyz'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {'status': 'unavailable', 'checks': {'database': 'ok', 'cache': 'unavailable'}},
        )
        database_check.assert_called_once_with()
        cache_check.assert_called_once_with()
