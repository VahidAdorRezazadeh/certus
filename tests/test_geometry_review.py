"""Known-good and known-bad geometry checks, drawing fit and selection glyphs."""
import unittest
from types import SimpleNamespace
import numpy as np
import matplotlib.pyplot as plt
from certus import cad_agent as CA, viewer, ui

V = np.array([[0,0,0],[1000,0,0],[1000,200,0],[0,200,0],
              [0,0,50],[1000,0,50],[1000,200,50],[0,200,50]],float)
F = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7], [0,1,5],[0,5,4],
              [3,7,6],[3,6,2],[0,4,7],[0,7,3],[1,2,6],[1,6,5]])

class GeometryReviewTests(unittest.TestCase):
    def measurements(self, fill):
        return dict(valid=True, n_solids=1, bbox={'x':1000,'y':200,'z':50},
                    bbox_fill=fill, n_faces=6, volume=10_000_000, holes=[])

    def test_solid_box_and_thin_part_pass_physical_fill_check(self):
        for fill in (1, .00001):
            results = CA.check_spec({}, self.measurements(fill))
            self.assertEqual(CA.verdict(results)[0], 'PASS')

    def test_invalid_volume_ratio_is_a_critical_failure(self):
        for fill in (0, -1, 1.02, float('nan')):
            results = CA.check_spec({}, self.measurements(fill))
            self.assertEqual(CA.verdict(results)[0], 'FAIL')

    def test_stated_fill_band_still_catches_wrong_geometry(self):
        results = CA.check_spec({'bbox_fill_range_source':'user','bbox_fill_range':[.1,.2]}, self.measurements(1))
        self.assertEqual(CA.verdict(results)[0], 'WARN')
        self.assertIn('stated by the user', results[-1]['detail'])

    def test_wrong_dimension_remains_a_failure(self):
        results = CA.check_spec({'overall_mm':{'x':900}}, self.measurements(1))
        self.assertEqual(CA.verdict(results)[0], 'FAIL')

    def test_isometric_fit_contains_entire_long_beam_projection(self):
        mesh = CA.Mesh(V[F])
        fig, ax = plt.subplots()
        CA._draw_iso(ax, mesh)
        for path in ax.collections[0].get_paths():
            xy = path.vertices
            self.assertTrue((xy[:,0]>=ax.get_xlim()[0]).all() and (xy[:,0]<=ax.get_xlim()[1]).all())
            self.assertTrue((xy[:,1]>=ax.get_ylim()[0]).all() and (xy[:,1]<=ax.get_ylim()[1]).all())
        self.assertEqual(ax.get_aspect(), 1)
        plt.close(fig)

    def test_fitted_camera_preserves_equal_coordinate_scale(self):
        fig = viewer.faces_figure({1:(V,F)}, projection='orthographic')
        scene = fig.layout.scene
        ratios = [scene.aspectratio.x,scene.aspectratio.y,scene.aspectratio.z]
        scales = [ratios[i]/(axis.range[1]-axis.range[0]) for i,axis in enumerate((scene.xaxis,scene.yaxis,scene.zaxis))]
        np.testing.assert_allclose(scales,[scales[0]]*3)
        self.assertEqual(max(ratios),1)
        self.assertEqual(scene.aspectmode,'manual')
        self.assertGreaterEqual(scene.xaxis.title.font.size,16)

    def test_force_support_and_pressure_symbols(self):
        tri = {1:(V,F[:2]),2:(V,F[2:4])}
        force = viewer.faces_figure(tri, load_tags=[1], fix_tags=[2], load_vector=[0,0,-2000])
        self.assertTrue(any(t.type=='cone' and t.w[0]<0 for t in force.data))
        self.assertTrue(any('support symbol' in t.name for t in force.data))
        self.assertTrue(any(t.type=='mesh3d' and t.color==viewer.LOAD_COLOR for t in force.data))
        pressure = viewer.faces_figure(tri, load_tags=[2], load_kind='pressure')
        self.assertTrue(any(t.type=='cone' and t.w[0]<0 for t in pressure.data))
        unset = viewer.faces_figure(tri)
        self.assertFalse(any(t.type=='cone' or 'support symbol' in t.name for t in unset.data))

    def test_face_labels_use_measured_catalogue(self):
        group = SimpleNamespace(kind='single',normal=(0,0,1),radius=None,axis=None,tags=[7],group_id=3,total_area=200000)
        labels = viewer.face_labels(SimpleNamespace(groups=[group]))
        self.assertIn('Upper face',labels[7])
        self.assertNotIn('Geometry',labels[7])
        fig = viewer.faces_figure({7:(V,F[2:4])},hover=labels,show_labels=True)
        self.assertIn('Upper face',fig.data[0].hovertemplate)

    def test_measurement_panel_distinguishes_advisory_from_failure(self):
        html = ui.measurement_html([{'name':'bbox_fill','status':'FAIL','critical':False,'detail':'<unsafe>'}])
        self.assertIn('WARN',html)
        self.assertIn('&lt;unsafe&gt;',html)
        self.assertNotIn('<unsafe>',html)

if __name__ == '__main__':
    unittest.main()
