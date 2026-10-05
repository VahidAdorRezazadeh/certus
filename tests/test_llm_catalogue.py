"""Catalogue discovery: account results, missing configuration and API failure."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from certus import llm


class ClaudeCatalogueTests(unittest.TestCase):
    def test_missing_key_refuses_discovery(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'ANTHROPIC_API_KEY'):
                llm.list_claude_models()

    def test_uses_only_returned_models(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.list.return_value = [SimpleNamespace(id=x) for x in ['b', 'a', 'b', '']]
        sdk = SimpleNamespace(Anthropic=MagicMock(return_value=client))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}), patch.dict(sys.modules, {'anthropic': sdk}):
            self.assertEqual(llm.list_claude_models(), ['a', 'b'])
        client.models.list.assert_called_once_with(limit=100)

    def test_api_failure_reports_no_invented_catalogue(self):
        sdk = SimpleNamespace(Anthropic=MagicMock(side_effect=RuntimeError('private error detail')))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}), patch.dict(sys.modules, {'anthropic': sdk}):
            with self.assertRaisesRegex(RuntimeError, 'Could not retrieve') as error:
                llm.list_claude_models()
            self.assertNotIn('private error detail', str(error.exception))


if __name__ == '__main__':
    unittest.main()
