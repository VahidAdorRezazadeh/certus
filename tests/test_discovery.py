import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from certus import discovery

class DiscoveryTests(unittest.TestCase):
    def test_launcher_outside_path(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Path(folder) / 'abaqus.bat'
            launcher.write_text('@echo off')
            with patch.dict(os.environ, {'CERTUS_ABAQUS_COMMAND': str(launcher)}):
                self.assertEqual(discovery.find_abaqus(), str(launcher))

    def test_server_models_and_offline(self):
        response = io.BytesIO(json.dumps({'data': [{'id': 'local'}, {'id': 'local'}, {'id': ''}]}).encode())
        with patch('urllib.request.urlopen', return_value=response):
            self.assertEqual(discovery.server_models('http://localhost:1234/v1'), ['local'])
        with patch('urllib.request.urlopen', side_effect=OSError):
            self.assertEqual(discovery.server_models('http://localhost:1234/v1'), [])

    def test_installed_models_are_distinct_from_running_server(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / 'manifests/registry.ollama.ai/library/qwen/latest'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{}')
            with patch.dict(os.environ, {'OLLAMA_MODELS': folder}), patch.object(discovery, 'server_models', return_value=[]), patch('shutil.which', return_value=None):
                result = discovery.scan_local_models()
            self.assertEqual(result['ollama_installed'], ['qwen:latest'])
            self.assertTrue(all(not s['models'] for s in result['servers']))

if __name__ == '__main__':
    unittest.main()
