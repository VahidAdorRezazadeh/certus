#!/usr/bin/env python3
"""
run_dir.py - every run gets its own folder, and a report an engineer can read.

Folder name is  YYYY-MM-DD_HHMMSS_label  so a directory listing sorts
chronologically and already says what the run was. 100233 is the time,
10:02:33.

    runs/
      2026-08-11_100233_cantilever_L100_h5/
        REPORT.txt            the engineering report. Read this one.
        run.json              the same facts, machine readable
        geometry/             the STEP that was meshed
        mesh/                 .msh and the mesh-only .inp
        case_abaqus/          solvable deck in Abaqus syntax
        case_calculix/        solvable deck in CalculiX syntax
        results/              solver output and the verification report

Why case folders are named per solver: the decks are NOT interchangeable.
CalculiX rejects ENCASTRE, Abaqus accepts it. CalculiX wants *NODE FILE,
Abaqus wants *OUTPUT, FIELD. Writing both from one mesh is deliberate, since
that pair is also what an oracle comparison needs.

Why run.json sits next to a human report: with forty benchmark runs you need a
script that reads every run and builds the table of which rule fired on which
case at which mesh size. That table is the benchmark and the grant evidence.
Text reports do not aggregate.
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List, Tuple
import datetime
import json
import os
import shutil

BASE_SUBDIRS = ("geometry", "mesh", "results")


class RunDir:
    def __init__(self, label: str, root: str = "runs",
                 solvers: Tuple[str, ...] = ("abaqus",),
                 meta: Optional[Dict[str, Any]] = None):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe = "".join(c if (c.isalnum() or c in "-_") else "_"
                       for c in label)[:60]
        self.path = os.path.join(root, f"{stamp}_{safe}")
        if os.path.exists(self.path):
            raise RuntimeError(f"run folder already exists: {self.path}")
        self.solvers = tuple(solvers)
        self._dirs = list(BASE_SUBDIRS) + [f"case_{s}" for s in self.solvers]
        os.makedirs(self.path)
        for d in self._dirs:
            os.makedirs(os.path.join(self.path, d))

        self.meta: Dict[str, Any] = dict(meta or {})
        self.meta["created"] = stamp
        self.meta["label"] = label
        self.meta["solvers"] = list(self.solvers)
        self._sections: List[Tuple[str, str]] = []
        self._warnings: List[str] = []
        self._actions: List[str] = []
        self.save_meta()

    # -- paths ------------------------------------------------------------

    def sub(self, kind: str, filename: str = "") -> str:
        if kind not in self._dirs:
            raise KeyError(f"unknown subdir '{kind}', have {self._dirs}")
        d = os.path.join(self.path, kind)
        return os.path.join(d, filename) if filename else d

    def case(self, solver: str, filename: str = "") -> str:
        return self.sub(f"case_{solver}", filename)

    def prefix(self, kind: str, name: str) -> str:
        return os.path.join(self.path, kind, name)

    def adopt(self, src: str, kind: str, newname: str = "") -> str:
        dst = self.sub(kind, newname or os.path.basename(src))
        shutil.copy2(src, dst)
        return dst

    # -- record -----------------------------------------------------------

    def section(self, title: str, body: str):
        self._sections.append((title, body.rstrip()))

    def warn(self, text: str):
        self._warnings.append(text)

    def action(self, text: str):
        self._actions.append(text)

    def set(self, key: str, value: Any):
        self.meta[key] = value
        self.save_meta()

    def save_meta(self):
        with open(os.path.join(self.path, "run.json"), "w") as f:
            json.dump(self.meta, f, indent=2, default=str)

    # -- report -----------------------------------------------------------

    def _file_tree(self) -> str:
        lines = []
        for d in self._dirs:
            full = os.path.join(self.path, d)
            names = sorted(os.listdir(full))
            if not names:
                lines.append(f"  {d}/   (empty)")
                continue
            lines.append(f"  {d}/")
            for n in names:
                size = os.path.getsize(os.path.join(full, n))
                lines.append(f"      {n:<34s} {size/1024:9.1f} kB")
        return "\n".join(lines)

    def write_report(self) -> str:
        w, W = 78, "=" * 78
        out = [W, "  CERTUS SIMULATION REPORT",
               f"  {self.meta.get('label','')}", W, ""]
        for k, v in (("run id", os.path.basename(self.path)),
                     ("created", self.meta.get("created", "")),
                     ("geometry",
                      os.path.basename(str(self.meta.get("step", "")))),
                     ("decks written", ", ".join(self.solvers))):
            out.append(f"  {k:<22s} {v}")
        out.append("")

        verdict = self.meta.get("headline_verdict")
        if verdict:
            out += [W, f"  HEADLINE: {verdict}", W, ""]

        for i, (title, body) in enumerate(self._sections, 1):
            out += [f"{i}. {title}", "-" * w, body, ""]

        n = len(self._sections)
        out += [f"{n+1}. WARNINGS", "-" * w]
        out += [f"  ! {x}" for x in self._warnings] or ["  none raised"]
        out.append("")

        out += [f"{n+2}. WHAT TO DO NEXT", "-" * w]
        out += [f"  {i}. {x}" for i, x in enumerate(self._actions, 1)] \
            or ["  nothing outstanding"]
        out.append("")

        out += [f"{n+3}. FILES", "-" * w, self._file_tree(), "", W,
                "  Every number above was produced by deterministic code. No "
                "language model",
                "  chose an element type, a mesh size, a deformation mode or "
                "a verdict.", W]

        text = "\n".join(out)
        path = os.path.join(self.path, "REPORT.txt")
        with open(path, "w") as f:
            f.write(text + "\n")
        self.meta["report"] = path
        self.save_meta()
        return path

    def __str__(self):
        return self.path
