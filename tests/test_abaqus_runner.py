from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from certus.abaqus_runner import run_abaqus

class AbaqusRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.deck = Path(self.temp.name) / 'case.inp'
        self.deck.write_text('*HEADING\nTest\n')

    def test_completed_run(self):
        def solve(args, **kwargs):
            folder = Path(kwargs['cwd'])
            (folder / 'certus_job.odb').write_bytes(b'test ODB fixture')
            self.assertIn('interactive', args)
            self.assertTrue((folder / 'input.inp').is_file())
            return SimpleNamespace(returncode=0, stdout='Abaqus JOB certus_job\nAbaqus ANALYSIS certus_job COMPLETED', stderr='')
        with patch('subprocess.run', side_effect=solve):
            result = run_abaqus(self.deck, launcher='abaqus')
        self.assertTrue(result.completed)
        self.assertTrue(Path(result.odb).is_file())

    def test_exit_zero_alone_is_not_success(self):
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
            self.assertFalse(run_abaqus(self.deck, launcher='abaqus').completed)

    def test_failed_job_with_odb_is_not_success(self):
        def fail(args, **kwargs):
            (Path(kwargs['cwd']) / 'certus_job.odb').write_bytes(b'partial')
            return SimpleNamespace(returncode=1, stdout='Abaqus ANALYSIS certus_job ABORTED', stderr='')
        with patch('subprocess.run', side_effect=fail):
            self.assertFalse(run_abaqus(self.deck, launcher='abaqus').completed)

    def test_missing_launcher_does_not_launch(self):
        with patch('certus.abaqus_runner.find_abaqus', return_value=None), patch('subprocess.run') as run:
            self.assertFalse(run_abaqus(self.deck).completed)
            run.assert_not_called()

    def test_include_requires_supported_staging(self):
        self.deck.write_text('*INCLUDE, INPUT=mesh.inp\n')
        with patch('subprocess.run') as run:
            self.assertFalse(run_abaqus(self.deck, launcher='abaqus').completed)
            run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
