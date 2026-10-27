"""GlideLink enclosure: 3D-printed base for one PYNQ-ZU + AD-FMCOMMS3 board, laser-cut 3 mm acrylic lid.

Coordinates (mm): origin at the front-left corner of the PYNQ-ZU PCB, x to the right (power jack side), y to the back
(edge with HDMI / USB / mini DP), z up with z = 0 at the outer bottom of the base. "Front" is the FMC edge; the
AD9361 card overhangs the front edge and its four SMA jacks point further forward, out through U-slots in the front
wall (open at the top, closed by the lid), so the antennas screw onto the card directly.

Measured on the board (2026-10-24): PCB 180 x 130 (manual), mounting holes 5.01 from both edges, d 3.24; parts on
the bottom side up to 5.35; fan top 20.54 above the PCB; card PCB bottom 9.89 above the PCB, card overhang 59.43,
card left edge 9.06 from the PCB left edge; SMA axes 6.71 above the PCB, pitch 10.98, first SMA 16.67 from the card
left edge, 24.0 out of the card edge. Edge connector positions are read off the manual's board photo (~4.25 px/mm)
and given generous margins; check them with the 1:1 wall templates before ordering.

    python enclosure.py            -> out/enclosure_base.stl, out/lid_TX.dxf, out/lid_RX.dxf, out/wall_templates.pdf,
                                      out/preview_*.png
"""
import math
import os

import numpy as np
import manifold3d as m3
import trimesh
import ezdxf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- parameters
PCB_W, PCB_D, PCB_T = 180.0, 130.0, 1.6
HOLE_INSET, HOLE_D = 5.01, 3.24
T_FLOOR, T_WALL, GAP = 2.5, 2.5, 4.0          # floor, walls, PCB edge to inner wall
STANDOFF = 8.0                                 # M3 brass standoff (bottom-side parts up to 5.35)
Z_PCB = T_FLOOR + STANDOFF + PCB_T             # top of the PYNQ-ZU PCB
FAN_TOP = Z_PCB + 20.54
H_WALL = 43.5                                  # lid underside 31.4 above the PCB (HDMI with bracket 28.0, fan 20.5)
LID_T = 3.0

CARD_X0, CARD_W, CARD_OVER, CARD_D = 9.06, 73.3, 59.43, 69.0
CARD_Z = Z_PCB + 9.89                          # card PCB bottom
SMA_X = [CARD_X0 + 16.67 + k * 10.98 for k in range(4)]
SMA_Z = Z_PCB + 6.71
SMA_LEN = 24.0
SMA_SLOT_W = 8.4                               # clears the 1/4-36 barrel and a 5/16" hex
SMA_SLOT_X = (SMA_X0 := 9.06 + 16.67) - 6.0, 9.06 + 16.67 + 3 * 10.98 + 11.0   # one slot: 4 SMAs + the small jack
FRONT_IN = -CARD_OVER - 11.0                   # inner face of the front wall (about 10 mm of SMA outside)

X_IN0, X_IN1 = -GAP, PCB_W + GAP
Y_IN0, Y_IN1 = FRONT_IN, PCB_D + GAP
X_OUT0, X_OUT1 = X_IN0 - T_WALL, X_IN1 + T_WALL
Y_OUT0, Y_OUT1 = Y_IN0 - T_WALL, Y_IN1 + T_WALL
BOSS_D = 7.0                                   # round lid-screw bosses in the inner corners, kept 0.5 clear of the PCB corners
LID_SCREW_PILOT, LID_SCREW_CLEAR = 2.6, 3.4    # M3 self-tapping into the print / clearance in the lid

FAN_C, FAN_R = (93.5, 50.5), 29.0              # fan centre and grille radius (manual photo)

# edge windows: (wall, along0, along1, z0, z1) with along = x on the back / front walls, y on the side walls and z
# relative to the top of the PYNQ-ZU PCB; all include ~2 mm margin around the connector body
WINDOWS = [                                    # from the top and side photos (2026-10-24): ~7.6 / 10.9 px per mm
    ("back", 13.0, 43.0, -1.0, 23.5, "HDMI in/out"),
    ("back", 42.0, 57.0, -1.0, 8.0, "mini DP"),
    ("back", 59.5, 77.0, -1.0, 7.5, "USB 3.0 micro"),          # overlapping windows merge into one stepped opening
    ("back", 76.5, 114.0, -1.0, 19.0, "USB 3.0 x4"),
    ("back", 112.0, 131.5, -1.0, 4.5, "microSD"),
    ("back", 132.5, 144.0, -1.0, 6.0, "UART"),
    ("back", 145.0, 165.5, -1.0, 20.0, "TPM-Pmod (top entry)"),
    ("right", 88.0, 125.0, -1.0, 25.0, "12 V in + switch"),    # room for the plug latch and a finger on the switch
    ("left", 110.0, 121.0, -1.0, 7.5, "MIC/HP"),
    ("left", 97.0, 108.0, -1.0, 7.5, "LINE-IN"),
    ("left", 15.0, 60.0, -1.0, 11.0, "Pmod x2"),
]
VENTS = [("right", 22.0, 86.0, 4.0, 18.0), ("left", 64.0, 92.0, 4.0, 18.0), ("front", 100.0, 176.0, 4.0, 18.0)]
VENT_W, VENT_PITCH = 3.0, 6.0


