#!/usr/bin/env python3
"""
intent.py - read the user's prompt into a DRAFT intent form.

This is a language edge. The model fills a form; it decides nothing.

What the model may fill                     What it may never fill
-----------------------------------         ----------------------------------
which feature carries the load (phrase)     element type, mesh size, E, nu
which feature is held (phrase)              the dominant deformation mode
load kind, magnitude, direction             any verdict
material NAME from a fixed list             material constants (these come
goal from a fixed list                        from model_agent.MATERIALS)
how the support is held
yield stress, only if the user wrote one

Every value must come with the exact words from the prompt that support it
("quote"). A deterministic check then keeps a value only if

    1. the quote really is in the prompt (case and spacing ignored), and
    2. for numbers, a number in the quote equals the value up to a known
       unit factor (N, kN, MPa, GPa, kg to N, lbf to N).

A value that fails either test is dropped and becomes a question in the GUI.
So the model can leave a field empty, but it cannot put a number in the form
that the user did not write. Everything that survives is still shown to the
user, marked "read from your text", and must be confirmed before the run.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import json
import re

MATERIAL_NAMES = ["steel", "aluminium", "titanium"]
GOAL_NAMES = ["stiffness (deflection)", "peak stress", "does it yield",
              "just check the setup"]
DIRECTIONS = ["-Z", "+Z", "-Y", "+Y", "-X", "+X"]
SUPPORTS = ["fully fixed (all translations)",
            "fixed in the load direction only"]

INTENT_SYSTEM = f"""You read an engineer's request for a structural simulation
and fill a form. You do not do engineering. You copy what the user said.

Return ONLY one JSON object with these keys. Each value is an object
{{"value": ..., "quote": "..."}} where quote is the EXACT words from the
request that state it. If the request does not state a field, use
{{"value": null, "quote": ""}}. Never infer, never use a default, never
use typical values.

  "part"          value: short description of the part geometry (text)
  "question"      value: what the user wants to know, in their words
  "load_feature"  value: the feature the load acts on, e.g. "the pin hole"
  "fix_feature"   value: the feature that is held, e.g. "the bottom face"
  "load_kind"     value: "force" or "pressure"
  "force_N"       value: magnitude in newtons (convert kN to N)
  "direction"     value: one of {DIRECTIONS}, only if the request names an
                  axis or says down (-Z) / up (+Z)
  "pressure_MPa"  value: pressure in MPa
  "material"      value: one of {MATERIAL_NAMES}, or null for anything else
  "goal"          value: one of {GOAL_NAMES}
  "support"       value: one of {SUPPORTS}
  "yield_MPa"     value: yield stress in MPa, only if a number is written

