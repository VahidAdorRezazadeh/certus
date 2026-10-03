"""Interpretation review: provenance, completion questions and revision state.

Language-model interpretations remain drafts. Geometry measurements and the
later face confirmation are still required before any simulation.
"""
import math

from certus import intent as INT

GROUPS = (
    ('01 · Part', (('part', 'Shape and dimensions'),)),
    ('02 · Material', (('material', 'Material'), ('yield_MPa', 'Yield strength (MPa)'))),
    ('03 · Boundary conditions', (('fix_feature', 'Constrained feature'), ('support', 'Constraints'))),
    ('04 · Loads', (('load_kind', 'Load type'), ('load_feature', 'Loaded feature'),
                    ('force_N', 'Force magnitude (N)'), ('direction', 'Force direction'),
                    ('pressure_MPa', 'Pressure (MPa)'))),
    ('05 · Analysis goal', (('goal', 'Analysis goal'), ('question', 'Your question'))),
)
DERIVED_KEYS = ('cad', 'tri', 'cat', 'part_tri', 'part_view_path', 'rd', 'conv',
                'sug_load', 'sug_fix', 'load_gid', 'fix_gid', 'form')


def clear_derived(state):
    for key in list(state.keys()):
        if key in DERIVED_KEYS or key.startswith(('confirm_faces_', 'review_assumptions_', 'review_material_')):
            state.pop(key, None)


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def geometry_questions(spec):
    if not isinstance(spec, dict) or not spec:
        return ['Describe the shape and give the dimensions with units.']
    questions = []
    overall = spec.get('overall_mm')
    for axis in ('x', 'y', 'z'):
        if not isinstance(overall, dict) or not _positive(overall.get(axis)):
            questions.append(f'Give the overall {axis.upper()} dimension with units, or the dimensions needed to derive it.')
    holes = spec.get('holes', [])
    if not isinstance(holes, list):
        questions.append('Clarify the holes and their dimensions.')
    else:
        for i, hole in enumerate(holes, 1):
            if not isinstance(hole, dict) or not _positive(hole.get('diameter_mm')):
                questions.append(f'Give the diameter of hole {i} with units.')
    extra = spec.get('questions', [])
    if isinstance(extra, list):
        questions.extend(str(q) for q in extra if q)
    elif extra:
        questions.append(str(extra))
    return list(dict.fromkeys(questions))


def completion_questions(intent, spec, has_cad=False):
    questions = [] if has_cad else geometry_questions(spec)
    required = [('material', 'Name the material: steel, aluminium or titanium.'),
                ('fix_feature', 'Describe the feature that is constrained.'),
                ('support', 'State the constraints: all translations fixed, or fixed only in the load direction.'),
                ('load_kind', 'Specify a force (N or kN) or a pressure (MPa).'),
                ('load_feature', 'Describe where the load acts.'),
                ('goal', 'State the goal: deflection, peak stress, first yield, or setup checks.')]
    if intent.get('load_kind') == 'force':
        required += [('force_N', 'Give the force magnitude with units (N or kN).'),
                     ('direction', 'Give the force direction (+X, -X, +Y, -Y, +Z or -Z).')]
        if intent.get('pressure_MPa') is not None:
            questions.append('The request includes pressure but selects a force. Clarify which single load type to apply.')
    elif intent.get('load_kind') == 'pressure':
        required += [('pressure_MPa', 'Give the pressure with units (MPa). Positive pressure pushes into the face.')]
        if intent.get('force_N') is not None:
            questions.append('The request includes a force but selects pressure. Clarify which single load type to apply.')
    if intent.get('goal') == 'does it yield':
        required += [('yield_MPa', 'Give the material yield strength with units (MPa).')]
    for key, question in required:
        if intent.get(key) is None:
            questions.append(question)
    return list(dict.fromkeys(questions))


def review_rows(intent, spec, material=None):
    rows = []
    for group, fields in GROUPS:
        for key, label in fields:
            field = intent.fields.get(key, INT.Field())
            rows.append(dict(group=group, field=label, value=field.value,
                             status=field.status,
                             source=field.quote if field.status == 'read' else field.note or 'Not stated in your request.'))
        if group.startswith('01') and isinstance(spec, dict) and spec:
            overall = spec.get('overall_mm') or {}
            if isinstance(overall, dict):
                rows.append(dict(group=group, field='Overall dimensions (mm)',
                                 value=' · '.join(f'{ax.upper()}: {overall.get(ax) if overall.get(ax) is not None else "missing"}' for ax in ('x', 'y', 'z')),
                                 status='draft', source='Draft part specification. Check dimensions and assumptions on the right.'))
        if group.startswith('02') and material is not None:
            for label, value in [('Constitutive model', 'Linear elastic'),
                                 ('Elastic modulus (MPa)', material.E),
                                 ('Poisson ratio', material.nu)]:
                rows.append(dict(group=group, field=label, value=value,
                                 status='preset', source='Certus material preset. Requires your confirmation; not extracted from your words.'))
    return rows


def revised_request(prompt, correction):
    correction = correction.strip()
    if not correction:
        raise ValueError('Enter a correction or the missing information.')
    return prompt + '\n\nUSER CORRECTION (supersedes earlier conflicting statements):\n' + correction


def apply_revision(state, correction, read_intent, read_spec):
    """Commit both drafts together only after both model calls succeed."""
    prompt = revised_request(state['prompt'], correction)
    intent = read_intent(prompt, state.get('image'))
    uploaded = state.get('uploaded_step', bool(state.get('step') and not state.get('cad')))
    spec = None if uploaded else read_spec(prompt, state.get('image'))
    if not uploaded and not isinstance(spec, dict):
        raise ValueError('The model did not return a part specification.')
    clear_derived(state)
    if not uploaded:
        state['step'] = None
    state.update(prompt=prompt, intent=intent, spec=spec,
                 review_revision=state.get('review_revision', 0) + 1)