# ---------------------------------------------------------------- CSG helpers
def box(x0, y0, z0, x1, y1, z1):
    return m3.Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def cyl_z(x, y, z0, z1, d, seg=48):
    return m3.Manifold.cylinder(z1 - z0, d / 2, d / 2, seg).translate([x, y, z0])


def cyl_y(x, z, y0, y1, d, seg=48):
    c = m3.Manifold.cylinder(y1 - y0, d / 2, d / 2, seg)          # along z, then rotate to y
    return c.rotate([-90, 0, 0]).translate([x, y0, z])


def union(parts):
    out = parts[0]
    for p in parts[1:]:
        out = out + p
    return out


def wall_cut(wall, a0, a1, z0, z1, extra=1.0):
    """box through the given wall; a = x for back/front, y for left/right; z absolute"""
    if wall == "back":
        return box(a0, Y_IN1 - extra, z0, a1, Y_OUT1 + extra, z1)
    if wall == "front":
        return box(a0, Y_OUT0 - extra, z0, a1, Y_IN0 + extra, z1)
    if wall == "right":
        return box(X_IN1 - extra, a0, z0, X_OUT1 + extra, a1, z1)
    return box(X_OUT0 - extra, a0, z0, X_IN0 + extra, a1, z1)


# ---------------------------------------------------------------- base
def base():
    shell = box(X_OUT0, Y_OUT0, 0, X_OUT1, Y_OUT1, H_WALL) - box(X_IN0, Y_IN0, T_FLOOR, X_IN1, Y_IN1, H_WALL + 1)
    posts = [cyl_z(x, y, 0, H_WALL, BOSS_D) for x, y in lid_screws()]
    card_posts = [cyl_z(x, -CARD_OVER + 3.0, 0, CARD_Z - 0.6, 6.0) for x in (CARD_X0 + 4.5, CARD_X0 + CARD_W - 4.5)]
    standoff_pads = [cyl_z(x, y, 0, T_FLOOR + 1.0, 8.0) for x, y in mount_holes()]
    body = union([shell] + posts + card_posts + standoff_pads)
    cuts = []
    for x, y in lid_screws():                                      # lid screw pilots, 12 mm deep
        cuts.append(cyl_z(x, y, H_WALL - 12, H_WALL + 1, LID_SCREW_PILOT, 24))
    for x, y in mount_holes():                                     # M3 from below into the brass standoff
        cuts.append(cyl_z(x, y, -1, T_FLOOR + 2, 3.4, 32))
        cuts.append(cyl_z(x, y, -1, 1.0, 6.5, 32))                 # head counterbore
    for wall, a0, a1, z0, z1, _ in WINDOWS:
        cuts.append(wall_cut(wall, a0, a1, Z_PCB + z0, Z_PCB + z1))
    cuts.append(box(SMA_SLOT_X[0], Y_OUT0 - 1, SMA_Z - SMA_SLOT_W / 2, SMA_SLOT_X[1], Y_IN0 + 1, H_WALL + 1))
    for wall, a0, a1, z0, z1 in VENTS:
        a = a0
        while a + VENT_W <= a1:
            cuts.append(wall_cut(wall, a, a + VENT_W, Z_PCB + z0, Z_PCB + z1))
            a += VENT_PITCH
    for x, y in [(X_OUT0 + 14, Y_OUT0 + 14), (X_OUT1 - 14, Y_OUT0 + 14), (X_OUT0 + 14, Y_OUT1 - 14),
                 (X_OUT1 - 14, Y_OUT1 - 14)]:                      # rubber feet recesses
        cuts.append(cyl_z(x, y, -1, 0.6, 12.5))
    return body - union(cuts)


def lid_screws():
    """boss centres: back corners 4.0 from the PCB corner on the diagonal (boss edge 0.5 clear of the PCB), front
    corners the same distance from the inner wall corner"""
    d = 2.83
    return [(-d, PCB_D + d), (PCB_W + d, PCB_D + d), (X_IN0 + GAP - d, Y_IN0 + GAP - d), (X_IN1 - GAP + d, Y_IN0 + GAP - d)]


