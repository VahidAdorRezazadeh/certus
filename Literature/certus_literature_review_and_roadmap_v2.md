# Certus: literature review and roadmap, v2
### Revised 19 August 2026 after audit against an independent full-corpus review
### Supersedes v1 (same date). Sources: 19 papers in project knowledge, 3 source repositories, 1 independent 51-page corpus review

Confidence tags: **[Certain]** = I can point to the document text or code in this session.
**[Likely]** = published evidence exists but I did not verify it here. **[Guessing]** = plausible
from mechanism, unverified.

---

## 0. Audit result: what changed and why

The independent review found real gaps in v1. Four of my claims needed correcting, one of them in
a direction that weakens my own argument. Three findings of mine survive that the independent
review does not contain, because it reviewed papers and I read source code.

### 0.1 Corrections to v1. All four are things I got wrong or overstated.

**Correction 1, and the most important. I overstated the PDE-Agents causal story.** [Certain]

v1 said: "the one group that built real deterministic physics checks measured what enforcing them
costs, found it costs success rate, and responded by taking them off the critical path."

The numbers are right. KG On (checks mandatory) = 94%, KG Smart (checks lazy) = 100%. Warning-induced
conservatism is one of three named mechanisms. But **the paper's own failure analysis attributes the
three KG On failures to iteration-budget exhaustion and timeout, not to warnings causing
termination.** Their contribution (iv) says so explicitly: "a failure analysis tracing KG On's 3
systematic failures to budget exhaustion and timeout, establishing warm-start injection as the
dominant factor in KG Smart's reliability advantage."

The honest version, which is still strong and still usable: *the checks were moved off the critical
path, and one of the three stated reasons is that a correct pre-solve physics warning triggers a
termination where it should trigger a modification.* Do not claim the measured penalty was caused by
the warnings. That is not what their failure analysis says, and a reviewer who reads the paper will
catch it. The design consequence for Certus is unchanged and, if anything, sharper: **a check that
costs iterations is as dangerous to adoption as a check that stops a run.** Certus findings must be
cheap as well as actionable.

**Correction 2. I over-weighted the ENGDESIGN mechanical-systems number.** [Certain, verified in
Table 1]

v1 quoted "the best score any model achieves on mechanical systems is 28.57%" as evidence. ENGDESIGN
Table 1 shows Mechanical Systems contains **7 tasks** (6 open, 1 closed) with 32 rubrics. So 28.57%
is 2 tasks out of 7, three trials each. That is not a usable statistic. Structure Design has 13
tasks, which is only slightly better. The whole benchmark is 101 tasks and 473 rubrics across nine
domains, built from 186 proposals.

The claim that survives is weaker and I should have made it in the first place: ENGDESIGN's
contribution is the **evaluation paradigm**, not the mechanics numbers. It replaces answer-checking
with executable simulation-driven grading, and it is one of the few benchmarks that reports
robustness (fraction solved in all three trials over fraction solved in any trial: about 0.62 for
o1, 0.61 for o3, 0.57 for o4-mini-high). Cite the paradigm and the robustness instability. Drop the
28.57%.

**Correction 3. ALL-FEM's 71.79% is the two-agent result, and the paper contradicts itself about
this.** [Certain]

The abstract says "Embedded in a multi-agent workflow with runtime feedback, the best fine-tuned
model (GPT OSS 120B) achieves code-level success of 71.79%." The results text says the opposite:
71.79% appears in the comparison of **fine-tuned two-agent frameworks** (GPT-OSS 120B FT 71.79% vs
Llama 3.3 70B FT 20.51% vs Qwen 3 32B FT 12.82%). The independent review reports the multi-agent
figure as 76.92%, or 30 of 39, taken from Figures 5 and 6; those figures are images and I could not
verify the number from extracted text. [Likely]

Two consequences that matter more than the discrepancy:

- **Base GPT-OSS 120B, no fine-tuning, solves 58.97% (23 of 39).** Fine-tuning adds five problems.
  [Certain]
- **Non-agentic GPT-5 Thinking, two zero-shot attempts, solves 27 of 39 on expert-designed
  computational mechanics problems.** [Certain from the text; count from the independent review's
  figure reading, so [Likely] on the exact 27]

That second number is a warning aimed squarely at Certus. A frontier model with **no** agentic
scaffolding, no fine-tuning and no verification gets roughly 69% of expert FEM problems right. It
makes falsification test 2 in section 12 more urgent, not less.

**Correction 4. ALL-FEM is not autonomous, and I did not say so.** [Certain] The Admin agent exists
because "the Coordinator does not terminate the process" on its own; a human ends the loop. The
human is not reported to supply technical corrections, but the system does not stop by itself. The
same pattern recurs across the corpus: Guo's LLM-CAE required a human to supply a missing weighted
mass matrix; ATHENA reports human quadrature and wavelet interventions; AgenticSciML requires human
approval of the evaluator before search begins.

This is a **selling point I missed entirely**, and section 10.2 now develops it.

### 0.2 Six things the independent review caught that v1 did not have

| # | Finding | Why it matters to Certus |
|---|---|---|
| 1 | **VFEAgent boundary-condition detection = 0.600, connectivity F1 = 0.648, while Stage-B execution = 1.000** | The single best number in the entire corpus for the Certus thesis. See 3.1. |
| 2 | **Geng: Gemini 2.5 Pro produces 88.5% executable code and 37% correct models; GPT-4o 0% correct** | A second, independent execution-versus-correctness pair, on structural models. |
| 3 | **AutoFEA exists, uses CalculiX, and evaluates by exact `.dat` comparison at rtol 1e-4, atol 1e-6** | The closest system to Certus's stack, and the strictest numerical metric in the corpus. v1 omitted it entirely. See 2.3. |
| 4 | **AutoFEA's cantilever case: GPT-4o answers 0.0975 from linear theory where plasticity gives 0.8738** | A published, worked example of Certus negative-benchmark case 6. External validation of a case I proposed. |
| 5 | **MechAgents reports a total reaction force from a 2D model without resolving the thickness convention** | A whole error class I did not have: dimensional and 2D-convention consistency of reported quantities. |
| 6 | **Metamorphic and invariance testing as an automatic verification family** | The largest technical addition in this revision. See section 6. |

### 0.3 Three findings of mine the independent review does not contain

All three come from reading source code rather than papers. [Certain, from source]

1. **FeaGPT's public `_create_physical_groups` assigns boundary conditions by Gmsh surface
   enumeration index.** The semantic location string is used only as a label. Its deck writer
   references node sets `NFIX` and `NLOAD` that are never defined anywhere in the repository.
2. **MooseAgent's only deterministic check is `check_app`**, which extracts `type = <Name>` and
   reports names absent from the documentation JSON. That is API-existence checking and nothing else.
3. **FEABench's "physics metrics" contain no physics.** `physics_code_metrics` in
   `common/eval/api_score.py` measures interface realism, interface code recall, feature code recall
   and correct dimension features, all as **string and API-call recall against ground-truth COMSOL
   Java code**.

The independent review also has no commercial landscape. SimPilot, SimScale, Cosmon and COMSOL's
copilot do not appear in it, because they do not appear in academic bibliographies. Keep both halves.

---

## 1. What the field is, as of August 2026

**Every published agentic FEA or CFD system optimises for the same objective: get a simulation to
run and finish.** The metrics are executability, schema validity, job lifecycle completion, and
percentage of benchmark problems that produce output. Where correctness is measured, it is measured
**offline, by a human or an LLM judge, against a reference the researchers prepared in advance**. No
published system carries a correctness judgement into a run on a problem where no reference exists.
[Certain, across all systems reviewed]

