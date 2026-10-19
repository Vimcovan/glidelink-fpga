"""Figure 2-6 of the report: (a) output-channel parallelism P per block (pies), (b) the three weight-ROM mappings with
the number of convolution engines of each, (c) on-chip storage plan (BRAM36 planned values). Data: memory_plan of
data/model_shallow/manifest.json (encoder: DeepJSCC-Q, decoder: shallow decoder).

    python resource_plan.py   -> ../network/fig07_memory.png and ../redraw/fig2_6_resource_plan.svg
"""
import json
import os
from collections import OrderedDict

import plotstyle as S
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
PLAN = json.load(open(os.path.join(ROOT, 'data', 'model_shallow', 'manifest.json'), encoding='utf-8'))['memory_plan']
PIE = ['#a5d9e8', '#66b8ce', '#d8eab4', '#a9cf78', '#ffe29a', '#e8c45d', '#d5c7eb', '#aa91d1', '#aebfd6', '#f1bba7']
GREEN, YELLOW, WEIGHT, STORE, MUTE = '#ebf1df', '#fff2cc', '#c9dfa8', '#abd8e4', '#595959'
EDGE_G, EDGE_Y, EDGE_B = '#4f6228', '#7f6000', '#205867'
# decoder engines -> block numbers of figure 2-2 (0 linear 3x3, 1-4 PWL residual blocks, 5-6 sub-pixel upsamplings)
DEC_BLOCK = {'sd.lin0': 'dec.0', 'sd.pre0': 'dec.1', 'sd.pre1': 'dec.2', 'sd.pre2': 'dec.3', 'sd.pre3': 'dec.4',
             'sd.conv1_plus_res': 'dec.5', 'sd.conv2': 'dec.6'}


def side(name):
    e, b = PLAN[name]['engines'], PLAN[name]['buffers']
    conv = [x for x in e if 'GDN' not in str(x.get('note', ''))]
    grp = OrderedDict()
    for x in conv:
        k = '.'.join(x['name'].split('.')[:2])
        k = DEC_BLOCK.get(x['name'], DEC_BLOCK.get(k, k))
        grp[k] = grp.get(k, 0) + x['P']
    return dict(grp=grp, n=len(conv), sumP=sum(x['P'] for x in conv),
                modes={m: sum(1 for x in conv if x['mode'] == m) for m in ('lut', 'direct', 'gear')},
                W=sum(x.get('b18', 0) for x in e) / 2, A=sum(x['b18'] for x in b if x['loc'] == 'BRAM') / 2,
                U=sum(x['uram'] for x in b if x['loc'] != 'BRAM'))


def pie(ax, d, title):
    v = list(d['grp'].values()); lab = list(d['grp'])
    w, _ = ax.pie(v, colors=PIE[:len(v)], startangle=90, counterclock=False,
                  wedgeprops=dict(edgecolor='white', linewidth=0.8), radius=1.0)
    import numpy as np
    for k, (wd, val, name) in enumerate(zip(w, v, lab)):
        a = np.deg2rad((wd.theta1 + wd.theta2) / 2)
        c, s = np.cos(a), np.sin(a)
        if val / sum(v) > 0.03:
            ax.text(0.68 * c, 0.68 * s, str(val), ha='center', va='center', fontsize=10.5)
        ax.annotate(name, xy=(1.0 * c, 1.0 * s), xytext=(1.28 * c, 1.22 * s), fontsize=10,
                    ha='left' if c >= 0 else 'right', va='center',
                    arrowprops=dict(arrowstyle='-', color=MUTE, lw=0.5, shrinkA=0, shrinkB=0))
    ax.set_title(title, fontsize=12, pad=14)
    ax.set_aspect('equal')


