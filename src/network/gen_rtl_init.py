"""Generate RTL initialisation files for every conv engine of the exported model.

For each conv engine in manifest.json (memory_plan section) writes to
<export>/rtl_init/<engine>/:
  wrom.mem     weight ROM: word = P lanes x 8 bit (lane 0 in the LSBs),
               address = g*K*K*CIN + (ky*K + kx)*CIN + ci, lane p = weight of output
               channel g*P + p (0 beyond COUT)
  wrom_s<i>.mem  the same ROM cut into column slices of 9 lanes (72 bit); conv_engine
               instantiates one ROM per slice
  bias.mem, pre.mem, m.mem, sh.mem   per output channel, same formats as params/
  engine.json  RTL parameters of the engine (CIN, COUT, K, STRIDE, PAD, P, ACT, ...)
Merged engines (upsample block main + skip conv) concatenate output channels
[main | skip]; the skip channels use ACT2.

  python gen_rtl_init.py [--export data/model_shallow]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


LANES_PER_SLICE = 9          # 9 x 8-bit weights = 72 bit


def read_mem(path: Path, bits: int, signed: bool) -> np.ndarray:
    v = np.array([int(x, 16) for x in path.read_text(encoding='ascii').split()], dtype=np.int64)
    if signed:
        v = np.where(v >= (1 << (bits - 1)), v - (1 << bits), v)
    return v


def write_words(path: Path, words: list[int], bits: int):
    digits = (bits + 3) // 4
    path.write_text(''.join(f'{w:0{digits}X}\n' for w in words), encoding='ascii', newline='\n')


def act_name(a):
    return 'none' if a is None else a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default=str(Path(__file__).resolve().parents[2] / 'data' / 'model_shallow'))
    a = ap.parse_args()
    root = Path(a.export)
    m = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    ops = {o.get('name', o['out']): o for o in m['ops']}
    tensors = m['tensors']
    out_root = root / 'rtl_init'
    count = 0
    for part in ('encoder', 'decoder'):
        for e in m['memory_plan'][part]['engines']:
            if 'GDN' in e['note']:
                continue
            members = [ops[n] for n in e['ops']]
            conv = members[0]
            k = conv['k'][0]
            cin = conv['cin']
            P = e['P']
            # weights OHWI per member, concatenated along output channels
            ws, fields = [], {'bias': [], 'rq_pre': [], 'rq_M': [], 'rq_sh': []}
            for op in members:
                f = op['files']
                ws.append(read_mem(root / f['weight']['file'], f['weight']['bits'], True)
                          .reshape(op['cout'], k, k, op['cin']))
                for key in fields:
                    fields[key].append(read_mem(root / f[key]['file'], f[key]['bits'], f[key].get('signed', True)))
            w = np.concatenate(ws, 0)
            cout = w.shape[0]
            groups = -(-cout // P)
            words = []
            for g in range(groups):
                for ky in range(k):
                    for kx in range(k):
                        for ci in range(cin):
                            word = 0
                            for p in range(P):
                                co = g * P + p
                                if co < cout:
                                    word |= (int(w[co, ky, kx, ci]) & 0xFF) << (8 * p)
                            words.append(word)
            d = out_root / e['name']
            d.mkdir(parents=True, exist_ok=True)
            write_words(d / 'wrom.mem', words, 8 * P)
            # column slices of <= 9 lanes (72 bit = one BRAM 512x72 column), one ROM instance each,
            # so the tool cannot pick a wasteful aspect ratio for very wide ROMs
            # direct: 512-deep banks when deeper than 512; gear: packed 32-bit columns
            mode = e['mode']
            for f in list(d.glob('wrom_s*.mem')) + list(d.glob('wrom_g_c*.mem')):
                f.unlink()
            bank = e.get('bank', 0) if mode == 'direct' else 0
            ncol = e.get('ncol', 0)
            if mode == 'gear':
                # bytes of the vector stream packed back to back into ncol 32-bit columns
                stream = bytearray()
                for wd in words:
                    stream += int(wd).to_bytes(P, 'little')
                wb = 4 * ncol
                stream += bytes((-len(stream)) % wb)
                nd = len(stream) // wb
                for c in range(ncol):
                    col = [int.from_bytes(stream[i * wb + 4 * c: i * wb + 4 * c + 4], 'little') for i in range(nd)]
                    write_words(d / f'wrom_g_c{c}.mem', col, 32)
            for s, l0 in enumerate(range(0, P if mode != 'gear' else 0, LANES_PER_SLICE)):
                nl = min(LANES_PER_SLICE, P - l0)
                sl = [(wd >> (8 * l0)) & ((1 << (8 * nl)) - 1) for wd in words]
                if bank:
                    for b in range(0, len(sl), bank):
                        write_words(d / f'wrom_s{s}_b{b // bank}.mem', sl[b:b + bank], 8 * nl)
                else:
                    write_words(d / f'wrom_s{s}.mem', sl, 8 * nl)
            assert len(set(np.concatenate(fields['rq_pre']).tolist())) == 1, f'{e["name"]}: pre differs per channel'
            bits = {'bias': 48, 'rq_pre': 8, 'rq_M': 17, 'rq_sh': 8}
            names = {'bias': 'bias.mem', 'rq_pre': 'pre.mem', 'rq_M': 'm.mem', 'rq_sh': 'sh.mem'}
            for key, vals in fields.items():
                v = np.concatenate(vals)
                write_words(d / names[key], [int(x) & ((1 << bits[key]) - 1) for x in v], bits[key])
            info = {
                'name': e['name'], 'ops': e['ops'], 'CIN': cin, 'COUT': cout, 'K': k,
                'STRIDE': conv['stride'][0], 'PAD': conv['pad'][0], 'P': P, 'GROUPS': groups,
                'ACT': act_name(conv['act']),
                'ACT2': act_name(members[1]['act']) if len(members) > 1 else 'none',
                'ACT_SPLIT': members[0]['cout'],
                'WROM_STYLE': 'distributed' if mode == 'lut' else 'block',
                'WROM_MODE': 'gear' if mode == 'gear' else 'direct', 'WG_NCOL': max(ncol, 1),
                'WROM_DEPTH': len(words), 'WROM_WIDTH': 8 * P, 'WROM_BANK_DEPTH': bank,
                # requant multiplier (memory plan): serial LUT multiplier when the P outputs of a
                # pass can be spaced SER_II = 10 cycles apart within the next pass
                'RQ_MUL': e['rq_mul'],
                # requant shifts: pre is a layer constant, sh = SH_MIN + d with a small d
                'PRE': int(np.concatenate(fields['rq_pre'])[0]),
                'SH_MIN': int(np.concatenate(fields['rq_sh']).min()),
                'SH_BITS': int(np.concatenate(fields['rq_sh']).max() - np.concatenate(fields['rq_sh']).min()).bit_length(),
                'in': conv['in'], 'in_shape_hwc': tensors[conv['in']]['shape_hwc'],
                'out': [op['out'] for op in members],
                'out_shape_hwc': [tensors[op['out']]['shape_hwc'] for op in members],
                'note': e['note'],
            }
            (d / 'engine.json').write_text(json.dumps(info, indent=1), encoding='utf-8', newline='\n')
            count += 1
    print(f'wrote {count} conv engines to {out_root}')
    ng = gen_gdn(root, m, ops, tensors, out_root)
    na = gen_add(m, out_root)
    print(f'wrote {ng} GDN/IGDN and {na} add blocks')


def gen_gdn(root, m, ops, tensors, out_root):
    """GDN/IGDN: lane packed gamma ROM (word = pass g, column j; lane l = gamma[g*LANES+l][j]),
    beta, gdn.json with the axis_gdn parameters.  Widths from the worst case over int12 x."""
    abits = int(m['format']['activations'].split('int')[1].split()[0])
    fclk, fps = m['memory_plan']['fclk_hz'], m['memory_plan']['fps']
    budget = 0.8 * fclk / fps
    n = 0
    for part in ('encoder', 'decoder'):
        for e in m['memory_plan'][part]['engines']:
            if 'GDN' not in e['note']:
                continue
            op = ops[e['ops'][0]]
            f = op['files']
            c, lanes, inv = op['channels'], e['P'], op['op'] == 'igdn'
            g = read_mem(root / f['gamma']['file'], 8, f['gamma'].get('signed', True)).reshape(c, c)
            beta = read_mem(root / f['beta']['file'], 48, f['beta'].get('signed', True))
            xmax = 1 << (abits - 1)
            dmax = int(((g.sum(1) * (xmax >> op['x2_shift']) ** 2 + beta) << op['L']).max())
            dmin = int((beta << op['L']).min())
            assert dmin > 0
            dw = dmax.bit_length() + (dmax.bit_length() & 1)
            rmin = int(np.floor(np.sqrt(float(dmin))))
            while rmin * rmin > dmin:
                rmin -= 1
            F = op.get('F') or 0
            qw = ((xmax << F) // rmin).bit_length() if not inv else 0
            passes = -(-c // lanes)
            words = []
            for p_ in range(passes):
                for j in range(c):
                    w = 0
                    for l in range(lanes):
                        i = p_ * lanes + l
                        if i < c:
                            w |= int(g[i, j]) << (8 * l)
                    words.append(w)
            d = out_root / e['name']
            d.mkdir(parents=True, exist_ok=True)
            write_words(d / 'gamma.mem', words, 8 * lanes)
            write_words(d / 'beta.mem', [int(v) & ((1 << 48) - 1) for v in beta], 48)
            h, w_, _ = tensors[op['out']]['shape_hwc']
            lat = 1 + dw // 2 + (abits if inv else qw) + 2     # start .. taken
            nunits = max(1, int(np.ceil(lat * c * h * w_ / budget)))
            info = {
                'name': e['name'], 'op': op['op'], 'C': c, 'LANES': lanes, 'INVERSE': int(inv),
                'X2_SHIFT': op['x2_shift'], 'LSH': op['L'], 'F': F, 'QW': max(qw, 1), 'DW': dw,
                'NUNITS': nunits, 'PRE': op['rq']['pre'], 'M': op['rq']['M'], 'SH': op['rq']['sh'],
                'in': op['in'], 'out': op['out'], 'shape_hwc': [h, w_, c],
                'mac_cycles_per_frame': passes * c * h * w_, 'budget_cycles': budget,
            }
            (d / 'gdn.json').write_text(json.dumps(info, indent=1), encoding='utf-8', newline='\n')
            n += 1
    return n


def gen_add(m, out_root):
    n = 0
    for op in m['ops']:
        if op['op'] != 'add':
            continue
        d = out_root / op['out']
        d.mkdir(parents=True, exist_ok=True)
        info = {'name': op['out'], 'MA': op['Ma'], 'MB': op['Mb'], 'SH': op['sh'],
                'RELU': int(bool(op['relu'])), 'in': op['in'], 'out': op['out']}
        (d / 'add.json').write_text(json.dumps(info, indent=1), encoding='utf-8', newline='\n')
        n += 1
    return n


if __name__ == '__main__':
    main()
