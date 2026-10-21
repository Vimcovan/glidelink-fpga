"""PSNR vs SNR (AWGN simulation, DIV2K validation 100 crops): JPEG + LDPC / convolutional code + 64-QAM against
DeepJSCC-Q (W8A12 integer model, int_sweep.json)

    python sscc_sim_fig.py <sscc_sim.json> <int_sweep.json> <out.png>
"""
import json
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

ss = json.load(open(sys.argv[1], encoding='utf-8'))
dj = json.load(open(sys.argv[2], encoding='utf-8'))['psnr_db']

fig, ax = plt.subplots(figsize=(5.6, 3.6), dpi=300)
x = sorted(float(k) for k in dj if k[0].isdigit())
ax.plot(x, [dj[f'{v:g}']['int'] for v in x], color='#1f4e8c', marker='o', ms=3, lw=1.8, label='DeepJSCC-Q（整数模型）')
style = {'ldpc1/2': ('#e08a1e', 'LDPC 1/2'), 'ldpc2/3': ('#c0504d', 'LDPC 2/3'), 'ldpc3/4': ('#8064a2', 'LDPC 3/4'),
         'ldpc5/6': ('#4f8f3a', 'LDPC 5/6'), 'cc': ('#7f7f7f', '卷积码 1/2（部署基线）')}
for code, (c, lab) in style.items():
    if code not in ss:
        continue
    p = ss[code]['psnr_db']
    xs = sorted(float(k) for k in p)
    ax.plot(xs, [p[f'{v:g}']['psnr'] for v in xs], color=c, lw=1.3,
            label=f"JPEG + {lab}（{ss[code]['payload_bytes']} B）")
ax.set_xlim(0, 30); ax.set_ylim(10, 34)
ax.set_xlabel('信噪比 SNR (dB)'); ax.set_ylabel('PSNR (dB)')
ax.grid(alpha=0.3, lw=0.5)
ax.legend(fontsize=6.5, loc='lower right')
fig.tight_layout(); fig.savefig(sys.argv[3], bbox_inches='tight', pad_inches=0.03)