def main():
    S.apply()
    E, D = side('encoder'), side('decoder')
    for k, d in (('encoder', E), ('decoder', D)):
        print(k, d)
    fig = plt.figure(figsize=(10.1, 8.2))
    # ---------------- (a)
    fig.text(0.02, 0.975, '(a) 输出通道并行度 P 的分配', fontsize=13, va='top')
    for x0, d, name in ((0.04, E, '编码器'), (0.53, D, '解码器')):
        ax = fig.add_axes([x0 + 0.03, 0.56, 0.27, 0.34])
        pie(ax, d, name)
        fig.text(x0 + 0.39, 0.75, 'ΣP = %d' % d['sumP'], fontsize=16, ha='center')
        fig.text(x0 + 0.39, 0.715, '%d 个卷积引擎' % d['n'], fontsize=10, ha='center', color=MUTE)
    fig.text(0.5, 0.545, '扇区内标注模块 P 合计，面积表示其在本端总 P 中的占比；GDN / IGDN 引擎不计。', fontsize=10,
             ha='center', color=MUTE)
    fig.add_artist(plt.Line2D([0.02, 0.98], [0.53, 0.53], color='#d9d9d9', lw=0.6))
    # ---------------- (b)
    ax = fig.add_axes([0.02, 0.02, 0.53, 0.49]); ax.set_xlim(0, 53); ax.set_ylim(0, 49); ax.axis('off')
    ax.text(0, 48, '(b) 权重 ROM 的三种映射', fontsize=13, va='top')
    ax.text(0, 44.6, '卷积引擎数按编码器 / 解码器标注', fontsize=10, va='top', color=MUTE)

    def box(x, y, w, h, fc, ec, t, fs=10.5):
        ax.add_patch(Rectangle((x, y), w, h, fc=fc, ec=ec, lw=0.8))
        ax.text(x + w / 2, y + h / 2, t, ha='center', va='center', fontsize=fs)

    def arrow(x1, x2, y):
        ax.add_patch(FancyArrowPatch((x1, y), (x2, y), arrowstyle='-|>', mutation_scale=8, lw=0.8, color='k'))
        ax.text(x2 + 0.8, y, 'P × 8 bit', va='center', fontsize=10.5)

    rows = (('小 ROM：分布式 LUT ROM', 'lut', 39.0), ('直接映射：并排 BRAM36 列', 'direct', 25.5),
            ('顺序读取：BRAM + 位宽转换', 'gear', 9.0))
    for t, m, y in rows:
        ax.text(0, y + 1.5, t, fontsize=11.5, va='bottom')
        ax.text(53, y + 1.5, '%d / %d' % (E['modes'][m], D['modes'][m]), fontsize=11.5, va='bottom', ha='right')
    box(2.5, 32.5, 14, 5, GREEN, EDGE_G, 'LUT ROM', 12); arrow(16.5, 25, 35)
    for k in range(3):
        box(2.5 + k * 6.3, 19, 5.5, 5, GREEN, EDGE_G, '72\nbit')
    ax.text(22.5, 21.5, '…', ha='center', va='center', fontsize=12); arrow(25, 32, 21.5)
    ax.text(2.5, 17.6, '每列 72 bit，对应 9 路 8 bit 权重', fontsize=10, va='top', color=MUTE)
    box(2.5, 2.5, 12, 5, GREEN, EDGE_G, 'BRAM\n32 bit × N 列')
    ax.add_patch(FancyArrowPatch((14.5, 5), (18, 5), arrowstyle='-|>', mutation_scale=8, lw=0.8, color='k'))
    box(18, 2.5, 10, 5, YELLOW, EDGE_Y, '位宽\n转换器'); arrow(28, 34, 5)
    # ---------------- (c)
    fig.text(0.585, 0.497, '(c) 片上存储分配', fontsize=13, va='top')
    fig.text(0.585, 0.462, 'BRAM36 规划值', fontsize=10, va='top', color=MUTE)
    cx = fig.add_axes([0.64, 0.09, 0.33, 0.25])
    xs = [0, 1]
    for x, d in zip(xs, (E, D)):
        cx.bar(x, d['W'], 0.42, color=WEIGHT, edgecolor=EDGE_G, lw=0.8)
        cx.bar(x, d['A'], 0.42, bottom=d['W'], color=STORE, edgecolor=EDGE_B, lw=0.8)
        cx.text(x, d['W'] / 2, '%g' % d['W'], ha='center', va='center', fontsize=10.5)
        cx.text(x, d['W'] + d['A'] / 2, '%g' % d['A'], ha='center', va='center', fontsize=10.5)
        cx.text(x, d['W'] + d['A'] + 3, '%g' % (d['W'] + d['A']), ha='center', va='bottom', fontsize=11.5)
    cx.axhline(100, color=MUTE, ls=(0, (5, 4)), lw=0.8)
    cx.set_ylim(0, 120); cx.set_xlim(-0.6, 1.6); cx.set_yticks([0, 25, 50, 75, 100, 120])
    cx.set_xticks(xs); cx.set_xticklabels(['编码器\n', '解码器\n'], fontsize=11.5)
    for x, d in zip(xs, (E, D)):
        cx.text(x, -26, '另用 %d URAM' % d['U'] if d['U'] else '不用 URAM', ha='center', va='top', fontsize=10, color=MUTE)
    cx.tick_params(axis='y', labelsize=10)
    for s_ in ('top', 'right'):
        cx.spines[s_].set_visible(False)
    lx = fig.add_axes([0.63, 0.355, 0.34, 0.08]); lx.axis('off'); lx.set_xlim(0, 10); lx.set_ylim(0, 2)
    lx.add_patch(Rectangle((0.0, 1.25), 0.45, 0.45, fc=WEIGHT, ec=EDGE_G, lw=0.8)); lx.text(0.7, 1.47, '权重 ROM', va='center', fontsize=10.5)
    lx.add_patch(Rectangle((4.6, 1.25), 0.45, 0.45, fc=STORE, ec=EDGE_B, lw=0.8)); lx.text(5.3, 1.47, '激活缓存（BRAM）', va='center', fontsize=10.5)
    lx.plot([0.0, 0.45], [0.45, 0.45], color=MUTE, ls=(0, (3, 2)), lw=0.8); lx.text(0.7, 0.45, '每核预算 100', va='center', fontsize=10.5)
    fig.savefig(os.path.join(HERE, '..', 'network', 'fig07_memory.png'), dpi=600)
    fig.savefig(os.path.join(HERE, '..', 'redraw', 'fig2_6_resource_plan.svg'))
    plt.close(fig)


if __name__ == '__main__':
    main()
