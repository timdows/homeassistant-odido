"""Local cache lifecycle tests; no credentials or real account are used."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
store = importlib.import_module("session_store")
checker = importlib.import_module("local_check")


class StoreTests(unittest.TestCase):
    def test_store_is_account_bound_and_contains_no_username(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            store.save_session(path,'TEST@example.invalid',{'session':'fake-cookie'})
            self.assertEqual(store.load_session(path,'test@example.invalid'),{'session':'fake-cookie'})
            self.assertIsNone(store.load_session(path,'other@example.invalid'))
            self.assertNotIn('example.invalid',path.read_text())
            self.assertNotIn('password',path.read_text())
            store.clear_session(path)
            self.assertIsNone(store.load_session(path,'test@example.invalid'))

    def test_corrupt_or_incomplete_cache_does_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            for value in ('bad json','[]','{}'):
                path.write_text(value)
                self.assertIsNone(store.load_session(path,'test@example.invalid'))


class ReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_authentication_survives_parse_failure_and_next_run_skips_login(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            client=Mock()
            client.async_start_login=AsyncMock(return_value=True)
            client.async_submit_code=AsyncMock()
            client.async_get_usage=AsyncMock(side_effect=checker.OdidoError('missing usage'))
            client.export_cookies.return_value={'session':'fake-cookie'}
            client.debug_summary.return_value={}
            session=AsyncMock()
            with patch.object(checker,'SESSION_PATH',path), patch.object(checker,'ClientSession',return_value=session), patch.object(checker,'OdidoClient',return_value=client), patch('builtins.input',return_value='test@example.invalid'), patch('builtins.print'), patch.object(checker.getpass,'getpass',side_effect=['fake-password','1234']) as prompt:
                self.assertEqual(await checker.main(),1)
                self.assertTrue(path.exists())
                prompt.reset_mock()
                self.assertEqual(await checker.main(),1)
                prompt.assert_not_called()
                self.assertEqual(client.async_start_login.await_count,1)

    async def test_valid_cache_skips_password_and_otp(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            store.save_session(path,'test@example.invalid',{'session':'fake-cookie'})
            client=Mock()
            usage=Mock()
            usage.as_dict.return_value={'remaining_gb':7.5}
            client.async_get_usage=AsyncMock(return_value=usage)
            client.export_cookies.return_value={'session':'rotated-cookie'}
            session=AsyncMock()
            with patch.object(checker,'SESSION_PATH',path), patch.object(checker,'ClientSession',return_value=session), patch.object(checker,'OdidoClient',return_value=client), patch('builtins.input',return_value='test@example.invalid'), patch('builtins.print'), patch.object(checker.getpass,'getpass') as prompt:
                self.assertEqual(await checker.main(),0)
                prompt.assert_not_called()
            self.assertEqual(store.load_session(path,'test@example.invalid'),{'session':'rotated-cookie'})
