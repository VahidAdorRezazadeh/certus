# Milestone B benchmark results

CalculiX 2.21, 5 runs per case, checker.py on structured decks of one steel cantilever (case 12 through the Gmsh pipeline).
Verification in the sense of ASME V&V 10-2006: whether the model encodes the stated intent and is solved adequately, not validation against tests.

| # | seeded decision | expected finding | propagation | caught told (all/any) | caught inferred (all/any) | control quiet | FAILs on the correct deck | other FAILs on the control |
|---|---|---|---|---|---|---|---|---|
| 1 | plane stress where plane strain is required | PLANE STRESS/STRAIN | 9.9% | yes/yes | no/no | yes/yes | none | none |
| 2 | linear elastic material past yield | ELASTIC PAST YIELD | 28.5% | yes/yes | no/no | yes/yes | none | none |
| 3 | linear full-integration hex in bending (shear locking) | R3 | 30.6% | yes/yes | yes/yes | yes/yes | none | none |
| 4 | linear tet, near-incompressible (volumetric locking, no cure in CalculiX) | R1 | 99.1% | yes/yes | yes/yes | yes/yes | none | R3, R4 |
| 5 | reduced integration hourglassing in bending | R5 hourglass in bending | 3645.8% | yes/yes | yes/yes | yes/yes | none | none |
| 6 | load on a wrong but plausible face | none (blind spot) | 60.1% | no/no | no/no | yes/yes | none | none |
| 7 | full clamp where a pin is physical (overconstraint) | 8 SUPPORT REACTIONS | 75.3% | yes/yes | no/no | yes/yes | none | none |
| 8 | rigid-body mode not removed | 4 ZERO-ENERGY MODES | > 10^6 % (rigid-body drift) | yes/yes | yes/yes | yes/yes | none | none |
| 9 | 2D thickness convention in the reported force | 2D THICKNESS | 900.0% | yes/yes | no/no | yes/yes | none | none |
| 10 | unit inconsistency (E in GPa in an N-mm-MPa deck) | 1 LOAD vs INTENT | 99900.0% | yes/yes | no/no | yes/yes | none | none |
| 11 | symmetry BC under a non-symmetric load | SYMMETRY vs LOAD | 3.0% | yes/yes | yes/yes | yes/yes | none | none |
| 12 | mesh too coarse at a stress concentration, convergence gate | CONVERGENCE | 4.6% | yes/yes | yes/yes | yes/yes | none | none |

## Reading the table

- *told*: the stated intent includes the governing condition (yield stress, plane assumption, thickness, E, support type). *inferred*: that one condition is withheld. Checks that need a statement abstain (NOT EVALUATED) rather than guess, so 'no' in the inferred column is an abstention, not a silent pass.
- *control quiet*: the same rule does not fire on the matched control. *other FAILs on the control* are true findings on that deck (case 4's control is a linear-tet model, which R3 and R4 correctly flag).
- Five runs are identical: the checker is deterministic, so all-five equals at-least-once. The column is kept for the LLM edges, where it will differ.
- Case 4: no CalculiX element reaches the 97-101% band at nu = 0.4999 (measured: C3D8I 82.6%, C3D20 94.7%, C3D20R 95.9%); the missing hybrid formulation is a stack limit, so propagation is measured against beam theory.
- Case 6 is the known blind spot: a load on a plausible wrong face passes every check. It belongs to the face catalogue and the confirmation step.
- Case 12 runs through the Gmsh pipeline (Richardson study); the other eleven are structured decks, identical on every platform.

## Seed findings (run 1, told tier)

- **1**: stated plane strain, deck uses ['CPS8']; all seed FAILs: PLANE STRESS/STRAIN
- **2**: max von Mises 390.7 MPa exceeds the stated yield 250 MPa in a linear elastic run; the result is invalid past first yield; all seed FAILs: ELASTIC PAST YIELD
- **3**: linear hex elements with full integration under a bending load. Straight-edged linear elements cannot represent bending curvature and generate spurious shear strain. This is independent of nu = 0.3.; all seed FAILs: R3 shear locking (parasitic shear in bending), R7 insufficient through-thickness resolution in bending
- **4**: nu = 0.4999 with full integration, order 1. The volumetric constraint count grows relative to the available displacement degrees of freedom.; all seed FAILs: R1 volumetric locking (near-incompressible elastic), R3 shear locking (parasitic shear in bending), R4 constant-strain element (linear tetrahedron)
- **5**: 1.00 C3D8R elements through the 5 depth in bending (lever 100, depth 5); all seed FAILs: R7 insufficient through-thickness resolution in bending, R5 hourglass in bending, 2 SMALL STRAIN
- **6**: expected rule did not fire; all seed FAILs: none
- **7**: on END0: 50% of the normal reaction is the clamp PULLING the part; tangential/compressive reaction 1.54 (limit 0.3); all seed FAILs: 8 SUPPORT REACTIONS
- **8**: body of 189 nodes keeps 1 free rigid mode(s): -1Tz; all seed FAILs: 4 ZERO-ENERGY MODES
- **9**: deck: 20.83 N on thickness 1 = 20.83 N/mm; stated: 20.83 N on 10 mm = 2.083 N/mm (factor 10); all seed FAILs: 2D THICKNESS
- **10**: *ELASTIC E = 210, but 210 GPa in N-mm-MPa is 210000 (factor 1e+03); all seed FAILs: 1 LOAD vs INTENT, 2 SMALL STRAIN
- **11**: symmetry plane y = 0 but the load has 71% of its magnitude across it; all seed FAILs: SYMMETRY vs LOAD, R7 insufficient through-thickness resolution in bending, 1 LOAD vs INTENT
- **12**: not converging: changes +0.08629 then +2.187 do not shrink (49.4245 -> 49.5108 -> 51.6979). Typical of a singularity; all seed FAILs: CONVERGENCE
