from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.template.loader import get_template


class NotificationPresentationTests(SimpleTestCase):
    def test_notification_controls_have_page_specific_layout_classes(self):
        get_template('web/notification_list.html')
        template = (settings.BASE_DIR / 'web/templates/web/notification_list.html').read_text()
        stylesheet = (settings.BASE_DIR / 'web/static/web/app.css').read_text()

        self.assertIn('class="notification-overview"', template)
        self.assertIn('class="notification-filter-tabs"', template)
        self.assertIn('class="notification-filter-tab', template)
        self.assertIn('body.notification-page .notification-overview', stylesheet)
        self.assertIn('body.notification-page .notification-filter-tabs', stylesheet)
        self.assertIn('body.notification-page .notification-filter-tab', stylesheet)