Three groups have now named the gap in their own published words. This is the core of the Certus
argument and it is not a claim of novelty, which is better, because it is checkable.

- **ALL-FEM** (Purdue, CMAME 457:118985, 2026): "a compilable code does not guarantee code
  correctness. Since the LLM receives no feedback on the numerical approach, it cannot correct
  formulation or implementation errors that still produce executable code."
- **Tian and Zhang** (Canterbury, arXiv 2408.13406): "a verification-validation gap where executable
  but physically incorrect code passed undetected," and "No agent combination successfully validated
  constitutive relations in complex tasks."
- **Baker, Rafferty and Price** (Queen's Belfast, *Big Data Cogn. Comput.* 9(12):305, 2025):
  "Current methods often rely on general NLP metrics or geometric similarity scores that fail to
  capture the functional and physical validity of a design. Future work should focus on creating
  comprehensive benchmarks that test for manufacturability, physical plausibility, and adherence to
  engineering principles."

**One qualification on Baker that I did not make in v1.** Baker excludes arXiv, so the review
systematically omits the fastest-moving agentic CAE literature. The independent review also notes
internal reporting tensions: 575 records after duplicate removal in one place and 122 unique records
in another, plus peer-reviewed eligibility framing alongside a large share of preprints. [Likely,
from the independent review; I did not verify the specific page numbers] Treat Baker's prevalence
statistics as indicative, not precise. The conclusion on benchmarks stands, and arguably stands
stronger: even a conservative, publication-lagged sample says physical-validity benchmarks do not
exist.

### 1.1 Five clusters

**A. End-to-end pipeline automation.** FeaGPT (CalculiX), MooseAgent (MOOSE), Foam-Agent 2.0
(OpenFOAM), VFEAgent (Abaqus), Geng et al. (OpenSeesPy), AutoFEA (CalculiX), MechAgents (FEniCS).
Metric: executable success rate.

**B. Model specialisation.** ALL-FEM. 503 expert seed codes expanded to about 1,004 through a
multi-LLM augmentation pipeline with domain-expert validation, then LoRA/QLoRA fine-tuning from 3B
to 120B. The most serious engineering effort in the set.

**C. Benchmarks.** FEM-Bench, FEABench, ENGDESIGN, ALL-FEM's 39, Geng's 20, Foam-Agent's 110,
AutoFEA's 512. See section 7.

**D. Vision papers.** Guo, Park, Qian, Hughes and Liu, CMAME 450:118591 (2026), plus Guo's
Northwestern dissertation (the uploaded copy is a 24-page preview containing only front matter and
Chapter 1, so it cannot support a methodological review of the missing chapters). **The
field-defining paper by the two biggest names in computational mechanics is not about setup
verification at all.** It is about using LLMs to automate derivation and implementation of intrusive
model order reduction (TAPS), so a hard-to-write class of solver becomes accessible. Their
hallucination concern is LLMs deriving wrong weak forms; their fix is chain-of-thought with curated
examples. [Certain]

Have an answer ready for why setup verification does not appear in the field's flagship vision. My
read: they write for researchers who build their own solvers and already know what plane strain
means. Certus is for people who do not. [Guessing]

**E. Method-discovery agents.** AgenticSciML (Jiang and Karniadakis, npj Artificial Intelligence
2026) and ATHENA (Toscano, Chen, Karniadakis, Brown). Adjacent, not competing. Both frame their
contributions in formal language that outruns the evidence: ATHENA's "contextual bandit" policy is
an LLM scaffold, not a learned decision rule, and its "submartingale-like" regret is an empirical
plot, not a bound. That is a useful caution about how Certus should phrase its own claims.

### 1.2 One item outside all clusters

Ghorbani et al., *Computers and Geotechnics* 174:106657 (2024). Surrogate-guided Monte Carlo tree
search for calibrating soil constitutive models and inverse FE analyses. No LLM. The only paper in
the set that automates a *judgement* about material parameters rather than a workflow. Note that
"AlphaZero-inspired" overstates the correspondence: there is no self-play policy/value learning
loop. Read properly only if Certus ever adds material-model validity checks beyond range-testing.

---

## 2. The verification ladder, revised

v1 had nine levels covering error classes. The independent review has a seven-rung V&V ladder
aligned to ASME practice, which adds two rungs mine lacked: predictive uncertainty and operational
assurance. Both are correct additions. The merged ladder:

| Level | What it checks | Fails when |
|---|---|---|
| **L0 Execution** | Solver starts and exits without error | The job crashes |
| **L1 Schema** | Deck parses, required fields present, types correct | Missing keyword, bad type |
| **L2 API existence** | Every keyword or function used actually exists | Hallucinated API call |
| **L3 Parameter range** | Values positive, finite, inside a plausible band | Negative Poisson's ratio |
| **L4 Input fidelity** | The model reflects what the user specified | BC on the wrong face; dropped dimension |
| **L5 Discretization adequacy** | Element formulation and mesh can represent this response | Shear locking, volumetric locking, hourglassing, unresolved wall |
| **L6 Idealization validity** | The abstraction matches the physics asked for | Plane strain for plane stress; elastic past yield; overconstraint; unresolved 2D thickness convention |
| **L7 Solution verification** | Discretization and iterative error controlled; invariants hold | No convergence study; equilibrium not closed; load scaling nonlinear in a linear run |
| **L8 Post-solve closure** | Result consistent with the assumptions that produced it | Stress exceeds yield in a linear run and nobody notices |
| **L9 Predictive uncertainty** | Claim carries parameter, numerical and model-form uncertainty | A single deterministic number presented as the answer |
| **L10 Refusal** | System declines when no valid configuration exists on its stack | Answers anyway |

L0 to L3 catch errors that announce themselves. **L4 to L9 catch errors that run cleanly, converge
and return plausible numbers.** L10 is a policy, and every published system designs against it.

L9 is new and I am **not** recommending Certus build it now. It is recorded because it is genuinely
unoccupied and because a customer in a regulated industry will ask.

### 2.1 Every system against the ladder

| System | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L10 |
|---|---|---|---|---|---|---|---|---|---|---|
| FeaGPT | yes | yes | AST | yes | **no (index order)** | partial | no | no | no | no |
| MooseAgent | yes | yes | yes | no | no | no | no | no | no | no |
| Foam-Agent 2.0 | yes | yes | yes | no | no | no | no | no | no | no |
| VFEAgent | yes | yes | AST | yes | **0.600 measured** | no | no | no | no | **anti** |
| Geng et al. | yes | yes | yes | no | partial (graph invariants) | n/a (beams) | no | no | no | no |
| AutoFEA | yes | yes | yes | no | no | no | no | **exact regression only** | no | no |
| ALL-FEM | yes | yes | yes | no | offline only | no | offline only | no | no | no |
| MechAgents | yes | yes | — | no | no | no | **fails (thickness)** | no | no | no |
| PDE-Agents | yes | yes | yes | yes | no | partial (fixed thresholds) | no | offline once | partial (Tscore) | **anti** |
| Tian and Zhang | yes | yes | — | no | no | no | **measured and failed** | no | no | no |
| FEABench | yes | yes | yes | no | no | no | no | target value only | no | no |
| FEM-Bench | yes | yes | — | — | — | — | code level only | **unit level: yes** | — | — |
| **Certus (today)** | **no** | yes | — | — | yes | designed, one rule defective | partial | **not built** | designed | designed |

"anti" means a deliberate mechanism to prevent refusal. VFEAgent's deterministic fallback exists to
guarantee an answer is always produced, and is used in 0% of its cases. PDE-Agents moved pre-run
checks off the mandatory path.

### 2.2 What the checks actually are, in code

Verified from source, not from papers. [Certain]

**FeaGPT.** `feagpt/geometry/validators.py` has three layers described as syntax, physics and
manufacturability. The "physics" layer checks that dimension fields are numeric, positive, above
0.1 mm and below 10000 mm. The syntax layer checks the geometry type against a hard-coded whitelist
of eight strings. `feagpt/meshing/quality.py` computes aspect ratio (threshold 10) and a Jacobian
quality metric (threshold 0.3).

`feagpt/meshing/mesher.py::_create_physical_groups` assigns boundary conditions by enumeration
index:

```python
for i, bc in enumerate(bcs):
    loc = bc.get("location", f"bc_{i}").replace(" ", "_")
    if i < len(surfaces):
        gmsh.model.addPhysicalGroup(2, [surfaces[i][1]], name=loc)
for i, load in enumerate(loads):
    idx = len(bcs) + i
    if idx < len(surfaces):
        gmsh.model.addPhysicalGroup(2, [surfaces[idx][1]], name=loc)
```

The semantic string the paper describes as intelligently inferred is used only as a name.

**Caveat.** This repository's LICENSE reads "Copyright (c) 2026 naividh," which does not match the
paper's authors (Qi, Xu, Chu). I cannot confirm it is their code. Either way, the leading public
artefact called FeaGPT selects boundary faces by index order.

**MooseAgent.** One deterministic check, `check_app` in `src/mooseagent/utils.py`. Extracts
`type = <Name>` and reports names absent from the documentation JSON. L2 only. Its LICENSE reads
"Copyright (c) 2024 LangChain," unedited, which makes the grant on their own code ambiguous.

**FEABench.** `physics_code_metrics` measures API-call recall against ground-truth COMSOL Java code.
`evaluate_bench.py` scores Executability, ExportedValue and TargetAccuracy, where TargetAccuracy is
relative error against a target value from a COMSOL tutorial. The harness needs a COMSOL licence.

### 2.3 AutoFEA, which v1 omitted and which matters most for the stack decision

Hou, Johnson, Makhija, Chen and Ye (Notre Dame, AAAI 2025). [Certain]

- 512 official CalculiX regression test cases, decomposed into 4,792 steps, average 9.36 steps per
  project, range 3 to 16.
- GPT-4o generates natural-language descriptions of each. Three random 90/10 splits.
- A plan graph whose nodes are steps and whose edges encode shared keywords, with a two-layer GCN
  plus Transformer attention doing link prediction to retrieve relevant example code.
- **Evaluation compares the `.dat` file from the generated deck against the `.dat` from the original
  deck at relative tolerance 1e-4 and absolute tolerance 1e-6.** This is the strictest numerical
  criterion anywhere in the corpus.
- Success: GPT-4o 90.2%, Gemini 1.5 Pro 83.8%, GPT-4o-mini 81.6%, Llama 3 8B 78.8%, Gemini Flash
  75.2%.

Three consequences.

**It is the closest system to Certus's stack.** CalculiX, deck generation, node sets, element sets,
steps. If you want to know what "the field can already do on your solver," this is the answer.

**Its 90.2% means something much narrower than it sounds.** It reproduces held-out cases from the
official CalculiX regression suite, with geometry and mesh blocks provided or truncated when longer
than ten lines. Random step-level or case-level splitting can leak near-identical templates across
train and test unless families are grouped, which the paper does not do. It measures template
reproduction, not model formulation.

**Its cantilever case study is a published instance of Certus negative-benchmark case 6.** GPT-4o
computed a free-end displacement of about 0.0975 units from linear theory; the material response was
plastic and the correct value was about 0.8738 units, roughly a factor of nine. The paper frames
this as hallucination corrected by running the simulation. Read it as an L6 idealization failure:
elastic model used past yield, plausible number, wrong by an order of magnitude, caught only because
someone ran the real analysis. Put it in the benchmark and cite it.

---

## 3. Nine findings that change how Certus should be built

### 3.1 The best drawing-to-FEA system detects boundary conditions 60% of the time and reports 100% execution success

VFEAgent, Table 1, Stage-A interpretation performance. [Certain, verified in the table]

| Model | Schema validity | Node accuracy | Connectivity F1 | **BC detection** | Overall |
|---|---|---|---|---|---|
| **VFEAgent** | **0.900** | **0.815** | 0.648 | **0.600** | **0.704** |
| Gemini-3-Pro | 0.417 | 0.775 | 0.577 | 0.458 | 0.639 |
| GPT-5 | 0.333 | 0.756 | **0.609** | 0.542 | 0.610 |
| GPT-4o | 0.583 | 0.750 | 0.488 | 0.500 | 0.596 |
| Gemini-3-Flash | 0.333 | 0.752 | 0.576 | 0.458 | 0.583 |
| Grok-4 | 0.545 | 0.582 | 0.500 | 0.500 | 0.503 |
| Qwen-3-Max | 0.091 | 0.127 | 0.024 | 0.136 | 0.128 |

Stage B, code generation and execution, reports **1.0 across all main metrics on all 15 cases**.

**This is the strongest single piece of evidence Certus has, and v1 did not contain it.** A
state-of-the-art multimodal FEA agent, published in 2026, gets the boundary conditions right on
three cases out of five, and then reports perfect execution. Every one of those wrong boundary
conditions produces an Abaqus job that runs, converges and returns a stress field.

Three uses.

1. **The one-sentence pitch line.** "The best published drawing-to-FEA system detects boundary
   conditions with 60% accuracy and reports 100% execution success. Those two numbers describe the
   same system, in the same paper, on the same fifteen cases."
2. **It quantifies the L0-to-L4 gap** that the whole Certus thesis rests on, with a number, from a
   competitor, in a table.
3. **It is the strongest available argument for Certus's face catalogue.** Boundary condition
   placement is the measured weak point of the state of the art, and it is the thing Certus's
   deterministic selector plus confirmation step is built to do.

Caveats to state honestly if challenged: 15 cases; largely 2D or extruded profiles; the metric is
extraction accuracy against expert annotation, not downstream response error. The paper does not
propagate an extraction error to a stress value. That propagation study is unoccupied work and would
make the number far more damning.

### 3.2 A second, independent execution-versus-correctness pair

Geng et al., verified. [Certain]

- Gemini 2.5 Pro: **88.5% of 200 runs execute without runtime errors, 37% average model accuracy**,
  with per-problem accuracy ranging from 90% down to 0%.
- GPT-4o: **0% accuracy across all 200 runs.**
- Their own five-agent Llama-3.3 70B system: over 80% on most problems, 100% on three-bay cases,
  88% average on five-bay, and 70/70/60% on the three hardest frames.
- Their ablation: **merging the geometry agent and the code-generation agent yields 0%; keeping them
  separate yields 100%** on a three-frame, ten-run test. [Likely, from the independent review; the
  ablation is described in the paper but I read the number from that review]
- Error taxonomy for the vanilla baseline: 61% element errors, 27.5% node errors, 7% support errors,
  4.5% material errors.

Two things follow. First, 88.5% executable against 37% correct is a second measurement of the same
gap, on structural models rather than drawings. Second, the merge ablation is direct causal evidence
for Certus's architectural rule: **separating semantic reasoning from artefact construction, with
deterministic invariants enforced in code, is worth the difference between 0% and 100% on that
test.** That is not an opinion about architecture. It is a measured effect.

### 3.3 The one directly comparable experiment says LLM verification does not work

Tian and Zhang: 1,120 controlled trials, seven role configurations, four linear-elastic FEA tasks,
12-turn limit, AutoGen, temperature 0, seed 42. [Certain]

- The Rebuttal agent, explicitly prompted to be adversarial, affirmed the Critic in 85 to 92% of
  opportunities. Task 1: 39 of 43 interventions (90.7%). Of the Critic's interventions in failed
  cases, 30.8% contained explicitly incorrect diagnoses, and the Rebuttal agent affirmed them
  anyway. Fisher's exact test across configurations p = 1.000.
- **On Task 2, Coder-Executor produced executable code in every run while achieving 0% task
  success.** [Likely, from the independent review's reading of their results section]
- Coder-Executor-Critic produced only two visually successful results on each of Tasks 3 and 4, and
  **none passed the physical audit**. No configuration validated constitutive relations.
- Adding a redundant reviewer reduced success.
- A generated plane-stress constitutive equation mixed incompatible coefficients; because it was
  executable and visually plausible, only the physical audit exposed it.

**Caveat that v1 omitted and that a reviewer will raise: the model is GPT-3.5 Turbo.** That is old.
The affirmation-bias mechanism is about role prompting and shared base models, which is architectural
rather than capability-limited, so the finding plausibly transfers. But do not present it as
established for 2026 frontier models. State the model when you cite it. If asked whether it
replicates on a current model, the honest answer is that nobody has run it, and that running it
would be a cheap and publishable contribution.

**Consequence:** the deterministic-versus-LLM split in the project instructions is now backed by
1,120 trials from an independent group. Do not weaken it. When a funded competitor ships "an AI
reviewer agent," this is the paper that says why it will not work.

### 3.4 The strongest concrete silent error in the literature is ALL-FEM's, and their catch method is unavailable at runtime

ALL-FEM's correctness evaluation: discard non-executable code; plot the candidate against the
reference and call them equal if indistinguishable at plot scale; then run an LLM judge over the
code diff and manually verify what it flags. Worked example: Llama 3.3 70B produced "a solution plot
indistinguishable from the reference," and code-level analysis revealed **plane strain where plane
stress was required**. [Certain]

Their stated reason for not using relative L2 error: different frameworks produce solutions on
different meshes, and projection between them is hard to automate robustly and introduces errors
that obscure true discrepancies. That independently confirms the Certus principle that **benchmarks
must assert on the finding, never on a raw number** (Certus's own platform variance: 84,584 elements
on Linux against 84,600 on Windows at identical target size).

The catch required a reference solution, an LLM judge and a human. None of those exists on a user's
part. That is the runtime gap.

### 3.5 The measurability trap, and the second trap underneath it

PDE-Agents Table 6: steady-state Dirichlet cases where 86%, 64% and 100% material-property errors
produce **zero** output error, because those solutions are analytically independent of k, rho and
c_p. [Certain]

First consequence: seed only errors that provably propagate to the quantity of interest, and report
the magnitude, or the false-negative rate is meaningless.

Second consequence, from their limitations section: "The narrow scope is also what makes our central
measurement possible. Because the agent fills in parameters for a fixed, independently verified
solver instead of emitting solver code, every run produces a configuration whose material properties
can be checked directly against ground truth... A system that generates solver code covers far
broader physics but cannot isolate property fidelity the same way, since a failure may lie in the
code, in the parameters, or in both." [Certain]

**Certus generates decks over free geometry, so by their argument Certus cannot copy their benchmark
design.** Section 7.3 gives the design that survives.

### 3.6 Reference-free verification is the largest unoccupied technical space, and v1 missed it

This is the most valuable addition in this revision. [Certain that the family is absent from all
reviewed systems; the invariants themselves are standard V&V practice]

Every correctness check in the corpus needs one of three things Certus will not have on a customer's
part: a reference solution, a ground-truth deck to diff against, or a human expert. But a large
family of physics checks needs **none of them**. They are computable from the model and its own
results, at runtime, on a part nobody has ever analysed:

| Invariant | Test | Catches |
|---|---|---|
| Load scaling | Double every applied load, re-solve, displacements and reactions must double exactly | Hidden nonlinearity, unintended contact, a nonlinear material silently active in a "linear" run |
| Global equilibrium | Sum of reaction forces and moments must equal applied load resultant to solver tolerance | Wrong load direction, dropped load, constraint absorbing load, unit inconsistency |
| Rigid-body rotation | Rotate geometry, loads and BCs rigidly; invariants (von Mises, principal values, strain energy) must be unchanged | Coordinate-frame errors, anisotropy applied in the wrong frame, orientation bugs |
| Zero load | Remove all applied loads and prescribed displacements; response must be identically zero | Spurious loads, residual initial state, wrong reference configuration |
| Rigid-body modes | Free-free eigenvalue count must be exactly six in 3D, and the constrained model must have none | Under-constraint solved anyway; over-constraint |
| Energy balance | External work must equal internal strain energy in a static linear run | Constraint doing work, missing reaction path |
| Patch test | Constant-strain patch on the actual generated element type must reproduce constant stress | Element formulation defect, degenerate elements, locking symptoms |
| Refinement rate | Two or three mesh levels; the quantity of interest must approach an asymptote at the expected rate | Under-resolution, singular BC, unconverged answer presented as converged |
| Limiting case | Drive a parameter to a limit with a known analytical answer and compare | Whole-model errors that survive every local check |
| Dimensional closure | Units and, in 2D, the thickness convention must be resolved before any force or energy is reported | The MechAgents failure: a total reaction in newtons from a 2D model with unresolved thickness |

**Nobody in the corpus does this in an agentic pipeline.** FEM-Bench does it at the unit-function
level, in generated pytest tests, which is why its expected-failure design is worth stealing.
PDE-Agents does a refinement study once, offline, to verify their own solver. Certus can do it
per-run, per-part.

Three reasons this is more valuable than v1's framing:

1. **It solves the measurability trap.** These checks do not need ground truth, so they work exactly
   where PDE-Agents says measurement becomes impossible.
2. **It is cheap.** Load scaling and zero load are one extra solve each. Equilibrium is a sum over
   reaction forces. Dimensional closure is free. Correction 1 above says a check that costs
   iterations is as dangerous as one that stops a run, so cheapness is a design requirement.
3. **It is general.** It works on any part, any load case, any solver, without a locking rule or a
   geometry catalogue. It is the foundation the locking rules sit on top of, not a competitor to
   them.

Add this as a new module. Provisional name `invariants.py`, sitting beside `locking_check.py`, with
the same finding schema and the same severity ladder.

### 3.7 The bottleneck is missing domain knowledge, not reasoning

FEM-Bench, GEPA appendix. Light and medium prompt optimisation produced generic advice with no
improvement. Heavy optimisation enabled Gemini and GPT-5 to solve the held-out critical-load tiers
**only after the evolved prompt incorporated the full 12x12 geometric stiffness matrix and
force-extraction conventions**. Their tier system (T0 no helpers needed, T1 all helpers supplied, T2
a subset, T3 helpers withheld that the reference uses) isolates this cleanly: assemble-global-
geometric-stiffness T3, elastic-critical-load T3 and local-geometric-stiffness T0 are **never solved
by any model**. [Certain]

Two consequences.

**Benchmark design.** The T0/T1/T2/T3 helper-tier pattern is directly transferable. Certus's negative
benchmark should modulate difficulty by withholding a named domain-knowledge object, not by making
the geometry more complicated. "Does the system catch volumetric locking when told the material is
near-incompressible" and "does it catch it when it must infer near-incompressibility from
nu = 0.499" are different tiers of the same case.

**Positioning.** Certus's deterministic rules encode exactly the kind of knowledge object that
closes these gaps. That is a defensible framing: the moat is not reasoning, it is a curated set of
adjudication objects that no amount of prompting substitutes for.

### 3.8 Reproducibility is a live problem, and Certus already treats it as a requirement

FEM-Bench five-run evaluation: Gemini 3 Pro solves 30 tasks at least once and 26 in all five runs.
**Claude Opus 4.5 solves 29 at least once but only 16 in all five.** GPT-5 solves 28 at least once
and 19 in all five. [Certain] ENGDESIGN's robustness ratio (all three trials over any trial) is
about 0.57 to 0.62 for the best reasoning models. [Certain]

Almost half of Claude Opus's successes are not repeatable. Any Certus claim about catching an error
class must be a claim about repeated runs, and Certus's existing requirement to assert on findings
rather than numbers is now supported by two independent benchmarks measuring the same instability.

### 3.9 Where the field's costs actually sit

ALL-FEM's cost appendix, on Sample Problem 1: the two-agent system uses 45,776 tokens and six
minutes; the multi-agent system uses 716,436 tokens and eleven minutes, for a gain of two medium
tasks across the whole benchmark. [Likely, from the independent review's reading of the appendix]
AgenticSciML reports 5.6 GPU hours against 1.7 LLM hours on one task. [Likely, same source]

The lesson for Certus: **the expensive part is the solving, not the reasoning.** A verification layer
that adds two cheap extra solves (load doubling, zero load) is a small marginal cost on a workflow
already dominated by solver time, and it is far cheaper than an LLM reviewer that adds hundreds of
thousands of tokens. That is a defensible engineering argument and a cost argument at the same time.

---

## 4. Honest inventory: what they have that Certus does not

Ranked by how much it should worry you.

**1. A working solve. All of them.** Certus has never executed a solver. Every claim in this
document is unverifiable from the Certus side until this closes.

**2. Public benchmarks with released data.** ALL-FEM's 39 with reference solutions. FEM-Bench's 33
with a fully automated re-runnable pipeline, expected-failure implementations and helper tiers.
FEABench Gold and Large (200 problems). ENGDESIGN on HuggingFace, 101 tasks, 473 rubrics. AutoFEA's
512 CalculiX cases. Foam-Agent's 110. Geng's 20. Certus has zero published test cases.

**3. Fine-tuned domain models.** ALL-FEM's 1,004-entry corpus and LoRA/QLoRA stack. Roughly a
person-year of graduate labour Certus should not try to replicate.

**4. Knowledge bases.** MooseAgent's 8,000+ annotated MOOSE inputs. PDE-Agents' Neo4j GraphRAG with
materials, known issues and run lineage. Foam-Agent's four stage-specific FAISS indices.
AgenticSciML's 70-technique curated base.

**5. Multimodal input.** VFEAgent: 90.0% schema validity against 41.7% for the best VLM baseline.

**6. Downstream product surface.** FeaGPT ships fatigue, Pareto, sensitivity, surrogates, 432-case
batch sweeps, a REST API and a Cloud Run deployment script. Foam-Agent 2.0 exposes itself as MCP
tools so Claude Code can drive it, generates Slurm scripts, ran a one-million-cell cavity on
Perlmutter across 32 subdomains, and does ParaView post-processing. Certus writes a deck.

**7. Published formal V&V.** PDE-Agents: O(h^2), rates 2.04 and 2.00 against closed-form solutions,
anchored to ASME V&V 10-2006.

**8. Strict numerical evaluation.** AutoFEA's exact `.dat` comparison at rtol 1e-4.

**9. Teams and institutions.** Purdue, Brown, Peking, Northwestern with UT Austin, Google DeepMind,
IFE Norway, Notre Dame, Nuclear Power Institute of China, Boston University, RPI, Miami, MIT.
Certus is one person with one to two days a week.

---

## 5. What Certus has that they do not

Verified against source or paper text. [Certain unless marked]

**1. Deterministic face selection on arbitrary CAD, with a confirmation step.** How each system gets
a boundary condition onto a surface:

- FeaGPT: eight-type whitelist, faces chosen by Gmsh enumeration index.
- VFEAgent: 2D orthographic drawings, extruded profiles; **measured BC detection 0.600**; their own
  limitations name single-view 3D reconstruction as ill-posed.
- ALL-FEM and PDE-Agents: canonical domains defined analytically in code. Unit squares, rectangles,
  a Turek-Hron beam.
- MooseAgent: the user supplies a mesh file **and tells the agent the boundary names in it**.
- Geng et al.: 2D frames, node coordinates derived by expert rules from bay and storey counts.
- AutoFEA: geometry and mesh blocks provided or truncated; generation focuses on analysis steps.

**Not one takes an arbitrary STEP file and determines which face is the pin hole.** Certus's
`geometry_features.py` catalogue plus `geom_session.py` single-session tag namespace does exactly
that, verified on a real bracket producing 27 feature groups, `PIN_HOLE` confirmed cylindrical about
X at diameter 8.0000 with 0.000% radius spread, `MOUNT_FACE` confirmed planar normal to Z. Given
3.1, this belongs at the top of the differentiator list, not in the plumbing category.

**2. Locking rules tied to load regime.** No system has any L5 check beyond mesh quality with fixed
thresholds. PDE-Agents: `nx < 10` in 2D, `< 8` per direction in 3D, geometry-blind and
solution-blind. FeaGPT: aspect ratio 10, Jacobian 0.3. `locking_check.py`'s eight rules with severity
ladder and owner routing have no counterpart.

**3. Solver capability routing and refusal.** `cure_availability()` turning a finding into a stack
decision has no counterpart. [Likely] Both systems that address refusal do so in the opposite
direction.

**4. Independent read-back verification.** `verify_sets.py` reads the written deck and answers a
geometric question about the actual node coordinates, independent of Gmsh and of the solver. Every
other system verifies through the consuming tool or against a researcher-authored reference.

**5. Check-provenance guarding.** Undiscussed anywhere. Certus has the concept and currently
violates it (R7), which is exactly the worked example a WBSO technical-risk narrative needs.

**6. Cross-platform verdict stability as a stated requirement.** Two benchmarks now measure the
underlying instability; nobody states stability as a design constraint.

---

## 6. New capabilities that would extend the moat

Beyond fixing what exists. Each is unoccupied in the reviewed corpus, buildable solo, and adds a
distinct selling point.

**6.1 `invariants.py`: reference-free physics checks.** Section 3.6. The highest-value addition in
this revision. Solves the measurability trap, is cheap, is general, and is the only check family that
works on a part nobody has analysed before. Start with four: global equilibrium, zero load, load
scaling, rigid-body mode count. Those four are implementable in a few hundred lines once a solve
runs, and each has an unambiguous pass/fail.

**6.2 Two-backend cross-check, with no commercial licence.** The independent review names cross-solver
replication as a priority experiment for detecting correlated implementation errors. Certus already
has `retype_inp()` and a solver registry. Generate the same model for CalculiX and Code_Aster or
FEniCSx, compare invariant quantities after mesh refinement, and report disagreement as a finding.

This has a second benefit that is easy to miss: **it removes the Abaqus oracle from the critical
path entirely.** The project instructions currently carry a live tension about which side of the
engineering-inspection line an Abaqus session sits on, and an unresolved question about institutional
seats backing product claims. If the oracle is Code_Aster, that tension disappears. That is a legal
and reputational simplification as much as a technical one.

**6.3 Dimensional and convention audit.** Units, coordinate frames, and in 2D the thickness
convention, resolved before any force, moment or energy is reported. The MechAgents plate-with-a-hole
reports a total reaction in newtons from a 2D model without resolving thickness. [Likely, from the
independent review; I could not locate the figure in extracted text, but the error class stands on
its own merits] Cheap, catches a real class, and it is the kind of thing a senior engineer notices
immediately and a customer recognises as expert judgement.

**6.4 Consequence-weighted findings.** A field-accuracy number does not reveal consequence: one
missed support changes the response qualitatively while barely moving a count-based score. Rank
findings by estimated sensitivity of the quantity of interest, not by rule count. PDE-Agents already
demonstrated sensitivity weighting works (equal-weight MPF 0.34 against weighted 0.21 on their novel
tasks). Certus can do the same with finite-difference sensitivity on the one or two decisions a
finding implicates.

**6.5 The evidence bundle.** An immutable artefact per run containing: the requirement, the extracted
spec, the face selections and their confirmation, the deck, the mesh statistics, every check with
its verdict and provenance, the invariant test results, the refinement sequence, the solver and
library versions, the platform, and any human approvals. Every solver keyword traces to one of four
origins: user requirement, deterministic transformation, retrieved authoritative source, or labelled
model inference.

Nobody in the corpus produces this. It is the difference between "the agent says it is fine" and "here
is why, and here is what would change the answer." It is also, unlike the checks themselves, hard to
copy quickly, because it requires provenance discipline through the whole pipeline rather than a
feature bolted on at the end.

**6.6 Abstention as a scored output.** Refusal with a stated reason and a proposed cure. The
independent review lists abstention quality as a benchmark dimension nobody scores. Given
Correction 1, Certus must be able to say "no valid configuration on this stack, here is the cure and
here is which solver has it" rather than simply stopping.

---

## 7. Answers to the standing questions

### 7.1 Do these tools avoid commercial solvers?

Mostly yes, which removes "we are the open-source one" as a differentiator. [Certain]

| System | Solver | Commercial licence |
|---|---|---|
| ALL-FEM, PDE-Agents, MechAgents | FEniCS / DOLFINx | no |
| **FeaGPT, AutoFEA** | **CalculiX** | no |
| MooseAgent | MOOSE | no |
| Foam-Agent 2.0 | OpenFOAM | no |
| Geng et al. | OpenSeesPy | no |
| **VFEAgent** | **Abaqus** | **yes** |
| **FEABench** | **COMSOL** | **yes** |
| SimPilot (commercial) | OpenFOAM, SU2, CalculiX | no |

**Do not put "no commercial licence required" on a slide as a differentiator.** It is table stakes.
It remains a cost talking point for an SME paying for seats, but that is adjacent to the "cheaper
simulation" line the project instructions forbid.

One useful consequence: **CalculiX is the established open-source structural choice** (FeaGPT,
AutoFEA, SimPilot). The stack decision in open question 3 is consistent with the field, and its
known limitation, no hybrid elements at all, is a real and citable reason for capability routing
rather than an embarrassment.

### 7.2 Do they run on local LLMs?

Several do, and the trend is toward open weights. [Certain]

- **ALL-FEM**: open-weight only, fine-tuned. Llama 3.2 3B, Llama 3.3 70B, Qwen 3 32B, GPT-OSS 120B.
  Best result from a fine-tuned GPT-OSS 120B, beating non-agentic GPT-5 Thinking.
- **PDE-Agents**: locally deployed open-source LLMs throughout, Qwen for simulation reasoning and
  Llama for analytics, with cross-model validation across two generations.
- **Geng et al.**: Llama-3.3 70B Instruct, chosen explicitly for open-source accessibility and
  deployment scalability, beating Gemini 2.5 Pro and GPT-4o on their benchmark.
- **MooseAgent**: DeepSeek R1 for input cards, V3 for orchestration.
- **AutoFEA**: Llama 3 8B reaches 78.8% against GPT-4o's 90.2%, so even a small open model works in
  the scaffold.
- **FEM-Bench**: evaluates open-weight models alongside frontier ones and names fine-tuning them as
  future work.
- **VFEAgent, FeaGPT, Foam-Agent 2.0, ATHENA, AgenticSciML**: frontier APIs.

**Consequence for Certus.** The EU data sovereignty angle is technically achievable at low cost, and
Certus needs *less* from its LLM than any of them, because the deterministic/LLM split confines the
model to three narrow edges. ALL-FEM needs a fine-tuned 120B to write FEniCS code; Certus needs a
model to turn "fix the mounting face" into a catalogue group id. **Certus can credibly promise a
fully on-premise deployment where no geometry leaves the building.** That converts a soft positioning
claim into a hard technical one, and it composes with the evidence bundle (6.5) into a single
compliance story.

### 7.3 What Milestone B must be

Constraints, all evidence-based:

- Cannot assert on raw numbers (ALL-FEM's mesh-projection problem; Certus's platform variance).
- Cannot seed errors that do not propagate (PDE-Agents Table 6).
- Cannot use a design where failure could lie in geometry, mesh, deck or check (PDE-Agents'
  measurability argument).
- Must fail on known-bad inputs by construction (FEM-Bench's expected-failure design; Certus's R7 as
  the counterexample).
- Must report repeated runs, not single runs (FEM-Bench's five-run instability; ENGDESIGN's
  robustness ratio).
- Must not group train and test by random split when template families exist (AutoFEA's leakage
  risk).

The design that satisfies all six: **one fixed part, one fixed load case, one fixed solver, one
fixed reference, and exactly one modelling decision varied per case.**

Per case:
1. A known-good baseline deck that runs and matches an analytical or converged reference.
2. One deliberate modelling change.
3. A **measured** propagation magnitude. If below noise, discard the case as unobservable and report
   it as such.
4. The expected Certus finding: rule id, severity, owner, cure. The assertion is on this.
5. A matched control where the same rule must **not** fire.
6. Five repeated runs, reporting all-five and at-least-once separately.
7. A helper tier, following FEM-Bench: does the system catch it when told the governing condition,
   and does it catch it when it must infer the condition.

Twelve cases for a first version. Candidates, with external anchors where they exist:

| # | Case | External anchor |
|---|---|---|
| 1 | Plane stress where plane strain is required | **ALL-FEM's own worked failure** |
| 2 | Linear elastic material used past yield | **AutoFEA's cantilever, 0.0975 against 0.8738** |
| 3 | Linear fully-integrated hex in bending: shear locking | FEM-Bench future work |
| 4 | Linear tet in near-incompressible response: volumetric locking, no cure in CalculiX, so also the refusal demo | FEM-Bench future work |
| 5 | Reduced integration without hourglass control | none |
| 6 | Load applied to the wrong but geometrically plausible face | **FeaGPT's index-order selection; VFEAgent's BC 0.600** |
| 7 | Fully clamped face where a pinned constraint is physical: overconstraint | VFEAgent L2 checks under-constraint only |
| 8 | Rigid-body mode not eliminated, solver returns something anyway | none |
| 9 | 2D thickness convention unresolved in a reported force | **MechAgents' plate with a hole** |
| 10 | Units inconsistency surviving dimensional plausibility checks | named as a gap, unoccupied |
| 11 | Symmetry boundary condition on a non-symmetric load | none |
| 12 | Mesh too coarse at a stress concentration, with a convergence gate | PDE-Agents does refinement offline only |

Six of twelve now have a published anchor, which was three in v1. Build on ALL-FEM's public
39-problem set where physics overlaps, so the baseline is externally validated. Anchor the language
to ASME V&V 10-2006.

---

## 8. Revised gap list

| # | Gap | Status |
|---|---|---|
| 1 | Discretization-error adjudication: element formulation against load regime; shear and volumetric locking; hourglassing | **Unoccupied.** Best existing L5 is a fixed-threshold quality metric. FEM-Bench lists incompressible elasticity as future work. Structurally impossible for PDE-Agents. |
| 2 | Solver-capability routing; refusal when no cure exists on the stack | **Unoccupied.** Every reviewed system is single-solver. |
| 3 | Overconstraint auditing | **Unoccupied.** VFEAgent L2 checks under-constraint only. |
| 4 | Idealization validity (plane stress vs plane strain, symmetry, elastic past yield) | **Unoccupied at runtime.** ALL-FEM and AutoFEA both have worked examples; both caught them offline with a reference plus a human. |
| 5 | Input fidelity: the BC is on the face the user meant | **Weakly occupied and measured at 0.600.** Nobody does it on arbitrary 3D CAD. |
| 6 | **Reference-free invariant testing at runtime** | **Unoccupied.** New in v2. FEM-Bench does it at unit-function level; PDE-Agents does refinement offline once. |
| 7 | **Cross-solver replication as a correctness check** | **Unoccupied.** Named as a priority experiment by the independent review; no system implements it. |
| 8 | **Dimensional and 2D-convention closure on reported quantities** | **Unoccupied.** MechAgents demonstrates the failure. |
| 9 | Post-solve assumption closure | **Weakly occupied.** PDE-Agents' Tscore checks Tmax/Tmin against expected ranges. |
| 10 | Mesh convergence as a per-run gate | **Partly occupied.** Offline and once, never per-run per-part. |
| 11 | Refusal as a valid output | **Unoccupied, actively designed against.** |
| 12 | Check-provenance guarding | **Unoccupied and undiscussed.** |
| 13 | **Evidence bundle / provenance graph as a deliverable** | **Unoccupied.** Named as needed by the independent review; nobody ships it. |
| 14 | Verdict reproducibility across platforms and repeated runs | **Unoccupied as a requirement**, though two benchmarks now measure the instability. |
| 15 | **Consequence-weighted finding ranking** | **Unoccupied.** PDE-Agents demonstrated sensitivity weighting for scoring, not for triage. |
| 16 | Negative benchmark for discretization and idealization errors in structural mechanics | **Unoccupied.** FEM-Bench has expected failures at the code layer; PDE-Agents has propagation-aware methodology for material properties on a scalar solver. |
| 17 | Predictive uncertainty (parameter, numerical, model-form) | **Unoccupied and out of scope for now.** Recorded because regulated customers will ask. |

Fifteen of seventeen rows are unoccupied or weakly occupied. That is a wider opening than v1, mostly
because reading source code and adding the reference-free family exposed three new rows.

---

## 9. Selling points, ranked by how hard they are to rebut

Everything here is verified and quotable. This section is new in v2.

**1. Two numbers from the same table.** The best published drawing-to-FEA system detects boundary
conditions at 0.600 and reports execution success at 1.000. Fifteen cases, one paper, 2026.

**2. Two more numbers from a different paper.** Gemini 2.5 Pro: 88.5% of generated structural models
execute, 37% are correct. GPT-4o on the same benchmark: 0% correct.

**3. The field has published the gap and declined to close it.** Verbatim quotes available from
ALL-FEM, Tian and Zhang, and a PRISMA-ScR scoping review of 66 studies.

**4. The one group that measured multi-agent verification found it does not work.** 1,120 trials.
An adversarial agent affirmed 85 to 92% of the time including on errors. No configuration validated
constitutive relations. State that the model was GPT-3.5 Turbo.

**5. Certus does the thing that is measured as the weak point.** Deterministic face selection on
arbitrary CAD with a confirmation step, verified on a real bracket with 27 feature groups. Nobody
else takes a STEP file and works out which face is the pin hole.

**6. Certus can run entirely on premises.** Three groups have shown a 70B-class open-weight model is
sufficient for the LLM roles, and Certus needs less from its model than any of them. No geometry
leaves the building, and the evidence bundle proves it.

**7. Autonomy in the field is overstated, and Certus can be honest about it.** ALL-FEM needs a human
to terminate the loop. Guo's CAE agent needed a human to supply a missing weighted mass matrix.
ATHENA reports human quadrature and wavelet interventions. AgenticSciML requires human approval of
the evaluator. **Certus's human-in-the-loop confirmation step is not a limitation relative to the
field; it is the same thing, declared.** Position it as calibrated allocation of responsibility:
agents do the repeatable work, humans approve the high-consequence assumptions. Hidden human labour
is the weakness; declared human approval is a feature.

**8. Verification is cheap where the cost actually is.** Solver time dominates. Two extra solves
cost far less than an LLM reviewer that adds 700,000 tokens for a two-problem gain.

Two things **not** to claim, because I checked and they do not hold:

- Do not claim nobody checks anything before running. VFEAgent has four pre-solve validation levels;
  PDE-Agents has nine deterministic rules; FeaGPT has three validation layers plus AST preflight.
  Claim instead that the checks operate at L1 to L3 and that no published system checks
  discretization adequacy or idealization validity at runtime.
- Do not claim an open-source stack as a differentiator. It is the field default.

---

## 10. Roadmap

Milestone-gated. The ordering is the point.

### Phase 0 — Close Milestone A. Nothing else first.

Nothing in this review changes the ranking in section 9 of the project instructions, and everything
in it makes the ranking more urgent, because every differentiator above is unverifiable without a
solve.

1. Load and constraint writer: `*MATERIAL`, `*SOLID SECTION`, `*STEP`, `*BOUNDARY`, `*CLOAD`.
2. CalculiX run on the bracket.
3. Comparison to a hand calculation with a stated tolerance band. **Read AutoFEA's cantilever case
   first.** Their comparison of an analytical 0.0975 against an FE 0.8738 is exactly the shape of
   comparison Milestone A makes, and the reason for the difference is a modelling assumption, not a
   bug. State explicitly which assumptions the hand calculation makes and confirm the deck makes the
   same ones before treating a mismatch as a defect.

**Explicit warning per section 12 of the project instructions.** Sections 4, 6 and 9 above will all
tempt you toward another upstream layer: a knowledge base like MooseAgent's, drawing parsing like
VFEAgent's, fine-tuning like ALL-FEM's, an evidence bundle, a second solver backend. Every one is
the failure mode this project has already demonstrated once. **Build none of them until a solver has
run.**

### Phase 1 — Make the checks trustworthy

4. **Fix R7.** Tag `char_size` with provenance; the rule returns `NOT_EVALUATED` when its input
   derives from its own output. Gap row 12 is unoccupied and undiscussed anywhere, and R7 is the
   worked example that makes it credible.
5. **Compute `dominant_mode`** from load resultant against constraint centroid.
6. **Overconstraint rule.** `MOUNT_FACE` is 12.6% of nodes on the real bracket, fully clamped. This
   will corrupt the Milestone A analytical comparison, so it is not optional.
7. **Provenance tagging as a general mechanism**, not a special case for R7.
8. **`invariants.py`, first four checks:** global equilibrium, zero load, load scaling, rigid-body
   mode count. This is new in v2 and it is the highest-value new module. Each check must be
   exercised on a known-good and a known-bad case before it is presented as working, per the
   corollary in section 1 of the project instructions.

### Phase 2 — Milestone B, designed to the constraints in 7.3

9. Freeze the finding schema: rule id, severity, owner, cure, propagation magnitude, provenance,
   consequence weight.
10. Build 12 negative cases per 7.3, each with measured propagation, a matched control, five
    repeated runs, and a helper tier.
11. Assert on findings only, never on stress values.
12. Build on ALL-FEM's public 39-problem set where physics overlaps. Anchor to ASME V&V 10-2006.
13. Add the actionability layer: every finding proposes a modification; refusal fires only when
    `cure_availability()` returns nothing. Keep the per-finding cost low, per Correction 1.

### Phase 3 — Milestone C, starting now, in parallel

Not gated on A or B. Zero customer conversations remains the largest strategic risk.

14. Write the one-page problem statement. It now has six external anchors that make the problem real
    without a demo: VFEAgent's 0.600 against 1.000, Geng's 88.5% against 37%, ALL-FEM's plane-stress
    example, AutoFEA's cantilever, Tian and Zhang's verification-validation gap, and Baker's
    conclusion on benchmarks.
15. Pick one segment. Open question 1 is still open and still blocking.
16. Ten conversations, written notes, plus three questions: (a) would you pay for a checker that
    audits a model you already built, versus a tool that builds it for you; (b) does on-premise, no
    data leaving the building, change your vendor choice; (c) would an auditable evidence bundle per
    run be worth anything to you, and to whom would you show it.
17. **Contact the MatPro group at IFE Kjeller.** Government institute, open source, EU, and they
    name vector mechanics as the extension they are not taking. No working solve required.

### Phase 4 — Only after A, B and C

18. WBSO filing. The technical-risk narrative is externally corroborated: reliable automated
    detection of finite element modelling errors that run cleanly and return plausible results,
    validated against a negative benchmark of known-good and known-bad models, with cited evidence
    that existing systems measure only executability, that the best measured boundary-condition
    detection in the field is 0.600 against 1.000 execution success, and that multi-agent LLM
    verification was measured and failed.
19. Lawyer check on Article 11 before incorporation.
20. KVK, EUIPO trademark, domain.
21. University incubator as participant, not spin-out.

### 10.1 Deferred, deliberately

Second solver backend (6.2), evidence bundle (6.5), consequence weighting (6.4), dimensional audit
(6.3), predictive uncertainty (L9). All are recorded, all are unoccupied, none is built before
Milestone B closes.

### 10.2 The strategic fork, unchanged and now better evidenced

Certus is roughly two years behind on the generation layer and cannot catch up solo. But **the
verification layer does not need to own the pipeline.** `verify_sets.py` already demonstrates the
shape, and `invariants.py` strengthens it, because reference-free invariants can be computed on a
deck and its results regardless of who produced them.

A checker that ingests a deck plus a stated intent and returns findings with severity, owner, cure
and an evidence bundle is buildable by one person, testable against artefacts produced by other
people's systems including ALL-FEM's public set, complementary rather than competitive, attachable to
SimPilot or SimScale or an internal Ansys workflow, and aligned with the one thing Certus has that
nobody else does.

The counter-argument is real: a checker is a feature, features get bought or copied, and it puts
Certus downstream of somebody else's roadmap.

**I am not recommending the fork. I am recommending you decide it deliberately before Milestone D
rather than by drift.** Question 16(a) resolves it.

---

## 11. Citations worth having ready

All verified against the documents this session unless marked. [Certain]

| Claim | Source |
|---|---|
| Best BC detection 0.600, connectivity F1 0.648, alongside 1.000 execution success | Zhang et al., VFEAgent, Table 1 |
| 88.5% of generated structural models execute; 37% are correct; GPT-4o 0% | Geng et al., arXiv 2510.05414 |
| Executable but physically incorrect code passes undetected; no configuration validated constitutive relations | Tian and Zhang, arXiv 2408.13406 (note: GPT-3.5 Turbo) |
| Adversarial agent affirmed 85 to 92% including on errors; 39 of 43 in Task 1 | same |
| A compilable code does not guarantee correctness; the LLM gets no feedback on the numerical approach | Deotale et al., ALL-FEM, CMAME 457:118985 |
| Plane strain substituted for plane stress produced a plot indistinguishable from the reference | same |
| Base GPT-OSS 120B 58.97%; fine-tuned 71.79%; human required to terminate the loop | same |
| Linear-theory answer 0.0975 against plastic FE answer 0.8738 on the same cantilever | Hou et al., AutoFEA, AAAI 2025 |
| Exact `.dat` comparison at rtol 1e-4, atol 1e-6 across 512 CalculiX cases; 90.2% GPT-4o | same |
| Enforcing pre-run checks: 94% against 100%; warning-induced conservatism named as one of three mechanisms; failures traced to budget exhaustion | Adhikari et al., PDE-Agents, arXiv 2606.07850v2 |
| Property errors of 64 to 100% producing zero output error | same, Table 6 |
| Narrow scope is what makes the measurement possible; a deck-generating system cannot isolate error the same way | same, limitations |
| A check that cannot fail is an incidental property, not a meaningful check | Mohammadzadeh et al., FEM-Bench, arXiv 2512.20732v2 |
| Claude Opus solves 29 tasks at least once and 16 in all five runs | same |
| Prompt optimisation closed the gap only after the full 12x12 geometric stiffness matrix was injected | same, GEPA appendix |
| Current evaluation fails to capture functional and physical validity; benchmarks needed for physical plausibility | Baker et al., *Big Data Cogn. Comput.* 9(12):305 (note: arXiv excluded) |
| Weak spatial and geometric reasoning most-cited challenge (16 of 66); reliability second (12) | same |
| Success rate is the percentage of cases that ran successfully | Yue et al., Foam-Agent 2.0 |
| Reviewer agent is the dominant factor: 48.2% to 86.4% | same, ablations |
| Simulation-driven functional verification replaces answer checking; robustness ratio 0.57 to 0.62 | Guo et al., ENGDESIGN (note: Mechanical Systems is 7 tasks) |

---

## 12. What would falsify the thesis

1. **Ten discovery conversations where nobody reports a wrong-but-plausible simulation costing them
   anything.** The failure mode is real; that it is painful enough to pay for is unverified.
2. **A 2026 frontier model catching locking and idealization errors from a deck.** ALL-FEM's
   non-agentic GPT-5 Thinking already solves roughly 27 of 39 expert FEM problems with no
   scaffolding. Testable in an afternoon once Milestone A is done: give the bracket deck with a
   seeded error to the best available model. **Run this test.** If it passes, the moat is the
   benchmark corpus and the evidence bundle, not the individual checks.
3. **SimPilot or SimScale shipping structural setup-correctness checks.** SimPilot already has a V&V
   stage; its published example is CFD-specific and reference-database-dependent. Re-check quarterly.
4. **Certus's own checks reducing the rate at which useful answers are produced.** Then PDE-Agents'
   finding applies to Certus and the actionability layer, not the checks, is the product.
5. **The benchmark not distinguishing Certus from a well-prompted general model** on the 12 negative
   cases.
6. **The invariant checks turning out to be trivially passable.** If a wrong-face load still closes
   global equilibrium and still scales linearly, then `invariants.py` catches less than section 3.6
   claims. Test this on case 6 of the negative benchmark specifically, before building the rest of
   the module.

Items 2 and 6 are cheap and should both be run as soon as Milestone A closes. Item 6 is new in v2
and is the honest stress test of the largest new idea in this revision.
