"""Portrait back (docs/architecture.md): the common rooms' entrances
seen from both sides. The guardian portrait (kit_portrait_guardian_a) was built of
faces facing the corridor only, so from inside common room A it was culled to a thin
gilt outline with the corridor showing through. Each entrance piece must now show a
face toward the viewer across its whole opening from the corridor (+z, the piece's
front) and from the common room (-z): rays along z through the opening must first meet
a face facing them (a face seen from behind is culled, so the ray would see through).
The knocker door's leaves, which turn open, must be closed oak slabs (but their bottoms).
"""

import os
import re
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
sys.path[:0] = [BLENDER, os.path.join(BLENDER, "world")]

from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

REPO = os.path.dirname(os.path.dirname(BLENDER))
KIT = os.path.join(REPO, "data", "world", "castle_kit.json")
# The common rooms' entrances: door surface id -> the piece hung in its corridor-side arch.
ENTRANCES = {
    "cr_a_portrait": "kit_portrait_guardian_a",
    "cr_b_barrels": "kit_barrel_stack",
    "cr_c_door": "kit_door_knocker_a",
    "cr_d_wall": "kit_door_stone_hidden",
}
STEP = 0.04  # sample spacing (m)
MARGIN = 0.06  # samples stay this far inside the arch (the guardian's frame is inset 3 cm)
OFFSET = 0.0037  # the sample grid's offset off round numbers


def opening(door: str) -> tuple[float, float]:
    """The (width, apex) of the arch castle_kit.json puts in `door`'s opening."""
    sizes = set()
    with open(KIT) as fh:
        text = fh.read()
    for m in re.finditer(r'"door":\s*"%s"[^}]*"piece":\s*"kit_stone_arch_([0-9.]+)x([0-9.]+)"' % re.escape(door), text):
        sizes.add((float(m.group(1)), float(m.group(2))))
    if len(sizes) != 1:
        raise AssertionError(f"{door}: arches {sorted(sizes)}")
    return sizes.pop()


