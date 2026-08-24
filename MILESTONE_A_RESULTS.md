# Milestone A, Step 0: measured reference results

CalculiX 2.21, Linux, 23 August 2026. Every number below was produced by running
the solver, not by recollection. Scripts to reproduce are in this folder.

## Problem

Prismatic cantilever, L = 200, H = 20, B = 20 mm, L/H = 10.
Steel, E = 210000 MPa, nu = 0.3. Uniform pressure 0.5 MPa on the top face,
line load q = 10 N/mm, total applied force 2000 N.

Analytical reference, Euler-Bernoulli plus Timoshenko shear:

| quantity | value |
|---|---|
| tip deflection, bending | 0.714286 mm |
| tip deflection, shear | 0.007429 mm (1.03 percent of total) |
| tip deflection, total | 0.721714 mm |
| root bending stress | 150.000 MPa |

## R1. The acceptance band for Milestone A, measured not assumed

| model | tip deflection | vs beam theory |
|---|---|---|
| C3D20R, 320 elements, fully clamped root | 0.714672 mm | 99.02 percent |
| C3D20R, same mesh, statically determinate support | 0.726395 mm | 100.65 percent |

The 0.98 percent deficit under a full clamp is the Poisson restraint at the root,
which beam theory does not model. Verified by replacing the clamp with
ux = 0 on the root face plus three point restraints: the deflection rises to
100.65 percent. Clamp stiffening measured at 1.63 percentage points.

**Milestone A acceptance band: 97 to 101 percent of beam theory** for tip
deflection, on a clamped-root L/H = 10 cantilever with quadratic or
incompatible-mode elements. Outside that band is a defect, not a modelling
assumption.

## R2. Shear locking, measured, with the aspect-ratio signature

Same geometry, same solver, same load. Only the element keyword changes.

| element | mesh | elements | tip deflection | vs theory |
|---|---|---|---|---|
| C3D8 | 20x2x2 | 80 | 0.625480 | 86.67 percent |
| C3D8 | 40x4x4 | 640 | 0.689512 | 95.54 percent |
| C3D8I | 20x2x2 | 80 | 0.710182 | 98.40 percent |
| C3D8I | 40x4x4 | 640 | 0.713422 | 98.85 percent |
| C3D8R | 40x4x4 | 640 | 0.761259 | 105.48 percent |
| C3D20R | 20x4x4 | 320 | 0.714672 | 99.02 percent |

Mechanism established, not assumed. Locking severity is controlled by the
element length-to-height ratio, so the two refinement directions behave
differently:

| refinement direction | elements | element L/h | vs theory |
|---|---|---|---|
| along the length, 10 to 80 divisions | 40 to 320 | 2.00 to 0.25 | 68.86 to 94.59 percent |
| through the depth, 2 to 16 divisions | 80 to 640 | 1.00 to 8.00 | 86.67 to 89.12 percent |

Through-depth refinement plateaus at 89.1 percent and does not converge. Eight
times the elements buys 2.45 percentage points. The one-keyword change to C3D8I
reaches 98.4 percent on the coarsest mesh, at one eighth the cost.

CalculiX issued no warning in any of these runs. Every job reported
"Job finished".

## R3. *CLOAD on an NSET applies the force to every node

A request for 100.0 N on a 9-node tip set produced a reaction sum of 900.0 N.
Converged, no warning. The writer therefore takes a TOTAL force, divides by the
member count, and records the divisor in the deck.

## R4. Reaction sums exclude loads applied to constrained nodes

`*NODE PRINT, NSET=<constrained>, TOTALS=ONLY, RF` omits external loads applied
to nodes inside that set. Measured against an applied 2000.000 N:

| case | reported RFy | shortfall | predicted from consistent nodal loads |
|---|---|---|---|
| C3D8, nx = 20 | 1950.000 | 50.000 | half the first slice, q dx / 2 |
| C3D8, nx = 40 | 1975.000 | 25.000 | same |
| C3D20R, nx = 10 | 1966.667 | 33.333 | one sixth of the slice, from -1/12 corners and +1/3 midsides |
| C3D20R, nx = 20 | 1983.333 | 16.667 | same |

All four predicted exactly. A naive equilibrium check reports a 0.83 to 2.50
percent imbalance on a correct model, and the size depends on the mesh, so no
fixed tolerance works. This only bites when the load set and the constraint set
intersect.

## R5. Two defects found in the checker itself, on the known-good case

Both would have shipped if only the known-bad case had been run.

1. **Sign error in the equilibrium correction.** The constrained-node load was
   added where it should have been subtracted. The broken check FAILED on the
   wrong-face case, which looked like a successful detection. It was passing for
   the wrong reason on a bad model while being wrong.
2. **Tolerance tighter than the data.** Load scaling used rtol = 1e-6. The .frd
   format stores displacements as E12.5, roughly six significant digits, so the
   resolution floor is about 1e-5. The check reported FAIL on a verified-correct
   model at a measured 4.86e-06 deviation. Tolerance is now set from the output
   precision and the reason is recorded in the docstring.

## R6. Falsification result: the invariants do not catch a wrong-face load

Section 12, item 6 of the v2 roadmap named this as the honest stress test of the
largest new idea. It fired.

Load moved from the tip face to the bottom face. Tip deflection changed from
1.903860 mm to 0.722193 mm, an error of 62 percent. The run converged.

| check | verdict |
|---|---|
| INV1 global equilibrium | PASS |
| INV2 zero load | PASS |
| INV3 load scaling | PASS |

Reference-free invariants catch bookkeeping errors, not placement errors. A
load applied anywhere is still reacted, still linear, still zero at zero load.
Input fidelity remains the job of the face catalogue and the confirmation step,
and `invariants.py` must not be sold as covering it.

What INV1 does catch, verified: a declared load that differs from the applied
load. Halving the declared resultant produced a FAIL.
