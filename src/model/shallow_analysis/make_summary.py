"""Summary of all jointly fine-tuned shallow decoders (sweep.json of joint_* runs) -> <runs>/summary.md
    python make_summary.py <runs directory>"""
import json
import sys
from pathlib import Path

R = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent   # runs directory
DESC = {
    'joint_sp_k3_n24_b0': 'B0：只有子像素上采样',
    'joint_sp_k3_n24_b1igdn': 'B1：B0 + 输入端 iGDN',
    'joint_sp_k3_n24_b2pwmid2': 'B2：B0 + 上采样后 2 个逐点块（LeakyReLU）',
    'joint_sp_k3_n24_b2pwl': 'B2-PWL：同 B2，激活为可学习分段线性',
    'joint_sp_k3_n24_b2gate': 'B2-gate：同 B2，激活为门控 x·sigmoid(ax+b)',
    'joint_sp_k3_n24_b2pwl4': 'B2-PWL4：同 B2-PWL，折点范围 ±4',
    'joint_sp_k3_n24_b2swl': 'B2-swl：同 B2，激活为 a·x + b·x·sigmoid(c·x + d)',
    'joint_sp_k3_n24_b2pwl_lin1': 'B2-PWL + 线性 3×3：B2-PWL 前加一个 64×64 线性 3×3',
    'joint_g1_lin3_pwl': 'G1：3 个线性 3×3 + 2 个 PWL 逐点块',
    'joint_g2_lin1_glu2': 'G2：1 个线性 3×3 + 2 个 SwiGLU 块',
    'joint_sp_k3_n24_pre2': 'pre 2：64×64 上 2 个残差块',
    'joint_sp_k3_n24_pre4': 'pre 4：64×64 上 4 个残差块',
    'joint_pre4_pwl': 'pre 4-PWL：pre 4 的激活换成可学习分段线性',
    'joint_pre4pwl_bneck': 'pre 4-PWL + 128×128 瓶颈残差块',
    'joint_sp_k3_n24_pre4_tail6': 'C：pre 4 + 全分辨率尾部',
    'joint_sp_k7_n24_pre4': 'A：pre 4 + 首层 7×7',
    'joint_e1_pre4_swl2': 'E1：pre 4 + 上采样后 2 个逐点块（Swish+线性）',
    'joint_e2_pre4_glu2': 'E2：pre 4 + 上采样后 2 个 SwiGLU 块',
    'joint_e3_pre4swl_glu2': 'E3：E2，pre 块激活改为 Swish+线性',
}
rows = []
dep = None
for name, desc in DESC.items():
    f = R / name / 'sweep.json'
    if not f.exists():
        rows.append(f'| {desc} | — | 未完成 | | | | | |')
        continue
    r = json.loads(f.read_text(encoding='utf-8'))
    v = r['val_div2k']; k = r.get('kodak') or {}
    g = lambda d, s: d.get(s, {}).get('shallow')
    dep = dep or (v, k, r['deployed_gmac'])
    rows.append(f"| {desc} | {r['shallow_gmac']:.3f} | {g(v, '4'):.2f} | {g(v, '10'):.2f} | {g(v, '20'):.2f} | "
                f"{g(v, 'Infinity'):.2f} | {g(k, '10'):.2f} | {g(v, '10') - v['10']['deployed']:+.2f} |")
lines = ['# 浅层解码器联合微调结果汇总（DIV2K 验证集 PSNR，dB；Kodak 为 24 张原分辨率）', '',
         '| 结构 | GMAC/帧 | 4 dB | 10 dB | 20 dB | 无噪声 | Kodak 10 dB | 10 dB 与部署版差 |',
         '|---|---|---|---|---|---|---|---|']
if dep:
    v, k, gm = dep
    lines.append(f"| 部署版解码器 | {gm:.3f} | {v['4']['deployed']:.2f} | {v['10']['deployed']:.2f} | "
                 f"{v['20']['deployed']:.2f} | {v['Infinity']['deployed']:.2f} | {k['10']['deployed']:.2f} | 0 |")
lines += rows
(R / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('\n'.join(lines))
