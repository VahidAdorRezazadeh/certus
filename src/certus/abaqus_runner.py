"""First-stage Abaqus execution adapter. Does not issue a physics verdict.

Runs a standalone generated input deck in a fresh directory. Relative INCLUDE
files are not supported by this initial adapter. ODB extraction is a later stage.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Optional

from certus.discovery import find_abaqus


@dataclass(frozen=True)
class AbaqusRun:
    completed: bool
    directory: Optional[str]
    odb: Optional[str]
    reason: str


def run_abaqus(deck_path, launcher=None, cpus=1, timeout=3600):
    """Run a standalone deck; completion is execution evidence, not correctness."""
    deck = Path(deck_path).resolve()
    if not deck.is_file():
        return AbaqusRun(False, None, None, 'Input deck does not exist.')
    if not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1:
        raise ValueError('cpus must be a positive integer')
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    if re.search(r'^\s*\*INCLUDE\b', deck.read_text(errors='replace'), re.I | re.M):
        return AbaqusRun(False, None, None, 'This initial adapter requires a standalone deck without INCLUDE files.')
    executable = launcher or find_abaqus()
    if not executable:
        return AbaqusRun(False, None, None, 'Abaqus launcher not found.')
    folder = Path(tempfile.mkdtemp(prefix='abaqus_', dir=deck.parent))
    shutil.copyfile(deck, folder / 'input.inp')
    args = [str(executable), 'job=certus_job', 'input=input.inp',
            f'cpus={cpus}', 'interactive']
    # Fixed job and input names also avoid passing user-controlled shell syntax
    # to Windows Abaqus batch launchers.
    try:
        process = subprocess.run(args, cwd=folder, capture_output=True,
                                 text=True, errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return AbaqusRun(False, str(folder), None,
                         'Launcher timed out. Solver child processes may still be running; check Abaqus before retrying.')
    except OSError as exc:
        return AbaqusRun(False, str(folder), None, f'Could not launch Abaqus: {exc}')
    output = (process.stdout or '') + (process.stderr or '')
    (folder / 'launcher.log').write_text(output, encoding='utf-8')
    job_log = folder / 'certus_job.log'
    if job_log.is_file():
        output += '\n' + job_log.read_text(errors='replace')
    odb = folder / 'certus_job.odb'
    completed = (process.returncode == 0 and odb.is_file() and odb.stat().st_size > 0
                 and bool(re.search(r'\bANALYSIS\s+certus_job\s+COMPLETED\b', output, re.I)))
    if not completed:
        return AbaqusRun(False, str(folder), None,
                         'No confirmed completed analysis with an ODB. Inspect launcher.log and Abaqus job files.')
    return AbaqusRun(True, str(folder), str(odb),
                     'Analysis completed. ODB extraction and physics checks have not run.')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('deck')
    parser.add_argument('--launcher')
    parser.add_argument('--cpus', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    result = run_abaqus(args.deck, args.launcher, args.cpus, args.timeout)
    print(result.reason)
    if result.directory:
        print('Run folder:', result.directory)
    raise SystemExit(0 if result.completed else 1)


if __name__ == '__main__':
    main()
