"""Shaded previews of the enclosure base with stand-in boards (z-buffer rasterizer, numpy only).

    python render.py   -> out/render_*.png
"""
import os

import numpy as np
import trimesh
from PIL import Image

import enclosure as E

OUT = E.OUT


def mesh_of(m, color):
    t = E.to_trimesh(m)
    return t, np.array(color, float)


def stand_ins():
    """PYNQ-ZU PCB, fan block, AD9361 card and its SMAs, as simple solids"""
    parts = []
    pcb = E.box(0, 0, E.Z_PCB - E.PCB_T, E.PCB_W, E.PCB_D, E.Z_PCB)
    parts.append(mesh_of(pcb, (0.82, 0.18, 0.18)))
    fan = E.box(E.FAN_C[0] - 31, E.FAN_C[1] - 32, E.Z_PCB, E.FAN_C[0] + 31, E.FAN_C[1] + 32, E.FAN_TOP)
    parts.append(mesh_of(fan, (0.15, 0.15, 0.16)))
    card = E.box(E.CARD_X0, -E.CARD_OVER, E.CARD_Z, E.CARD_X0 + E.CARD_W, -E.CARD_OVER + E.CARD_D, E.CARD_Z + 1.6)
    parts.append(mesh_of(card, (0.35, 0.62, 0.25)))
    for x in E.SMA_X:
        s = E.cyl_y(x, E.SMA_Z, -E.CARD_OVER - E.SMA_LEN, -E.CARD_OVER + 6, 6.35, 24)
        parts.append(mesh_of(s, (0.86, 0.66, 0.2)))
    for wall, a0, a1, z0, z1, _ in E.WINDOWS:                      # connector bodies (inset 2 mm from the windows)
        if wall == "back":
            c = E.box(a0 + 2, E.PCB_D - 12, E.Z_PCB, a1 - 2, E.PCB_D + 1, E.Z_PCB + z1 - 2)
        elif wall == "right":
            c = E.box(E.PCB_W - 12, a0 + 2, E.Z_PCB, E.PCB_W + 1, a1 - 2, E.Z_PCB + z1 - 2)
        else:
            c = E.box(-1, a0 + 2, E.Z_PCB, 12, a1 - 2, E.Z_PCB + z1 - 2)
        parts.append(mesh_of(c, (0.72, 0.72, 0.74)))
    return parts


def render(meshes, path, az, el, size=(1100, 820), ss=2, zoom=1.0):
    W, H = size[0] * ss, size[1] * ss
    a, e = np.radians(az), np.radians(el)
    fwd = np.array([np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)])      # from target towards the camera
    right = np.cross([0, 0, 1], fwd); right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    allv = np.vstack([m.vertices for m, _ in meshes])
    ctr = (allv.min(0) + allv.max(0)) / 2
    P = (allv - ctr) @ np.c_[right, up]
    scale = zoom * 0.9 * min(W / np.ptp(P[:, 0]), H / np.ptp(P[:, 1]))
    img = np.ones((H, W, 3)) * np.array([0.97, 0.975, 0.98])
    zb = np.full((H, W), -np.inf)
    light = np.array([0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
    for mesh, col in meshes:
        v = mesh.vertices - ctr
        sx = v @ right * scale + W / 2
        sy = H / 2 - v @ up * scale
        dz = v @ fwd
        n = mesh.face_normals
        shade = 0.45 + 0.55 * np.clip(np.abs(n @ light), 0, 1)
        for f, s in zip(mesh.faces, shade):
            xs, ys, zs = sx[f], sy[f], dz[f]
            x0, x1 = int(max(np.floor(xs.min()), 0)), int(min(np.ceil(xs.max()), W - 1))
            y0, y1 = int(max(np.floor(ys.min()), 0)), int(min(np.ceil(ys.max()), H - 1))
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-9:
                continue
            l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
            l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
            l2 = 1 - l0 - l1
            inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
            if not inside.any():
                continue
            z = l0 * zs[0] + l1 * zs[1] + l2 * zs[2]
            sub = zb[y0:y1 + 1, x0:x1 + 1]
            upd = inside & (z > sub)
            sub[upd] = z[upd]
            img[y0:y1 + 1, x0:x1 + 1][upd] = col * s
    # dark outline where depth jumps (silhouettes and creases)
    g = np.zeros_like(zb); fin = np.isfinite(zb)
    zz = np.where(fin, zb, zb[fin].min() - 50)
    g[1:, :] = np.maximum(g[1:, :], np.abs(np.diff(zz, axis=0)))
    g[:, 1:] = np.maximum(g[:, 1:], np.abs(np.diff(zz, axis=1)))
    img[g > 1.5 * ss / scale * 4] *= 0.35
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    im.save(path)


if __name__ == "__main__":
    base = (E.to_trimesh(E.base()), np.array([0.80, 0.82, 0.86]))
    boards = stand_ins()
    render([base], os.path.join(OUT, "render_base_front_left.png"), az=-140, el=32)
    render([base], os.path.join(OUT, "render_base_back_right.png"), az=40, el=32)
    render([base] + boards, os.path.join(OUT, "render_fit_front_left.png"), az=-140, el=32)
    render([base] + boards, os.path.join(OUT, "render_fit_back_right.png"), az=40, el=32)
    print("written", OUT)