def mount_holes():
    return [(HOLE_INSET, HOLE_INSET), (PCB_W - HOLE_INSET, HOLE_INSET), (HOLE_INSET, PCB_D - HOLE_INSET),
            (PCB_W - HOLE_INSET, PCB_D - HOLE_INSET)]


def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts))


# ---------------------------------------------------------------- lid (DXF, units mm, origin at the lid's corner)
def lid_dxf(path, label):
    doc = ezdxf.new("R2010", setup=True); doc.units = ezdxf.units.MM
    for name, col in (("CUT", 1), ("ENGRAVE", 5)):
        doc.layers.add(name, color=col)
    ms = doc.modelspace()
    W, D, r = X_OUT1 - X_OUT0, Y_OUT1 - Y_OUT0, 3.0
    ox, oy = -X_OUT0, -Y_OUT0                                     # board coords -> lid coords
    pts = [(r, 0), (W - r, 0), (W, r), (W, D - r), (W - r, D), (r, D), (0, D - r), (0, r)]
    ms.add_lwpolyline([(r, 0, 0), (W - r, 0, 0.414214), (W, r, 0), (W, D - r, 0.414214), (W - r, D, 0),
                       (r, D, 0.414214), (0, D - r, 0), (0, r, 0.414214)], format="xyb", close=True,
                      dxfattribs={"layer": "CUT"})
    for x, y in lid_screws():
        ms.add_circle((x + ox, y + oy), LID_SCREW_CLEAR / 2, dxfattribs={"layer": "CUT"})
    holes = grille_holes()
    for x, y, d in holes:
        ms.add_circle((x + ox, y + oy), d / 2, dxfattribs={"layer": "CUT"})
    if label:                                                    # label None: plain lid, cut layer only
        t = ms.add_text(f"GlideLink  {label}", height=7.0, dxfattribs={"layer": "ENGRAVE"})
        t.set_placement((W - 12, 14), align=ezdxf.enums.TextEntityAlignment.BOTTOM_RIGHT)
    doc.saveas(path)
    return holes


def grille_holes(d=5.0, pitch=7.0):
    out = []
    for j in range(-6, 7):
        for i in range(-6, 7):
            x = FAN_C[0] + i * pitch + (pitch / 2 if j % 2 else 0)
            y = FAN_C[1] + j * pitch * math.sqrt(3) / 2
            if math.hypot(x - FAN_C[0], y - FAN_C[1]) <= FAN_R - d / 2:
                out.append((x, y, d))
    return out


