from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.test import SimpleTestCase


class StorageSettingsTests(SimpleTestCase):
    def test_default_storage_is_configured_for_uploaded_media(self):
        self.assertIn('default', settings.STORAGES)

        storage = storages['default']

        self.assertIsInstance(storage, FileSystemStorage)
        self.assertEqual(storage.location, str(settings.MEDIA_ROOT))
        self.assertEqual(storage.base_url, settings.MEDIA_URL)
