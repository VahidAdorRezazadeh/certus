"""Is the residual 1% gap the clamped-root Poisson restraint, or something else?

Test: replace the full clamp (ux=uy=uz=0 on the whole root face) with a
statically determinate support that still fixes the section in space but lets it
contract freely in y and z:
    ux = 0 on the entire root face  (kills x-translation, y-rotation, z-rotation)
    uy = uz = 0 at the root centre node       (kills y- and z-translation)
    uz = 0 at one node offset in +y            (kills x-rotation)
If the deflection moves toward beam theory, the attribution holds.
"""
import os, cantilever_reference as C

work = os.path.dirname(os.path.abspath(__file__))
ref = C.beam_theory()["d_total"]
nx, ny, nz, quad, el = 20, 4, 4, True, "C3D20R"

nodes, elems, ids, grid = C.build_mesh(nx, ny, nz, quad)
NX, NY, NZ = grid
tol = 1e-9
root = sorted(n for n, (x, y, z) in nodes.items() if abs(x) < tol)
centre = ids[(0, NY // 2, NZ // 2)]
above  = ids[(0, NY, NZ // 2)]
probe  = ids[(NX, NY // 2, NZ // 2)]

top = [i for i, cn in enumerate(elems, 1)
       if abs(max(nodes[n][1] for n in cn[:8]) - C.H) < tol]

def deck(path, bc_lines):
    L = ["*HEADING", "soft clamp variant", "*NODE, NSET=NALL"]
    for n in sorted(nodes):
        x, y, z = nodes[n]; L.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")
    L.append(f"*ELEMENT, TYPE={el}, ELSET=EALL")
    for i, cn in enumerate(elems, 1):
        L.append(f"{i}, " + ", ".join(map(str, cn[:15])) + ",\n" + ", ".join(map(str, cn[15:])))
    L.append("*NSET, NSET=NROOT")
    for i in range(0, len(root), 8):
        L.append(", ".join(map(str, root[i:i+8])))
    L.append("*NSET, NSET=NPROBE"); L.append(str(probe))
    L += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{C.E:.1f}, {C.NU}",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL",
          "*STEP", "*STATIC", "*BOUNDARY"] + bc_lines + ["*DLOAD"]
    for i in top:
        L.append(f"{i}, P5, {C.P_TOP}")
    L += ["*NODE PRINT, NSET=NPROBE", "U", "*END STEP"]
    open(path, "w").write("\n".join(L) + "\n")

for tag, bcs in [
    ("full_clamp", ["NROOT, 1, 3, 0.0"]),
    ("soft_clamp", ["NROOT, 1, 1, 0.0",
                    f"{centre}, 2, 3, 0.0",
                    f"{above}, 3, 3, 0.0"]),
]:
    deck(os.path.join(work, tag + ".inp"), bcs)
    C.run_ccx(tag, work)
    p = C.parse_dat(os.path.join(work, tag + ".dat"))
    uy = abs(p["u"][1])
    print(f"{tag:12s}  uy = {uy:.6f} mm   {100*uy/ref:6.2f}% of beam theory")
print(f"beam theory  uy = {ref:.6f} mm")
