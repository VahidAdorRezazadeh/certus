"""Viewer regression tests; synthetic values test display mapping, not solver accuracy.

Run: python -m unittest discover -s tests -p test_viewer.py -v
The data tests need only numpy. Plotly checks run when the GUI extra is installed.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from certus.visual_mesh import read_inp_mesh, skin, skin_edges, result_data
from certus import ui


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.deck = self.root / "case.inp"
        self.deck.write_text("*NODE, NSET=ALL\n1,0,0,0\n2,1,0,0\n3,0,1,0\n4,0,0,1\n"
                             "*ELEMENT, TYPE=C3D4\n1,1,2,3,4\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tet_skin(self):
        self.assertEqual(len(skin([[1,2,3,4]])), 4)
        self.assertEqual(len(skin_edges([[1,2,3,4]])), 6)

    def test_shared_face_is_removed(self):
        triangles = skin([[1,2,3,4], [1,3,2,5]])
        self.assertEqual(len(triangles), 6)
        self.assertNotIn({1,2,3}, [set(t) for t in triangles])

    def test_hex_is_not_drawn_as_a_tet(self):
        self.assertEqual(len(skin([list(range(1,9))])), 12)
        self.assertEqual(len(skin_edges([list(range(1,9))])), 12)

    def test_quadratic_tet_retains_midside_nodes(self):
        triangles = skin([list(range(1,11))])
        self.assertEqual(len(triangles), 16)
        self.assertEqual({n for t in triangles for n in t}, set(range(1,11)))
        self.assertEqual(len(skin_edges([list(range(1,11))])), 12)

    def test_quadratic_hex_retains_midside_nodes(self):
        triangles = skin([list(range(1,21))])
        self.assertEqual(len(triangles), 36)
        self.assertEqual({n for t in triangles for n in t}, set(range(1,21)))
        self.assertEqual(len(skin_edges([list(range(1,21))])), 24)

    def test_shared_quadratic_face_is_removed(self):
        a = [1,2,3,4,5,6,7,8,9,10]
        b = [1,3,2,11,7,6,5,12,13,14]
        self.assertEqual(len(skin([a,b])), 24)

    def test_quad_faces_are_planar_for_a_unit_hex(self):
        xyz=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],
                      [0,0,1],[1,0,1],[1,1,1],[0,1,1]])
        for triangle in skin([list(range(8))]):
            pts=xyz[list(triangle)]
            self.assertTrue(any(np.ptp(pts,axis=0) == 0))
            self.assertGreater(np.linalg.norm(np.cross(pts[1]-pts[0],pts[2]-pts[0])),0)

    def test_include_and_node_keyword(self):
        original=self.deck.read_text()
        (self.root/'mesh.inc').write_text(original)
        self.deck.write_text('*INCLUDE, INPUT="mesh.inc"\n*NODE FILE\nU\n')
        nodes,elems=read_inp_mesh(self.deck)
        self.assertEqual(len(nodes),4)
        self.assertEqual(elems,[[1,2,3,4]])

    def test_quadratic_element_continuation(self):
        self.deck.write_text('*NODE\n'+''.join(f'{i},{i},0,0\n' for i in range(1,21))+
                             '*ELEMENT, TYPE = C3D20R\n1,1,2,3,4,5,6,7,8,9,10,\n11,12,13,14,15,16,17,18,19,20\n')
        self.assertEqual(len(read_inp_mesh(self.deck)[1][0]),20)

    def test_missing_node_is_rejected(self):
        self.deck.write_text(self.deck.read_text().replace('1,1,2,3,4','1,1,2,3,9'))
        with self.assertRaisesRegex(ValueError,'missing node'):
            read_inp_mesh(self.deck)

    def test_unsupported_element_is_rejected(self):
        self.deck.write_text(self.deck.read_text().replace('C3D4','S4'))
        with self.assertRaisesRegex(ValueError,'does not support'):
            read_inp_mesh(self.deck)

    def test_incomplete_connectivity_is_rejected(self):
        self.deck.write_text(self.deck.read_text().replace('1,1,2,3,4','1,1,2'))
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            read_inp_mesh(self.deck)

    def test_cyclic_include_is_rejected(self):
        self.deck.write_text('*INCLUDE, INPUT=case.inp\n')
        with self.assertRaisesRegex(ValueError,'Cyclic'):
            read_inp_mesh(self.deck)

    def test_non_finite_coordinate_is_rejected(self):
        self.deck.write_text(self.deck.read_text().replace('2,1,0,0','2,nan,0,0'))
        with self.assertRaisesRegex(ValueError,'non-finite'):
            read_inp_mesh(self.deck)

    def test_nonmanifold_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Non-manifold'):
            skin([[1,2,3,4],[1,2,3,5],[1,2,3,6]])

    def data(self, field='U', scale=1.0, displacements=None, stresses=None):
        u=displacements if displacements is not None else {n:(0,0,-.1*n) for n in range(1,5)}
        s=stresses if stresses is not None else {n:(10*n,0,0,0,0,0) for n in range(1,5)}
        with patch('certus.frdread.read_frd_field',return_value=u), patch('certus.frdread.read_frd_stress',return_value=s):
            return result_data(self.deck,'fixture.frd',field,scale)

    def test_true_scale_and_original_values(self):
        d=self.data()
        np.testing.assert_allclose(d['P'],d['X']+d['U'])
        np.testing.assert_allclose(d['values'],[.1,.2,.3,.4])
        self.assertEqual(d['units'],'mm')

    def test_signed_component(self):
        np.testing.assert_allclose(self.data('UZ')['values'],[-.1,-.2,-.3,-.4])

    def test_stress_units_and_values(self):
        d=self.data('S')
        np.testing.assert_allclose(d['values'],[10,20,30,40])
        self.assertEqual(d['units'],'MPa')

    def test_magnification_does_not_change_values(self):
        a,b=self.data(scale=1),self.data(scale=100)
        np.testing.assert_array_equal(a['values'],b['values'])
        np.testing.assert_allclose(b['P']-b['X'],100*b['U'])

    def test_undeformed(self):
        d=self.data(scale=0)
        np.testing.assert_array_equal(d['P'],d['X'])

    def test_zero_field_auto_scale_is_finite(self):
        d=self.data(scale=None,displacements={n:(0,0,0) for n in range(1,5)})
        self.assertEqual(d['scale'],0)
        self.assertTrue(np.isfinite(d['P']).all())

    def test_missing_displacement_is_not_replaced_with_zero(self):
        with self.assertRaisesRegex(ValueError,'no zero values'):
            self.data(displacements={1:(0,0,0)})

    def test_missing_stress_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Stress is missing'):
            self.data('S',stresses={})

    def test_non_finite_displacement_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'non-finite'):
            self.data(displacements={n:(float('nan'),0,0) for n in range(1,5)})

    def test_negative_scale_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'non-negative'):
            self.data(scale=-1)

    def test_unknown_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unsupported result field'):
            self.data('invented')

    @unittest.skipUnless(importlib.util.find_spec('plotly'), 'Plotly is not installed')
    def test_plotly_result_serializes(self):
        from certus.viewer import result_figure
        u={n:(0,0,-.1*n) for n in range(1,5)}
        with patch('certus.frdread.read_frd_field',return_value=u):
            f=result_figure(self.deck,'fixture.frd','UZ',scale=10,show_edges=True,show_extrema=True)
        self.assertIn('Displacement UZ',f.to_json())
        self.assertEqual(f.layout.meta['deformation_scale'],10)
        self.assertEqual(f.data[0].cmin,-f.data[0].cmax)
        np.testing.assert_allclose(f.data[0].customdata[:,7],[-.1,-.2,-.3,-.4])

    @unittest.skipUnless(importlib.util.find_spec('plotly'), 'Plotly is not installed')
    def test_plotly_face_roles_and_camera(self):
        from certus.viewer import faces_figure
        xyz=np.array([[0.,0,0],[1,0,0],[0,1,0]])
        tri={1:(xyz,np.array([[0,1,2]])),2:(xyz+1,np.array([[0,1,2]]))}
        f=faces_figure(tri,[1],[2],view='Top · XY',show_labels=True,load_vector=(0,0,-1))
        f.to_json()
        self.assertEqual(f.data[0].color,'#df623e')
        self.assertEqual(f.layout.scene.camera.projection.type,'orthographic')
        self.assertEqual(f.layout.scene.camera.eye.z,2.1)


class BrandTests(unittest.TestCase):
    def test_assets_are_local(self):
        self.assertTrue(ui.asset_uri('Verimech_Icon.svg').startswith('data:image/svg+xml;base64,'))
        css=ui.stylesheet()
        self.assertIn('IBM Plex Sans',css)
        self.assertNotIn('https://',css)

    def test_viewer_export_is_local(self):
        self.assertFalse(ui.PLOT_CONFIG['displaylogo'])
        self.assertEqual(ui.PLOT_CONFIG['toImageButtonOptions']['scale'],2)


if __name__ == '__main__':
    unittest.main()