# ---------------------------------------------------------------- 1:1 wall templates (PDF, A4 landscape)
def wall_templates(path):
    walls = [("back", "后侧板（内侧看，左右与板子相同）", X_OUT0, X_OUT1),
             ("right", "右侧板（电源一侧，从外面看）", Y_OUT0, Y_OUT1),
             ("left", "左侧板（音频一侧，从外面看）", Y_OUT0, Y_OUT1),
             ("front", "前侧板（SMA 一侧，从外面看）", X_OUT0, X_OUT1)]
    plt.rcParams["font.family"] = "Microsoft YaHei"
    with PdfPages(path) as pdf:
        for wall, title, a0, a1 in walls:
            fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))
            ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 297); ax.set_ylim(0, 210); ax.axis("off")
            L = a1 - a0; ox = (297 - L) / 2; oz = 60
            flip = wall in ("right", "front")                        # outside view
            A = (lambda a: ox + (a1 - a) if flip else ox + (a - a0))
            ax.add_patch(Rectangle((ox, oz), L, H_WALL, fill=False, lw=1.0))
            ax.plot([ox, ox + L], [oz + Z_PCB, oz + Z_PCB], color="#2F5D8A", lw=0.6, ls="--")
            ax.text(ox + L + 2, oz + Z_PCB, "PCB 上表面", fontsize=7, color="#2F5D8A", va="center")
            ax.plot([ox, ox + L], [oz + Z_PCB - PCB_T, oz + Z_PCB - PCB_T], color="#2F5D8A", lw=0.4, ls=":")
            if wall in ("back", "front"):
                for e in (0.0, PCB_W):
                    ax.plot([A(e)] * 2, [oz, oz + H_WALL], color="#C8602A", lw=0.5, ls="--")
                ax.text(A(0.0), oz - 4, "PCB 左边缘", fontsize=7, color="#C8602A", ha="center")
                ax.text(A(PCB_W), oz - 4, "PCB 右边缘", fontsize=7, color="#C8602A", ha="center")
            else:
                for e in (0.0, PCB_D):
                    ax.plot([A(e)] * 2, [oz, oz + H_WALL], color="#C8602A", lw=0.5, ls="--")
                ax.text(A(0.0), oz - 4, "PCB 前边缘", fontsize=7, color="#C8602A", ha="center")
                ax.text(A(PCB_D), oz - 4, "PCB 后边缘", fontsize=7, color="#C8602A", ha="center")
            for w, b0, b1, z0, z1, name in WINDOWS:
                if w != wall:
                    continue
                xa, xb = sorted((A(b0), A(b1)))
                ax.add_patch(Rectangle((xa, oz + Z_PCB + z0), xb - xa, z1 - z0, fill=False, lw=0.8, color="k"))
                ax.text((xa + xb) / 2, oz + Z_PCB + z1 + 2, name, fontsize=6.5, ha="center")
            if wall == "front":
                xa, xb = sorted((A(SMA_SLOT_X[0]), A(SMA_SLOT_X[1])))
                ax.add_patch(Rectangle((xa, oz + SMA_Z - SMA_SLOT_W / 2), xb - xa, H_WALL - SMA_Z + SMA_SLOT_W / 2,
                                       fill=False, lw=0.8, color="k"))
                for x in SMA_X:
                    ax.add_patch(Circle((A(x), oz + SMA_Z), 3.2, fill=False, lw=0.4, ls=":"))
                ax.text((xa + xb) / 2, oz + H_WALL + 3, "SMA ×4 + 小接头（槽口向上）", fontsize=6.5, ha="center")
            ax.text(ox, 190, title, fontsize=11)
            ax.text(ox, 182, "按 100% 打印（不要“适合页面”），用尺量下方比例尺应为 50 mm。贴在板子对应边上，"
                             "开孔框应把接口完整框住。", fontsize=7.5)
            ax.plot([ox, ox + 50], [25, 25], color="k", lw=1.2)
            for e in (0, 50):
                ax.plot([ox + e] * 2, [23, 27], color="k", lw=1.2)
            ax.text(ox + 25, 28, "50 mm", fontsize=7, ha="center")
            pdf.savefig(fig); plt.close(fig)


# ---------------------------------------------------------------- previews
def preview(mesh, path, elev, azim, title):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(8, 6), dpi=110)
    ax = fig.add_subplot(111, projection="3d")
    tris = mesh.vertices[mesh.faces]
    n = mesh.face_normals
    light = np.clip(0.35 + 0.65 * (n @ np.array([0.3, -0.5, 0.8]) / np.linalg.norm([0.3, -0.5, 0.8])), 0.2, 1)
    cols = np.c_[0.62 * light, 0.66 * light, 0.72 * light, np.ones_like(light)]
    ax.add_collection3d(Poly3DCollection(tris, facecolors=cols, edgecolors="none"))
    lo, hi = mesh.bounds
    c = (lo + hi) / 2; s = (hi - lo).max() / 2
    ax.set_xlim(c[0] - s, c[0] + s); ax.set_ylim(c[1] - s, c[1] + s); ax.set_zlim(c[2] - s / 2, c[2] + s / 2)
    ax.set_box_aspect((1, 1, 0.5)); ax.view_init(elev, azim); ax.set_axis_off()
    ax.set_title(title, fontsize=10)
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    b = base()
    mesh = to_trimesh(b)
    mesh.export(os.path.join(OUT, "enclosure_base.stl"))
    lo, hi = mesh.bounds
    print(f"base: {hi[0] - lo[0]:.1f} x {hi[1] - lo[1]:.1f} x {hi[2] - lo[2]:.1f} mm, watertight {mesh.is_watertight}, "
          f"volume {mesh.volume / 1000:.1f} cm3")
    for label in ("TX", "RX"):
        holes = lid_dxf(os.path.join(OUT, f"lid_{label}.dxf"), label)
    lid_dxf(os.path.join(OUT, "lid_plain.dxf"), None)
    print(f"lid: {X_OUT1 - X_OUT0:.1f} x {Y_OUT1 - Y_OUT0:.1f} mm, {len(holes)} grille holes")
    wall_templates(os.path.join(OUT, "wall_templates.pdf"))
    preview(mesh, os.path.join(OUT, "preview_back_right.png"), 28, 35, "后方 / 右侧（电源、USB、HDMI）")
    preview(mesh, os.path.join(OUT, "preview_front_left.png"), 28, 215, "前方 / 左侧（SMA 槽、音频）")
    print("written to", OUT)


