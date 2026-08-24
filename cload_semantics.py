"""Does *CLOAD with an NSET name apply F to EACH node, or distribute F over the set?
Getting this wrong scales every load by the node count and still solves cleanly.
Test: 100 N on a tip face with a known node count; check the reaction sum."""
import os, cantilever_reference as C
work = os.path.dirname(os.path.abspath(__file__))
nodes, elems, ids, grid = C.build_mesh(20, 2, 2, False)
NX, NY, NZ = grid; tol = 1e-9
root = sorted(n for n,(x,y,z) in nodes.items() if abs(x) < tol)
tip  = sorted(n for n,(x,y,z) in nodes.items() if abs(x-C.L) < tol)
L = ["*HEADING","cload semantics","*NODE, NSET=NALL"]
for n in sorted(nodes):
    x,y,z = nodes[n]; L.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")
L.append("*ELEMENT, TYPE=C3D8, ELSET=EALL")
for i,cn in enumerate(elems,1): L.append(f"{i}, " + ", ".join(map(str,cn)))
L.append("*NSET, NSET=NFIX")
for i in range(0,len(root),8): L.append(", ".join(map(str,root[i:i+8])))
L.append("*NSET, NSET=NTIP")
for i in range(0,len(tip),8): L.append(", ".join(map(str,tip[i:i+8])))
L += ["*MATERIAL, NAME=STEEL","*ELASTIC",f"{C.E:.1f}, {C.NU}",
      "*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL",
      "*STEP","*STATIC","*BOUNDARY","NFIX, 1, 3, 0.0",
      "*CLOAD","NTIP, 2, -100.0",
      "*NODE PRINT, NSET=NFIX, TOTALS=ONLY","RF","*END STEP"]
open(os.path.join(work,"cload_test.inp"),"w").write("\n".join(L)+"\n")
C.run_ccx("cload_test", work)
p = C.parse_dat(os.path.join(work,"cload_test.dat"))
print(f"nodes in NTIP            : {len(tip)}")
print(f"requested per-line value : -100.0 N")
print(f"sum RFy from CalculiX    : {p['rf'][1]:.3f} N")
print(f"-> interpretation        : "
      f"{'PER NODE (F x n_nodes)' if abs(p['rf'][1]-100*len(tip))<1e-3 else 'DISTRIBUTED over the set' if abs(p['rf'][1]-100)<1e-3 else 'neither -- inspect'}")
