import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from certus import intent as INT, review, ui

PROMPT = ('A steel rectangular beam 100 x 20 x 10 mm. Fix the left face in all translations. '
          'Apply 2 kN downwards on the right face. What is the deflection?')
VALUES = {'part': ('rectangular beam', 'rectangular beam 100 x 20 x 10 mm'),
          'material': ('steel', 'steel'), 'fix_feature': ('left face', 'left face'),
          'support': ('fully fixed (all translations)', 'all translations'),
          'load_feature': ('right face', 'right face'), 'load_kind': ('force', 'Apply 2 kN'),
          'force_N': (2000, '2 kN downwards'), 'direction': ('-Z', 'downwards'),
          'goal': ('stiffness (deflection)', 'deflection')}
SPEC = {'part_name': 'Beam', 'overall_mm': {'x': 100, 'y': 20, 'z': 10},
        'holes': [], 'features': ['solid rectangular section'], 'questions': []}

def draft():
    return INT.validate({k: {'value': v, 'quote': q} for k, (v, q) in VALUES.items()}, PROMPT)

class ReviewTests(unittest.TestCase):
    def test_complete_and_lying_inputs(self):
        self.assertEqual(review.completion_questions(draft(), SPEC), [])
        it = draft()
        it.fields['force_N'] = INT.validate({'force_N': {'value': 5000, 'quote': '2 kN'}}, PROMPT).fields['force_N']
        self.assertIn('Give the force magnitude', '\n'.join(review.completion_questions(it, SPEC)))

    def test_missing_geometry_and_ambiguous_units(self):
        spec = dict(SPEC, overall_mm={'x': 100, 'y': None, 'z': 10},
                    questions=['What units apply to the width?'])
        questions = '\n'.join(review.completion_questions(draft(), spec))
        self.assertIn('Y dimension', questions)
        self.assertIn('units apply', questions)
        self.assertEqual(len(review.geometry_questions(dict(SPEC, overall_mm={'x': float('nan'), 'y': -1, 'z': True}))), 3)

    def test_force_pressure_conflict(self):
        it = draft()
        it.fields['pressure_MPa'] = INT.Field(200, '200 MPa', 'read')
        self.assertIn('Clarify', '\n'.join(review.completion_questions(it, SPEC)))

    def test_strength_required_only_for_yield(self):
        it = draft()
        self.assertFalse(any('yield strength' in q for q in review.completion_questions(it, SPEC)))
        it.fields['goal'] = INT.Field('does it yield', 'Does it yield?', 'read')
        self.assertTrue(any('yield strength' in q for q in review.completion_questions(it, SPEC)))

    def test_group_order_and_preset_provenance(self):
        rows = review.review_rows(draft(), SPEC, SimpleNamespace(E=210000, nu=.3))
        groups = list(dict.fromkeys(row['group'] for row in rows))
        self.assertEqual(groups, [g for g, _ in review.GROUPS])
        presets = [row for row in rows if row['status'] == 'preset']
        self.assertEqual(len(presets), 3)
        self.assertTrue(all('not extracted' in row['source'] for row in presets))

    def test_html_escapes_user_content(self):
        rows = review.review_rows(draft(), SPEC)
        rows[0]['value'] = '<script>alert(1)</script>'
        rows[0]['source'] = '<img src=x onerror=alert(1)>'
        html = ui.review_table_html(rows)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('Missing · not stated', html)

    def test_revision_updates_both_drafts_and_discards_stale_outputs(self):
        state = dict(prompt=PROMPT, intent=draft(), spec=SPEC, image=None,
                     uploaded_step=False, step='generated.step', cad={}, rd='old results',
                     load_gid=1, confirm_faces_1=True, review_material_0=True)
        read = Mock(return_value=draft())
        generate = Mock(return_value=dict(SPEC, part_name='Corrected beam'))
        review.apply_revision(state, 'Make the beam 150 mm long.', read, generate)
        self.assertIn('150 mm', state['prompt'])
        self.assertEqual(state['spec']['part_name'], 'Corrected beam')
        self.assertIsNone(state['step'])
        self.assertNotIn('rd', state)
        self.assertNotIn('confirm_faces_1', state)
        self.assertNotIn('review_material_0', state)
        self.assertEqual(read.call_args.args[0], generate.call_args.args[0])

    def test_failed_revision_preserves_prior_review_and_results(self):
        state = dict(prompt=PROMPT, intent=draft(), spec=SPEC, rd='previous result')
        before = dict(state)
        with self.assertRaises(RuntimeError):
            review.apply_revision(state, 'Change width to 25 mm.', Mock(return_value=draft()), Mock(side_effect=RuntimeError('offline')))
        self.assertEqual(state, before)

    def test_uploaded_cad_is_preserved(self):
        state = dict(prompt=PROMPT, intent=draft(), spec=None, uploaded_step=True, step='input.step')
        generate = Mock()
        review.apply_revision(state, 'Change force to 3 kN.', Mock(return_value=draft()), generate)
        generate.assert_not_called()
        self.assertEqual(state['step'], 'input.step')
        self.assertIsNone(state['spec'])

if __name__ == '__main__':
    unittest.main()
