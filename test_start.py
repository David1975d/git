"""Launcher checks: no real model calls or changes to saved tasks."""
import io
import unittest
from unittest.mock import patch, Mock
import start

HEALTH = {'service_id': 'xiyan-review', 'version': '0.2.0', 'workspace_connected': True}


class LauncherTest(unittest.TestCase):
    def test_existing_service_is_reused(self):
        with patch.object(start, 'health', return_value=HEALTH), patch.object(start, 'spawn_server') as spawn, patch.object(start.webbrowser, 'open') as browser:
            start.main()
            spawn.assert_not_called()
            browser.assert_called_once_with(start.URL)

    def test_occupied_port_is_not_overwritten(self):
        with patch.object(start, 'health', return_value=None), patch.object(start, 'port_in_use', return_value=True), patch.object(start, 'spawn_server') as spawn:
            with self.assertRaisesRegex(RuntimeError, '端口已被占用'):
                start.main(False)
            spawn.assert_not_called()

    def test_incompatible_service_is_rejected(self):
        with patch.object(start, 'health', return_value={'service_id': 'other'}):
            with self.assertRaisesRegex(RuntimeError, 'different/older'):
                start.main(False)

    @unittest.skipUnless(start.os.name == 'nt', 'Windows lifecycle flags')
    def test_breakaway_and_denied_fallback(self):
        with patch.object(start.subprocess, 'Popen', return_value=Mock()) as popen:
            start.spawn_server('python.exe', io.BytesIO(), io.BytesIO())
            self.assertTrue(popen.call_args.kwargs['creationflags'] & start.subprocess.CREATE_BREAKAWAY_FROM_JOB)
        denied = OSError('access denied')
        denied.winerror = 5
        with patch.object(start.subprocess, 'Popen', side_effect=[denied, Mock()]) as popen:
            start.spawn_server('python.exe', io.BytesIO(), io.BytesIO())
            self.assertEqual(popen.call_count, 2)
            self.assertFalse(popen.call_args.kwargs['creationflags'] & start.subprocess.CREATE_BREAKAWAY_FROM_JOB)


if __name__ == '__main__':
    unittest.main()
