"""Where does a straight line between two images go in the latent space of the deployed encoder?
Fig. 1: 2-D projection of z(t) for 6 pairs: x = along the chord z0 -> z1, y = first principal direction of the part
orthogonal to the chord (both / chord length). Fig. 2: one pair in detail (images, one latent channel of z(t) and of the
chord interpolation, I/Q scatter at t = 0.5)."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402
from deepjsccq_model import latent_to_iq  # noqa: E402

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
dev = 'cuda'
dep = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)
plt.rcParams.update({'font.family': ['Microsoft YaHei', 'sans-serif'], 'font.size': 10, 'axes.edgecolor': '#c3c2b7',
                     'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e'})
BG = '#fcfcfb'
ts = torch.linspace(0, 1, 41, device=dev)
g = torch.Generator().manual_seed(7)
pairs = [tuple(torch.randperm(len(val), generator=g)[:2].tolist()) for _ in range(6)]


@torch.no_grad()
def path(i0, i1):
    x0, x1 = val[i0:i0 + 1].float() / 255, val[i1:i1 + 1].float() / 255
    xt = x0 + ts[:, None, None, None] * (x1 - x0)
    return xt, dep.encoder(xt)


def project(z):
    c = z.flatten(1).double()
    v = c[-1] - c[0]; L = v.norm(); e1 = v / L
    rel = c - c[0]
    a = rel @ e1                                    # along the chord
    res = rel - a[:, None] * e1[None]               # orthogonal part
    _, _, vt = torch.linalg.svd(res, full_matrices=False)
    e2 = vt[0]
    b = res @ e2
    if b[len(b) // 2] < 0:
        b = -b
    rest = (res - b[:, None] * e2[None]).norm(dim=1)   # what the 2-D view does not show
    return (a / L).cpu().numpy(), (b / L).cpu().numpy(), (rest / L).cpu().numpy()


# ---------------- Fig. 1: trajectories
fig, axs = plt.subplots(2, 3, figsize=(14, 8.6)); fig.patch.set_facecolor(BG); rests = []
for ax, (i0, i1) in zip(axs.flat, pairs):
    xt, z = path(i0, i1)
    a, b, rest = project(z)
    ax.set_facecolor(BG)
    ax.plot([0, 1], [0, 0], color='#52514e', ls='--', lw=1.2, zorder=1, label='线性编码器应走的直线')
    ax.plot(a, b, color='#c3c2b7', lw=1, zorder=2)
    sc = ax.scatter(a, b, c=ts.cpu().numpy(), cmap='viridis', s=28, zorder=3, edgecolor=BG, linewidth=0.6)
    for t_mark in (0, 10, 20, 30, 40):
        ax.annotate(f't={ts[t_mark].item():.2g}', (a[t_mark], b[t_mark]), textcoords='offset points',
                    xytext=(4, 6), fontsize=8, color='#52514e')
    # the two images as small insets
    for k, (xx, yy) in ((0, (0.0, 0.70)), (40, (0.80, 0.70))):
        ins = ax.inset_axes([xx, yy, 0.2, 0.27]); ins.imshow(xt[k].permute(1, 2, 0).cpu().numpy()); ins.axis('off')
    ax.set_xlim(-0.15, 1.15); ax.set_ylim(-0.12, max(0.9, b.max() * 1.6))
    ax.set_title(f'DIV2K {i0 + 801} → {i1 + 801}', fontsize=10, loc='left'); rests.append(rest.max())
    ax.grid(color='#e6e5e0', lw=0.7); ax.set_axisbelow(True)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
for ax in axs[1]:
    ax.set_xlabel('沿 z₀→z₁ 连线方向的位置 / 连线长度')
for ax in axs[:, 0]:
    ax.set_ylabel('垂直于连线方向（主偏离方向） / 连线长度')
cb = fig.colorbar(sc, ax=axs, shrink=0.6, pad=0.06); cb.set_label('输入插值位置 t')
fig.text(0.01, 0.005, f'投影平面外的剩余偏离（各对最大值）：{min(rests):.2f}–{max(rests):.2f} 个连线长度；'
         '左上、右上小图为两端的输入图像。', fontsize=9, color='#52514e')
axs[0, 0].legend(loc='lower center', frameon=False, fontsize=8.5)
fig.suptitle('输入空间的一条直线 x(t) = (1−t)·x₀ + t·x₁，经部署版编码器后在潜在空间（64×64×16）中的轨迹',
             x=0.01, ha='left', fontsize=12.5)
fig.savefig(OUT / 'latent_paths.png', dpi=150, facecolor=BG, bbox_inches='tight')

# ---------------- Fig. 2: one pair in detail
i0, i1 = pairs[0]
xt, z = path(i0, i1)
chord = z[0:1] + ts[:, None, None, None] * (z[-1:] - z[0:1])
idx = [0, 10, 20, 30, 40]
ch = int(z[[0, -1]].abs().mean(dim=(0, 2, 3)).argmax())       # a strongly used channel
vmax = float(z[:, ch].abs().max())
fig = plt.figure(figsize=(12, 8.2)); fig.patch.set_facecolor(BG)
gs = fig.add_gridspec(3, 6, width_ratios=[1, 1, 1, 1, 1, 0.08], hspace=0.12, wspace=0.08)
rows = [('输入图像 x(t)', None), (f'实际编码，通道 {ch}', z), (f'直线插值，通道 {ch}', chord)]
for r, (label, data) in enumerate(rows):
    for c, k in enumerate(idx):
        ax = fig.add_subplot(gs[r, c])
        if data is None:
            ax.imshow(xt[k].permute(1, 2, 0).cpu().numpy())
            ax.set_title(f't = {ts[k].item():.2g}', fontsize=10)
        else:
            im = ax.imshow(data[k, ch].cpu().numpy(), cmap='RdBu_r', vmin=-vmax, vmax=vmax)
        ax.set_xticks([]); ax.set_yticks([])
        if c == 0:
            ax.set_ylabel(label, fontsize=9.5, color='#0b0b0b')
cax = fig.add_subplot(gs[1:, 5]); fig.colorbar(im, cax=cax).set_label('潜变量值')
fig.suptitle(f'同一条直线的细节（DIV2K {i0 + 801} → {i1 + 801}）：第 2 行为实际编码 f(x(t))，第 3 行为两端潜变量的直线插值 (1−t)·z₀ + t·z₁（同一通道，64×64）',
             x=0.01, ha='left', fontsize=12)
fig.savefig(OUT / 'latent_detail.png', dpi=150, facecolor=BG, bbox_inches='tight')
# I/Q scatter at t = 0.5
fig, ax = plt.subplots(figsize=(6.6, 7.2)); fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
lev = (np.arange(-7, 8, 2) / np.sqrt(42))
for v in lev:
    ax.axvline(v, color='#e6e5e0', lw=0.7, zorder=0); ax.axhline(v, color='#e6e5e0', lw=0.7, zorder=0)
sel = torch.randperm(32768, generator=torch.Generator().manual_seed(1))[:3000]
for data, col, lab in ((chord, '#eb6834', '直线插值的中点 (z₀+z₁)/2'), (z, '#2a78d6', '实际编码 f(x(0.5))')):
    iq = latent_to_iq(data[20:21])[0][sel].cpu().numpy()
    p = float((latent_to_iq(data[20:21])[0] ** 2).sum(1).mean())
    ax.scatter(iq[:, 0], iq[:, 1], s=4, color=col, alpha=0.45, label=f'{lab}，平均功率 {p:.2f}', zorder=2)
ax.set_aspect('equal'); ax.set_xlim(-2, 2); ax.set_ylim(-2, 2)
ax.set_xlabel('I'); ax.set_ylabel('Q')
ax.set_title(f'DIV2K {i0 + 801} → {i1 + 801}，t = 0.5 时的 I/Q 符号\n（量化前的连续值，随机取 3000 个；网格线为 64QAM 电平）', fontsize=10, loc='left')
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.08), frameon=False, fontsize=9, markerscale=4)
for sp in ('top', 'right'):
    ax.spines[sp].set_visible(False)
fig.savefig(OUT / 'latent_iq_midpoint.png', dpi=150, facecolor=BG, bbox_inches='tight')
print('saved', OUT)