No prose. No markdown fences."""

# A quoted number must carry a unit, and the unit fixes the factor. Without
# this, "2 kN" read as value 2 would pass as factor 1: a 1000x load error
# accepted silently, which is exactly the error class check 1 exists for.
_UNITS = {
    "force_N": {"n": 1.0, "newton": 1.0, "newtons": 1.0, "kn": 1e3,
                "mn": 1e6, "kgf": 9.80665, "kg": 9.80665, "lbf": 4.44822,
                "lb": 4.44822},
    "pressure_MPa": {"mpa": 1.0, "n/mm2": 1.0, "n/mm^2": 1.0, "kpa": 1e-3,
                     "pa": 1e-6, "bar": 0.1, "gpa": 1e3, "psi": 6.89476e-3},
}
_UNITS["yield_MPa"] = _UNITS["pressure_MPa"]
_NUM_UNIT = re.compile(r"([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*"
                       r"([a-zA-Z/^0-9]+)?")
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")

FIELDS = ["part", "question", "load_feature", "fix_feature", "load_kind",
          "force_N", "direction", "pressure_MPa", "material", "goal",
          "support", "yield_MPa"]
NUMERIC = {"force_N", "pressure_MPa", "yield_MPa"}
CHOICES = {"load_kind": ["force", "pressure"], "direction": DIRECTIONS,
           "material": MATERIAL_NAMES, "goal": GOAL_NAMES,
           "support": SUPPORTS}


@dataclass
class Field:
    value: Any = None
    quote: str = ""
    status: str = "missing"      # read | missing | rejected
    note: str = ""


@dataclass
class Intent:
    fields: Dict[str, Field] = field(default_factory=dict)
    raw: str = ""

    def get(self, k, default=None):
        f = self.fields.get(k)
        return f.value if f and f.status == "read" else default

    def missing(self) -> List[str]:
        return [k for k in FIELDS if self.fields.get(k, Field()).status
                != "read"]

    def to_dict(self):
        return {k: asdict(v) for k, v in self.fields.items()}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _numbers(s: str) -> List[float]:
    out = []
    for m in _NUM.findall(s or ""):
        try:
            out.append(float(m.replace(",", ".")))
        except ValueError:
            pass
    return out


def _number_supported(key: str, value: float, quote: str):
    """(ok, note). ok only if a number WITH a known unit in the quote
    converts to the value."""
    units = _UNITS[key]
    saw_bare = False
    for num, unit in _NUM_UNIT.findall(quote or ""):
        n = float(num.replace(",", "."))
        u = (unit or "").lower().rstrip(".")
        if u not in units:
            saw_bare = True
            continue
        if n != 0 and abs(abs(value) - abs(n) * units[u]) <= 1e-3 * abs(value):
            return True, ""
        return False, (f"{value:g} does not match '{num} {unit}' "
                       f"(= {abs(n) * units[u]:g})")
    if saw_bare:
        return False, "no unit written next to the number"
    return False, f"{value:g} does not match any number in '{quote}'"


def validate(obj: Optional[dict], prompt: str) -> Intent:
    """The deterministic half. Keeps only values the prompt supports."""
    it = Intent()
    p = _norm(prompt)
    obj = obj or {}
    for k in FIELDS:
        entry = obj.get(k)
        if not isinstance(entry, dict):
            entry = {"value": entry, "quote": ""}
        v, q = entry.get("value"), str(entry.get("quote") or "")
        if v in (None, "", []):
            it.fields[k] = Field()
            continue
        if not q or _norm(q) not in p:
            it.fields[k] = Field(v, q, "rejected",
                                 "the model could not point to where you "
                                 "said this")
            continue
        if k in NUMERIC:
            try:
                v = float(v)
            except (TypeError, ValueError):
                it.fields[k] = Field(v, q, "rejected", "not a number")
                continue
            ok, why = _number_supported(k, v, q)
            if not ok:
                it.fields[k] = Field(v, q, "rejected", why)
                continue
        if k in CHOICES and v not in CHOICES[k]:
            it.fields[k] = Field(v, q, "rejected",
                                 f"'{v}' is not one of the options")
            continue
        it.fields[k] = Field(v, q, "read")
    # a direction word must actually appear. 'down' is accepted for -Z,
    # 'up' for +Z; an axis name is accepted as written.
    d = it.fields.get("direction")
    if d and d.status == "read":
        ok = (d.value.lower() in _norm(d.quote) or
              (d.value == "-Z" and re.search(r"\b(down|downward|downwards)\b",
                                              _norm(d.quote))) or
              (d.value == "+Z" and re.search(r"\b(up|upward|upwards)\b",
                                              _norm(d.quote))))
        if not ok:
            it.fields["direction"] = Field(d.value, d.quote, "rejected",
                                           "no axis or up/down in the quote")
    return it


def read_intent(prompt: str, image_path: Optional[str] = None) -> Intent:
    """The language half, then the deterministic half."""
    import llm
    content = [{"type": "text", "text": f"Request:\n{prompt}"}]
    raw = llm.ask(INTENT_SYSTEM, content, max_tokens=1500, role="intent form")
    it = validate(llm.json_or_none(raw), prompt)
    it.raw = raw
    return it


if __name__ == "__main__":
    # known-good and known-bad cases, no model needed
    prompt = ("An L bracket, 60 by 50 by 30 mm, steel. A pin in the hole "
              "pulls 2 kN downwards. The bottom face is bolted to the "
              "table. Does it yield? Yield is 250 MPa.")
    good = {"force_N": {"value": 2000, "quote": "pulls 2 kN downwards"},
            "direction": {"value": "-Z", "quote": "pulls 2 kN downwards"},
            "material": {"value": "steel", "quote": "steel"},
            "yield_MPa": {"value": 250, "quote": "Yield is 250 MPa"}}
    # value 2 from '2 kN' is the silent 1000x error: must be rejected
    unit = validate({"force_N": {"value": 2, "quote": "pulls 2 kN downwards"}},
                    prompt)
    assert unit.fields["force_N"].status == "rejected", unit.fields
    print("unit trap  force 2 from '2 kN' ->", unit.fields["force_N"].note)
    bare = validate({"force_N": {"value": 200, "quote": "a load of 200"}},
                    "a load of 200 on the hole")
    assert bare.fields["force_N"].status == "rejected"
    print("bare number 'a load of 200'     ->", bare.fields["force_N"].note)
    bad = {"force_N": {"value": 5000, "quote": "pulls 2 kN downwards"},
           "direction": {"value": "-Y", "quote": "pulls 2 kN downwards"},
           "material": {"value": "aluminium", "quote": "aluminium"},
           "yield_MPa": {"value": 355, "quote": "typical S355"}}
    g, b = validate(good, prompt), validate(bad, prompt)
    for k in good:
        print(f"good {k:10s} {g.fields[k].status:8s}  "
              f"bad {b.fields[k].status:8s} {b.fields[k].note}")
    assert all(g.fields[k].status == "read" for k in good)
    assert all(b.fields[k].status == "rejected" for k in bad)
    print("intent validator: known-good kept, known-bad rejected")