# ---------------------------------------------------------------- all four walls on one A4 sheet (1:1)
def wall_sheet(path_pdf, path_png):
    """the four walls one under the other, cut to the band around the board (PCB top -5 .. +28 mm), A4 landscape"""
    plt.rcParams["font.family"] = "Microsoft YaHei"
    ZB0, ZB1 = Z_PCB - 5.0, Z_PCB + 28.0                     # drawn band (absolute z)
    walls = [("back", "后侧板（从盒内看，左右与板子相同）"), ("right", "右侧板（电源一侧，从外面看）"),
             ("left", "左侧板（音频一侧，从外面看）"), ("front", "前侧板（SMA 一侧，从外面看）")]
    fig = plt.figure(figsize=(297 / 25.4, 210 / 25.4))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 297); ax.set_ylim(0, 210); ax.axis("off")
    H = ZB1 - ZB0
    for k, (wall, title) in enumerate(walls):
        a0, a1 = (X_OUT0, X_OUT1) if wall in ("back", "front") else (Y_OUT0, Y_OUT1)
        L = a1 - a0; ox = 12.0; top = 196.0 - k * 47.5; oz = top - 6 - H     # strip bottom (page y of z = ZB0)
        flip = wall in ("right", "front")
        A = (lambda a, a0=a0, a1=a1, flip=flip: ox + (a1 - a) if flip else ox + (a - a0))
        Z = lambda z: oz + (min(max(z, ZB0), ZB1) - ZB0)
        ax.text(ox, top - 2.5, title, fontsize=8.5, va="center")
        ax.add_patch(Rectangle((ox, oz), L, H, fill=False, lw=0.9))
        ax.plot([ox, ox + L], [Z(Z_PCB)] * 2, color="#2F5D8A", lw=0.6, ls="--")
        ax.text(ox + L + 1.5, Z(Z_PCB), "PCB 上表面", fontsize=6.5, color="#2F5D8A", va="center")
        ax.text(ox + L + 1.5, oz + H - 1.5, "↑ 侧板继续向上", fontsize=6, color="#888888", va="center")
        span = PCB_W if wall in ("back", "front") else PCB_D
        names = ("PCB 左边缘", "PCB 右边缘") if wall in ("back", "front") else ("PCB 前边缘", "PCB 后边缘")
        for e, nm in zip((0.0, span), names):
            ax.plot([A(e)] * 2, [oz, oz + H], color="#C8602A", lw=0.6, ls="--")
            ax.text(A(e), oz - 2.2, nm, fontsize=6, color="#C8602A", ha="center", va="center")
        for w, b0, b1, z0, z1, name in WINDOWS:
            if w != wall:
                continue
            xa, xb = sorted((A(b0), A(b1)))
            ya, yb = Z(Z_PCB + z0), Z(Z_PCB + z1)
            ax.add_patch(Rectangle((xa, ya), xb - xa, yb - ya, fill=False, lw=0.8, color="k"))
            ax.text((xa + xb) / 2, min(yb + 1.6, oz + H - 1.2), name, fontsize=5.5, ha="center", va="center")
        if wall == "front":
            xa, xb = sorted((A(SMA_SLOT_X[0]), A(SMA_SLOT_X[1])))
            ya = Z(SMA_Z - SMA_SLOT_W / 2)
            ax.add_patch(Rectangle((xa, ya), xb - xa, oz + H - ya, fill=False, lw=0.8, color="k"))
            for x in SMA_X:
                ax.add_patch(Circle((A(x), Z(SMA_Z)), 3.2, fill=False, lw=0.4, ls=":"))
            ax.text((xa + xb) / 2, oz + H - 2.5, "SMA ×4 + 小接头（槽向上开口）", fontsize=5.5, ha="center", va="center")
    bx = 236.0
    ax.plot([bx, bx + 50], [24, 24], color="k", lw=1.2)
    for e in (0, 50):
        ax.plot([bx + e] * 2, [22, 26], color="k", lw=1.2)
    ax.text(bx + 25, 28, "50 mm（用尺核对）", fontsize=7, ha="center")
    ax.text(bx, 12, "四条侧板只画了 PCB 附近一段（上表面以下 5 mm\n到以上 28 mm）。沿外框剪下，虚线对齐 PCB\n上表面和板边，开孔框应完整框住接口。",
            fontsize=6, va="top")
    fig.savefig(path_pdf); fig.savefig(path_png, dpi=300); plt.close(fig)


if __name__ == "__main__":
    wall_sheet(os.path.join(OUT, "wall_sheet.pdf"), os.path.join(OUT, "_sheet_print.png"))
    print("wall sheet written")
