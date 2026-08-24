"""Does C3D8 lock, and is it shear locking?

Signature test. Shear locking severity in a bending-dominated hex is governed by
the ELEMENT length-to-height aspect ratio, not by node count. So:
  refining along the length  -> aspect ratio falls -> locking eases
  refining through the depth -> aspect ratio rises -> locking gets WORSE
If both directions improve the answer, it is not locking, it is under-resolution.
"""
import cantilever_reference as C
import os

ref = C.beam_theory()["d_total"]
work = os.path.dirname(os.path.abspath(__file__))

def run(el, quad, nx, ny, nz):
    job = f"as_{el}_{nx}_{ny}_{nz}".lower()
    nodes, elems, ids, grid = C.build_mesh(nx, ny, nz, quad)
    meta = C.write_deck(os.path.join(work, job + ".inp"), el, nodes, elems, ids, grid, quad)
    C.run_ccx(job, work)
    p = C.parse_dat(os.path.join(work, job + ".dat"))
    uy = abs(p["u"][1])
    dx, dy = C.L/nx, C.H/ny
    return meta["n_elems"], dx/dy, uy, 100*uy/ref

print(f"{'case':22s} {'nel':>6s} {'elem L/h':>9s} {'uy [mm]':>10s} {'% theory':>9s}")
print("-"*62)
print("A. refine ALONG THE LENGTH, depth fixed at ny=2")
for nx in (10, 20, 40, 80):
    n, ar, uy, pc = run("C3D8", False, nx, 2, 2)
    print(f"   C3D8 {nx:3d}x2x2{'':10s} {n:6d} {ar:9.2f} {uy:10.6f} {pc:8.2f}%")
print("B. refine THROUGH THE DEPTH, length fixed at nx=20")
for ny in (2, 4, 8, 16):
    n, ar, uy, pc = run("C3D8", False, 20, ny, 2)
    print(f"   C3D8 20x{ny:<2d}x2{'':10s} {n:6d} {ar:9.2f} {uy:10.6f} {pc:8.2f}%")
print("C. same meshes, C3D8I (incompatible modes) -- the correct cure")
for ny in (2, 4, 8, 16):
    n, ar, uy, pc = run("C3D8I", False, 20, ny, 2)
    print(f"   C3D8I 20x{ny:<2d}x2{'':9s} {n:6d} {ar:9.2f} {uy:10.6f} {pc:8.2f}%")
