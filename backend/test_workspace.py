"""Same-origin entry and assets: public UI files must not expose the workspace."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import create_app


class WorkspaceTest(unittest.TestCase):
    def test_only_public_assets_are_served(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(Path(folder),start_worker=False)) as client:
                root=client.get('/')
                self.assertEqual(root.status_code,200)
                self.assertIn('<script src="live.js">',root.text)
                self.assertNotIn('href="design.html"',root.text)
                self.assertNotIn('id="load-video-review"',root.text)
                for asset in ['styles.css','app.js','live.js']:
                    self.assertEqual(client.get('/'+asset).status_code,200)
                for asset in ['design.html','offline-design.html','offline.html','README.md','start.py','config.local.json','server.stderr.log',
                              'data/reviews.sqlite3','backend/data/reviews.sqlite3','model-pilot/config.local.json']:
                    self.assertEqual(client.get('/'+asset).status_code,404)
                health=client.get('/api/health').json()
                self.assertEqual(health['version'],'0.2.0')
                self.assertTrue(health['workspace_connected'])


if __name__=='__main__':
    unittest.main()
