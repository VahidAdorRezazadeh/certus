# Certus prototype

Natural-language FEA pipeline with deterministic physics checks.
STEP in, CalculiX deck out, solve, report with a verdict that can fail.

## Layout

    Certus/                  workspace, not a git repo
    ├── certus/              THIS repo: the product
    │   ├── src/certus/      the Python package
    │   ├── tests/           test_*.py, each check with a seeded bad and a good case
    │   ├── benchmark/       Milestone B, 12 cases
    │   ├── examples/        reference bracket part.step + part_spec.json
    │   ├── docs/            results notes
    │   └── pyproject.toml
    ├── runs/                every run writes here (override: CERTUS_RUNS)
    ├── experiments/  website/  presentations/  literature/  design/

Papers, decks and design files are not in git: they are not ours to publish.

## Install (once, inside the environment that has gmsh and ccx)

    cd Certus\certus
    pip install -e ".[all]"      # editable: code changes apply without reinstalling

## GUI (local web page)

    certus-gui                   # or: python -m streamlit run src/certus/app.py

Prompt (plus an optional sketch, or your own STEP) -> what was understood ->
part built and measured -> faces picked in 3D -> missing values asked ->
CalculiX solve -> verdict, answer, pictures, report. The language model is
chosen in the sidebar: Ollama or LM Studio on this computer (OpenAI-compatible
server), or the Claude API. It only reads words; every number it puts in the
form must be quoted from the prompt with its unit, or it is dropped and asked.

## Command line

    python -m certus.cad_agent ...                               # text or image -> part.step
    certus examples/part.step --solvers calculix --solve calculix --size 2.5
    certus --cantilever --solvers calculix --solve calculix      # validation case, -0.69 %
    certus examples/part.step --solvers calculix --solve calculix --converge 4,2,1
    python -m certus.checker deck.inp                            # check a deck someone else wrote

## Tests (from the repo folder)

    python tests/test_checks.py                  # the seven checks: seed, solve, verdict
    python tests/test_gui.py                     # GUI end to end against a stub model
    python -m benchmark.run_benchmark --runs 5   # Milestone B, 12 cases

Every run writes `<workspace>/runs/<stamp>_<label>/` with `REPORT.txt` and `run.json`.

## Files

| file (in `src/certus/`) | role |
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
| `checker.py` | deck in, findings out: plane/thickness/symmetry/locking on the deck, the seven checks, support reactions, elastic past yield |
| `../../benchmark/` (repo root) | Milestone B: 12 seeded cases on one cantilever, five runs, `RESULTS.md` |
| `thickness.py` | member thickness and elements through it, measured on the mesh (R7) |
| `verify_sets.py` | reads a written deck back: did each node set land on the intended face |
| `run_dir.py` | run folder, REPORT.txt, run.json |
| `app.py` | the GUI (Streamlit), six stages |
| `llm.py` | the one door to a language model: Claude API or an OpenAI-compatible local server |
| `intent.py` | prompt -> draft intent form; values kept only if quoted from the prompt with a matching unit |
| `viewer.py` | 3D face picker and result plots (Plotly); no physics |
| `paths.py` | where runs and examples live; the only place that decides it |
| `cli.py` | the `certus` and `certus-gui` commands |

Milestone A probe scripts (aspect study, soft clamp, CLOAD semantics) were removed
from the tree on 23 Sep 2026; they remain in commit `75a6ef0`.
