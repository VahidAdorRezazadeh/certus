"""Small C3D8 cube decks shared by the tests."""
import os


def nid(n, i, j, k, off=0):
    return off + 1 + i + (n + 1) * (j + (n + 1) * k)


def top(n):
    return [nid(n, i, j, n) for i in range(n + 1) for j in range(n + 1)]


def deck(W, name, n=2, bc=("BOT, 1, 3",), loads=None, E="210000.",
         output="*NODE FILE\nU"):
    h = 10.0 / n
    L = ["*NODE"] + [f"{nid(n,i,j,k)}, {i*h}, {j*h}, {k*h}"
                     for k in range(n + 1) for j in range(n + 1)
                     for i in range(n + 1)]
    L.append("*ELEMENT, TYPE=C3D8, ELSET=EALL")
    e = 1
    for k in range(n):
        for j in range(n):
            for i in range(n):
                c = [nid(n, *p) for p in [(i,j,k),(i+1,j,k),(i+1,j+1,k),
                     (i,j+1,k),(i,j,k+1),(i+1,j,k+1),(i+1,j+1,k+1),
                     (i,j+1,k+1)]]
                L.append(f"{e}, " + ", ".join(map(str, c))); e += 1
    L += ["*NSET, NSET=BOT"] + [f"{nid(n,i,j,0)}," for i in range(n + 1)
                                for j in range(n + 1)]
    L += ["*NSET, NSET=TOP"] + [f"{t}," for t in top(n)]
    L += ["*MATERIAL, NAME=S", "*ELASTIC", f"{E}, 0.3",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=S", "*STEP", "*STATIC",
          "*BOUNDARY"] + list(bc) + ["*CLOAD"] + \
        (loads or [f"{t}, 3, -10." for t in top(n)]) + [output, "*END STEP"]
    p = os.path.join(W, name + ".inp")
    open(p, "w").write("\n".join(L) + "\n")
    return p, top(n)
