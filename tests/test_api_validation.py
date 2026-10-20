"""La autenticación de Deepgram no debe exigir permisos de administración."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QByteArray, QCoreApplication, QEvent
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

from app.ui.views.main_window import SentryWindow


class DeepgramValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.window = SentryWindow(Path(self.folder.name) / 'audit.db')
        self.window.transcription_api_key.setText('sentry-test-key')

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.folder.cleanup()

    def reply(self, status, payload, error=QNetworkReply.NetworkError.NoError):
        reply = Mock()
        reply.attribute.return_value = status
        reply.readAll.return_value = QByteArray(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        reply.error.return_value = error
        return reply

    def test_validation_uses_the_auth_endpoint_instead_of_projects(self):
        with patch.object(self.window, '_send_api_request') as send:
            self.window._validate_api('transcription')
        send.assert_called_once_with('transcription', 'deepgram', 'sentry-test-key',
                                     'https://api.deepgram.com/v1/auth/token', 'validate')

    def test_pasted_token_prefix_is_removed_before_validation_and_storage(self):
        self.window.transcription_api_key.setText('  Token sentry-test-key  ')
        with patch.object(self.window, '_send_api_request') as send:
            self.window._validate_api('transcription')
        self.assertEqual(send.call_args.args[2], 'sentry-test-key')
        self.assertEqual(self.window.transcription_api_key.text(), 'sentry-test-key')

    def test_authenticated_key_is_saved_without_a_model_catalog_request(self):
        reply = self.reply(200, {'project_id': 'example', 'key': {'scopes': ['usage:write']}})
        with patch.object(self.window, '_send_api_request') as send, \
                patch('app.ui.views.main_window.protect', return_value='encrypted-test'):
            self.window._finish_api_request(reply, 'transcription', 'deepgram', 'sentry-test-key', 'validate')
        send.assert_not_called()
        self.assertEqual(self.window.transcription_validate.text(), 'Validada')
        self.assertEqual(self.window.transcription_api_status.property('state'), 'valid')
        stored = self.window.database.credentials()['transcription']
        self.assertEqual((stored['provider'], stored['model'], stored['encrypted_key']),
                         ('deepgram', 'nova-3', 'encrypted-test'))
        self.assertTrue(self.window.transcription_api_key.isEnabled())

    def test_permission_denial_is_not_reported_as_an_invalid_key(self):
        reply = self.reply(403, {'err_msg': 'sentry-test-key forbidden'},
                           QNetworkReply.NetworkError.ContentAccessDenied)
        self.window._finish_api_request(reply, 'transcription', 'deepgram', 'sentry-test-key', 'validate')
        message = self.window.transcription_api_status.text()
        self.assertIn('403', message)
        self.assertIn('permisos', message)
        self.assertNotIn('Clave rechazada', message)
        self.assertNotIn('sentry-test-key', message)
        self.assertEqual(self.window.database.credentials(), {})

    def test_invalid_key_stays_rejected_and_can_be_retried(self):
        reply = self.reply(401, b'Invalid credentials.', QNetworkReply.NetworkError.AuthenticationRequiredError)
        self.window._finish_api_request(reply, 'transcription', 'deepgram', 'sentry-test-key', 'validate')
        self.assertIn('401', self.window.transcription_api_status.text())
        self.assertEqual(self.window.transcription_validate.text(), 'Reintentar')
        self.assertTrue(self.window.transcription_validate.isEnabled())
        self.assertEqual(self.window.database.credentials(), {})

    def test_unreadable_auth_response_does_not_save_the_key(self):
        for payload in (b'not JSON', b'[]', b'{}'):
            with self.subTest(payload=payload), patch.object(self.window, '_send_api_request'):
                self.window._finish_api_request(self.reply(200, payload), 'transcription', 'deepgram',
                                                'sentry-test-key', 'validate')
                self.assertEqual(self.window.transcription_validate.text(), 'Reintentar')
                self.assertEqual(self.window.database.credentials(), {})

    def test_authorization_header_uses_token_scheme(self):
        with patch.object(self.window.network, 'get', return_value=Mock()) as get:
            self.window._send_api_request('transcription', 'deepgram', 'sentry-test-key',
                                          'https://api.deepgram.com/v1/auth/token', 'validate')
        request = get.call_args.args[0]
        self.assertEqual(bytes(request.rawHeader('Authorization')), b'Token sentry-test-key')