def inside(pts: np.ndarray, poly) -> np.ndarray:
    """Even-odd test of points (N x 2) in the polygon [(x, y), ...]."""
    x, y = pts[:, 0], pts[:, 1]
    out = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    for i in range(n):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
        cross = (ya > y) != (yb > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = xa + (y - ya) * (xb - xa) / (yb - ya)
        out ^= cross & (x < xi)
    return out


def samples(width: float, apex: float, step: float = STEP, margin: float = MARGIN) -> np.ndarray:
    """Grid points inside the pointed opening (width x apex, bottom centre at the origin), `margin` inside."""
    # Off the round-number grid by OFFSET, so no ray runs exactly along a shelf's or a rail's edge.
    xs = np.arange(-width / 2 + margin + OFFSET, width / 2 - margin, step)
    ys = np.arange(margin + OFFSET, apex - margin, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    shrunk = [(x, y + margin) for x, y in geom.lancet_outline(width - 2 * margin, apex - 2 * margin, 16)]
    return pts[inside(pts, shrunk)]


def seen(faces, pts: np.ndarray):
    """For rays along z through `pts` (N x 2): (front, back, hit), whether the first face
    met coming from +z faces +z, the first coming from -z faces -z, and any face is met."""
    zmax = np.full(len(pts), -np.inf)
    zmin = np.full(len(pts), np.inf)
    up = np.zeros(len(pts), dtype=bool)
    down = np.zeros(len(pts), dtype=bool)
    for f in faces:
        n = f.normal()
        if abs(n[2]) < 1e-6:
            continue
        poly = [(p[0], p[1]) for p in f.pts]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        box = ((pts[:, 0] >= min(xs) - 1e-9) & (pts[:, 0] <= max(xs) + 1e-9)
               & (pts[:, 1] >= min(ys) - 1e-9) & (pts[:, 1] <= max(ys) + 1e-9))
        if not box.any():
            continue
        idx = np.nonzero(box)[0]
        m = inside(pts[idx], poly)
        idx = idx[m]
        if not len(idx):
            continue
        x0, y0, z0 = f.pts[0]
        z = z0 - (n[0] * (pts[idx, 0] - x0) + n[1] * (pts[idx, 1] - y0)) / n[2]
        hi = z > zmax[idx]
        zmax[idx[hi]] = z[hi]
        up[idx[hi]] = n[2] > 0
        lo = z < zmin[idx]
        zmin[idx[lo]] = z[lo]
        down[idx[lo]] = n[2] < 0
    return up, down, np.isfinite(zmax)


def boundary_edges(faces) -> list:
    """Edges used by exactly one face (endpoints rounded to 0.1 mm)."""
    count: dict = {}
    for f in faces:
        n = len(f.pts)
        for i in range(n):
            a = tuple(round(c, 4) for c in f.pts[i])
            b = tuple(round(c, 4) for c in f.pts[(i + 1) % n])
            k = (a, b) if a <= b else (b, a)
            count[k] = count.get(k, 0) + 1
    return [k for k, c in count.items() if c == 1]


class EntrancesBothWaysTests(unittest.TestCase):
    def test_every_entrance_is_seen_from_both_sides(self):
        for door, name in sorted(ENTRANCES.items()):
            with self.subTest(door=door):
                w, h = opening(door)
                piece = P.build(name, **P.CATALOGUE[name])
                pts = samples(w, h)
                self.assertGreater(len(pts), 1000)
                front, back, _ = seen(piece.faces, pts)
                # From the corridor (+z) and from the common room (-z), the whole opening.
                self.assertEqual(int((~front).sum()), 0, f"{name}: see-through from the corridor")
                self.assertEqual(int((~back).sum()), 0, f"{name}: see-through from the common room")

    def test_the_guardian_has_a_back(self):
        p = P.build("kit_portrait_guardian_a", **P.CATALOGUE["kit_portrait_guardian_a"])
        backs = [f for f in p.faces if f.normal()[2] < -0.99]
        self.assertTrue(backs)
        # Oak from the castle trim sheet (the door's existing trim node; palette frame_oak).
        self.assertTrue(all(f.tag == "castle_trim|fit:frame_oak" for f in backs))
        # Inside the reveal, behind the canvas, and the trim budget kept.
        zs = [pt[2] for f in p.faces for pt in f.pts]
        self.assertLess(max(zs), 0.0)
        self.assertGreaterEqual(min(zs), -0.5)
        self.assertLessEqual(p.triangles(), 1200)
        # The outer rim runs the frame's whole depth (no slit between front and back).
        rim = [f for f in p.faces if f.tag == "castle_trim|fit:frame_gilt" and abs(f.normal()[2]) < 1e-6
               and min(pt[2] for pt in f.pts) <= p.meta["back"] + 1e-9]
        self.assertTrue(rim)

    def test_the_knocker_leaves_are_closed_slabs(self):
        p = P.build("kit_door_knocker_a")
        for leaf in G.ENTRANCE_LEAVES:
            wood = [f for f in p.faces if f.tag.startswith("castle_wood") and f.group == G.LEAF_TAG.format(leaf)]
            self.assertGreater(len(wood), 2)
            open_edges = [e for e in boundary_edges(wood) if not (abs(e[0][1]) < 1e-6 and abs(e[1][1]) < 1e-6)]
            self.assertEqual(open_edges, [], leaf)
            self.assertLessEqual(p.triangles(), G.KNOCKER_DOOR_TRIS)

    def test_budgets(self):
        self.assertLessEqual(P.build("kit_barrel_stack").triangles(), G.BARREL_STACK_TRIS)
        self.assertLessEqual(P.build("kit_door_stone_hidden").triangles(), G.STONE_DOOR_TRIS)

    def test_the_check_finds_a_one_sided_door(self):
        # A front-only painting (the guardian as it was) fails from the common room.
        w, h = opening("cr_a_portrait")
        faces: list = []
        geom.add_face(faces, [(x, y, -0.3) for x, y in geom.lancet_outline(w, h, 8)], "castle_trim|fit:frame_gilt",
                      None, (0.0, 0.0, 1.0))
        front, back, hit = seen(faces, samples(w, h))
        self.assertTrue(front.all() and hit.all())
        self.assertFalse(back.any())


# Portrait back, round 2: the other front-only pieces seen from behind. (piece, args,
# the door whose opening its back must close: a door in castle_kit.json, or None for a
# passage door, secrets.DOOR_W x DOOR_H.)
BEHIND = (
    ("kit_entrance_painting", {"subject": 0}, None),  # P3, both ends: seen from inside the passage
    ("kit_entrance_painting", {"subject": 1}, None),
    ("kit_entrance_panel", {"case": 0}, None),  # P2's corridor end
    ("kit_entrance_panel", {"case": 1, "seed": 104, "house": 0}, None),  # P2's Trophy Room end
    ("kit_door_painting", {"width": 4.8, "canvas": 55}, "kitchen_painting_door"),  # seen from the kitchens
)


class SeenFromBehindTests(unittest.TestCase):
    def test_pieces_over_doors_are_seen_from_both_sides(self):
        from castle_kit.pieces import secrets as SP

        for name, kw, door in BEHIND:
            with self.subTest(piece=name, args=kw):
                w, h = opening(door) if door else (SP.DOOR_W, SP.DOOR_H)
                pts = samples(w, h)
                front, back, _ = seen(P.build(name, **kw).faces, pts)
                self.assertEqual(int((~front).sum()), 0, f"{name}: see-through from its room")
                self.assertEqual(int((~back).sum()), 0, f"{name}: see-through from behind")

    def test_the_backs_stay_in_the_pieces_depth(self):
        # Flat boards just off the wall face: never in front of the piece's front, never
        # behind the wall plane (a sliding panel's back would cut through the jamb).
        for name, kw, _ in BEHIND:
            p = P.build(name, **kw)
            backs = [f for f in p.faces if f.normal()[2] < -0.99 and min(pt[2] for pt in f.pts) < 0.02]
            self.assertTrue(backs, name)
            for f in backs:
                for pt in f.pts:
                    self.assertGreater(pt[2], 0.0, name)
                    self.assertLess(pt[2], 0.02, name)
            self.assertTrue(all(G.parse_face_tag(f.tag).mode == "kit" for f in backs), name)  # palette names

    def test_the_staff_table_is_seen_from_the_dais(self):
        p = P.build("kit_table_staff_16")
        hl, top0 = p.meta["length"] / 2.0, p.meta["height"] - 0.08
        xs, ys = np.meshgrid(np.arange(-hl + 0.2, hl - 0.2, 0.05), np.arange(0.12, top0 - 0.02, 0.04))
        pts = np.stack([xs.ravel(), ys.ravel()], axis=1)
        front, back, _ = seen(p.faces, pts)
        self.assertTrue(front.all())
        self.assertEqual(int((~back).sum()), 0, "the staff table's front board seen from behind")
        self.assertTrue(p.walkthrough)
        self.assertLessEqual(p.triangles(), 600 * 16 / 12)

    def test_budgets_round_2(self):
        self.assertLessEqual(P.build("kit_door_painting", width=4.8, canvas=55).triangles(), 100)
        self.assertLessEqual(P.build("kit_entrance_panel", case=1, seed=104, house=0).triangles(), G.TROPHY_CASE_TRIS)
        for name, kw, _ in BEHIND:
            self.assertTrue(P.build(name, **kw).walkthrough, name)


class EntranceNodeTests(unittest.TestCase):
    """The backs are part of the moving entrance nodes and keep the cells' draws."""

    @classmethod
    def setUpClass(cls):
        from castle_kit import assemble as A

        cls.b = A.Builder.from_files()

    def test_guardian_back_moves_with_the_painting(self):
        cb = self.b.build("l7_corridor_north")
        mine = [f for f in cb.faces if f.src == "hero:entrance_cr_a_portrait"]
        self.assertTrue(mine)
        self.assertTrue(all(f.group == "entrance_cr_a_portrait" for f in mine))
        # Facing the common room (-z in the world: the painting hangs at yaw 0).
        backs = [f for f in mine if geom.unit(geom.newell(f.pts))[2] < -0.99]
        self.assertTrue(backs)
        self.assertTrue(all(f.material == "castle_trim" for f in backs))
        self.assertLessEqual(cb.draws(), G.BUDGETS["corridor"].draws)

    def test_passage_back_moves_with_its_entrance(self):
        cb = self.b.build("trophy_room")
        mine = [f for f in cb.faces if f.src == "hero:entrance_sp_panel_door_trophy"]
        self.assertTrue(any(geom.unit(geom.newell(f.pts))[0] > 0.99 for f in mine))  # yaw pi/2: its back faces +x
        self.assertTrue(all(f.group == "entrance_sp_panel_door_trophy" and f.material == "castle_props" for f in mine))
        self.assertLessEqual(cb.draws(), G.BUDGETS[self.b.kit_plan.cells["trophy_room"].budget].draws)

    def test_barrel_backing_slides_with_the_stack(self):
        cb = self.b.build("basement_east")
        mine = [f for f in cb.faces if f.src == "hero:entrance_cr_b_barrels"]
        self.assertTrue(any(f.material == "castle_props" and f.arg.endswith("@soot") for f in mine))
        self.assertTrue(all(f.group == "entrance_cr_b_barrels" for f in mine))
        self.assertLessEqual(cb.draws(), G.BUDGETS["corridor"].draws)


if __name__ == "__main__":
    unittest.main()
