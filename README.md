# Certus prototype

Natural-language FEA pipeline with deterministic physics checks.
STEP in, CalculiX deck out, solve, report with a verdict that can fail.

## Run

    python cad_agent.py ...                                  # text or image -> part.step
    python model_agent.py part.step --solvers calculix --solve calculix --size 2.5
    python model_agent.py --cantilever --solvers calculix --solve calculix   # validation case

Every run writes `runs/<stamp>_<label>/` with `REPORT.txt` and `run.json` (not tracked).

## Files

| file | role |
|---|---|
| `cad_agent.py` | LLM writes build123d code, the solid is measured against the spec |
| `geom_session.py` | one Gmsh session: STEP import, face catalogue, selections, node sets |
| `geometry_features.py` | face catalogue, selectors, node sets and surfaces into the deck |
| `mesh_agent.py` | element choice and mesh size from the physics, retry loop |
| `locking_check.py` | locking rules R1 to R7 |
| `case_agent.py` | material, step, BCs, loads; dominant mode; overconstraint |
| `solvers.py` | solver capability table |
| `model_agent.py` | orchestrator and CLI |
| `results_check.py`, `frdread.py` | read .frd results, compare to a closed form |
| `cantilever.py` | the validation case and its closed form answer |
| `invariants.py` | the seven checks: load vs intent, small strain, penetration, rigid-mode rank test, reversibility, rate independence, increment convergence |
| `thickness.py` | member thickness and elements through it, measured on the mesh (R7) |
| `verify_sets.py` | reads a written deck back: did each node set land on the intended face |
| `run_dir.py` | run folder, REPORT.txt, run.json |
| `test_chain.py`, `test_checks.py`, `test_step3.py`, `test_step4.py`, `test_step6.py` | tests; each check has a seeded known-bad and a known-good case |
| `part.step`, `part_spec.json` | reference bracket and its spec |

`Literature/` and `Presentation/` hold the review and the deck.
Milestone A probe scripts (aspect study, soft clamp, CLOAD semantics) were removed
from the tree on 23 Sep 2026; they remain in commit `6847315`.
