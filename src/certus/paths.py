"""
paths.py - the one place that says where files live.

    <workspace>/                  not a git repo
        certus/                   the git repo (this package is certus/src/certus)
            examples/             reference inputs, versioned
        runs/                     every run writes here, never into the repo

Override the output folder with the environment variable CERTUS_RUNS.
If the package is installed without the repo (not editable), there is no
workspace to find, so runs go to ./runs under the current folder.
"""
from __future__ import annotations
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
IN_REPO = (REPO / "pyproject.toml").exists()
WORKSPACE = REPO.parent if IN_REPO else Path.cwd()

RUNS = Path(os.environ.get("CERTUS_RUNS") or WORKSPACE / "runs")
EXAMPLES = REPO / "examples"
EXAMPLE_STEP = EXAMPLES / "part.step"          # reference bracket
EXAMPLE_SPEC = EXAMPLES / "part_spec.json"


def runs_dir(*parts: str) -> str:
    """A folder under RUNS, created on first use."""
    p = RUNS.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
