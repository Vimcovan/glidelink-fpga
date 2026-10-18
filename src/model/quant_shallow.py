"""W8A12 post-training quantization check of a jointly trained shallow decoder (train_shallow_dec.py --joint):
the fine-tuned encoder runs through the deployed integer model (int_ref.encode), the shallow decoder through an
integer graph built from the int_ref ops plus a per-channel piecewise-linear activation.

Integer PWL (the activation of the --pre blocks): the 12-bit input has a power-of-two scale, so the 9 knots
(-2 .. 2, step 0.5) fall on integers and the segment index is a shift + clamp; per channel and segment
    acc = S[c][seg] * x + I[c][seg]      (S: W-bit signed, per-channel scale; I at the accumulator scale)
    y   = satA(rsr(sat27(rsr(acc, pre)) * M[c], sh[c]))   (the conv requantization)

    python quant_shallow.py --ckpt <run>/best.pt --cache <data/cache> --kodak <data/Kodak> [--snr 10 4]
Prints PSNR of float / int encoder / int decoder / full int for the shallow pair and the deployed W8A12 model on the
same noise (DIV2K validation centre crops and Kodak).
"""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

import int_ref as IR
from deepjsccq_model import DeepJSCCQ, iq_to_latent
from ptq_int16 import symbols, to_q10
from train_shallow_dec import PWLAct, build
from train_v2 import CachedCrops

DEPLOYED = Path(__file__).resolve().parents[2] / 'data/model/checkpoint/best.pt'


# --------------------------------------------------------------------------- shallow decoder graph
def pwl_segments(act: PWLAct):
    """per channel slope / intercept of the 10 segments (x < t0, [t0, t1), ..., x >= t8)"""
    a, b = act.a.detach().double().view(-1), act.b.detach().double().view(-1)
    c = act.c.detach().double().view(a.numel(), -1)
    t = act.t.detach().double().view(-1)
    slopes, icpts = [a], [b]
    for k in range(len(t)):
        slopes.append(slopes[-1] + c[:, k]); icpts.append(icpts[-1] - c[:, k] * t[k])
    return torch.stack(slopes, 1), torch.stack(icpts, 1), t


def shallow_decode(B, dec, y):
    x = B.rx_in(y)
    for i, m in enumerate(dec.head):                       # --pre-lin: linear 3x3 convs on the received latent
        assert isinstance(m, torch.nn.Conv2d), type(m)
        x = B.conv(f'sd.lin{i}', x, m, None)
    for i, blk in enumerate(dec.pre):
        h = B.pwl(f'sd.pre{i}.a1', B.conv(f'sd.pre{i}.c1', x, blk.c1, None), blk.act1)
        h = B.pwl(f'sd.pre{i}.a2', B.conv(f'sd.pre{i}.c2', h, blk.c2, None), blk.act2)
        x = B.add(f'sd.pre{i}.out', h, x)
    m = B.gdn('sd.igdn', B.ps(B.conv('sd.conv1', x, dec.conv1.conv, None), 2), dec.act)
    r = B.ps(B.conv('sd.conv_res', x, dec.conv_res.conv, None), 2)
    h = B.add('sd.sum', m, r)
    return B.image_out(B.ps(B.conv('sd.conv2', h, dec.conv2.conv, None), 2))


class FloatB(IR.FloatBackend):
    def pwl(self, name, x, act):
        self.stats[name + '.in'] = max(self.stats.get(name + '.in', 0.0), float(x.abs().max()))
        return self.rec(name, act(x))


class IntB(IR.IntBackend):
    PWL_WBITS = None            # slope width of the PWL table (default: weight width)
    PWL_FORM = 'knot'           # 'knot': y = Y[k] + S[seg] * (x - T[k]), k = left knot of the segment (t0 for x < t0);
                                # 'icpt': y = S[seg] * x + I[seg] (slope error grows with |x|);
                                # 'float': exact float PWL on the dequantized input (reference: PWL error removed)

    def pwl(self, name, x: IR.QT, act: PWLAct):
        s_y = self.scales[name]
        if self.PWL_FORM == 'float':
            return self.out_act(name, torch.round(act(x.q * x.s).double() / s_y), s_y)
        if name not in self.plan:
            sl, ic, t = pwl_segments(act)
            sl, ic, t = sl.to(x.q.device), ic.to(x.q.device), t.to(x.q.device)
            step = float(t[1] - t[0]) / x.s                      # knot spacing in input LSB: a power of two
            assert abs(step - round(step)) < 1e-9 and (int(round(step)) & (int(round(step)) - 1)) == 0, step
            smax = 2 ** (self.PWL_WBITS - 1) - 1 if self.PWL_WBITS else self.wmax
            sw = (sl.abs().amax(1) / smax).clamp_min(1e-30)
            nk = len(t)
            anchor = torch.clamp(torch.arange(nk + 1, device=t.device) - 1, min=0)   # left knot of each segment
            yk = sl[:, 1:] * t + ic[:, 1:]                                # ordinate at knot k (segment k+1, left end)
            self.plan[name] = {'sq': torch.round(sl / sw[:, None]), 'iq': torch.round(ic / (x.s * sw[:, None])),
                               'yq': torch.round(yk / (x.s * sw[:, None])), 'tk': torch.round(t / x.s),
                               'anchor': anchor, 'sw': sw, 't0': float(t[0]) / x.s, 'step': step, 'nk': nk}
        p = self.plan[name]
        seg = torch.floor((x.q - p['t0']) / p['step']) + 1                # shift + clamp in hardware
        seg = seg.clamp(0, p['nk']).long()
        ch = torch.arange(x.q.shape[1], device=x.q.device).view(1, -1, 1, 1)
        sq = p['sq'][ch, seg]
        if self.PWL_FORM == 'knot':
            k = p['anchor'][seg]
            acc = p['yq'][ch, k] + sq * (x.q - p['tk'][k])
        else:
            acc = sq * x.q + p['iq'][ch, seg]
        v = self.requant(name, acc, (x.s * p['sw'] / s_y).cpu().numpy(), per_channel=True)
        return self.out_act(name, v, s_y)


# --------------------------------------------------------------------------- models and data
def load_models(ckpt, dev):
    ck = torch.load(ckpt, map_location='cpu', weights_only=False)
    base = torch.load(DEPLOYED, map_location='cpu', weights_only=False)
    dep = DeepJSCCQ(32, 16, 10.0, quantize=True, paper=base.get('paper_arch', True)).to(dev).eval()
    dep.load_state_dict(base['model'])
    new = DeepJSCCQ(32, 16, 10.0, quantize=True, paper=base.get('paper_arch', True)).to(dev).eval()
    new.load_state_dict(base['model']); new.encoder.load_state_dict(ck['encoder'])
    dec = build(ck['args']).to(dev).eval(); dec.load_state_dict(ck['decoder'])
    return dep, new, dec, ck


def calibrate(enc_model, dec, cache, dev, n=64, snr=10.0, seed=20260928):
    fb = FloatB()
    ds = CachedCrops(Path(cache))
    random.seed(seed); torch.manual_seed(seed)
    ids = random.sample(range(len(ds)), n)
    for i in range(0, n, 16):
        x = torch.stack([ds[j] for j in ids[i:i + 16]]).to(dev)
        idx = IR.encode(fb, enc_model, x)
        y = symbols(idx); y = y + torch.randn_like(y) * math.sqrt(10 ** (-snr / 10) / 2)
        shallow_decode(fb, dec, iq_to_latent(to_q10(y), 16, x.shape[2] // 4, x.shape[3] // 4))
    return fb.stats


def calibrate_deployed(model, cache, dev, n=64, snr=10.0, seed=20260928):
    fb = IR.FloatBackend()
    ds = CachedCrops(Path(cache))
    random.seed(seed); torch.manual_seed(seed)
    ids = random.sample(range(len(ds)), n)
    for i in range(0, n, 16):
        x = torch.stack([ds[j] for j in ids[i:i + 16]]).to(dev)
        y = symbols(IR.encode(fb, model, x)); y = y + torch.randn_like(y) * math.sqrt(10 ** (-snr / 10) / 2)
        IR.decode(fb, model, iq_to_latent(to_q10(y), 16, x.shape[2] // 4, x.shape[3] // 4))
    return fb.stats


def pwl_input_scales(scales, stats, abits):
    """the conv in front of a PWL gets a power-of-two output scale (rounded up: no extra saturation)"""
    for k in [k for k in stats if k.endswith('.in')]:
        conv = k.replace('.a1.in', '.c1').replace('.a2.in', '.c2')
        s = scales[conv]
        scales[conv] = 2.0 ** math.ceil(math.log2(s))
    return scales


def load_images(cache, kodak):
    val = torch.from_numpy(np.load(Path(cache) / 'val_center256.npy')).permute(0, 3, 1, 2).contiguous()
    kod = [torch.from_numpy(np.asarray(Image.open(p).convert('RGB')).copy()).permute(2, 0, 1)[None]
           for p in sorted(Path(kodak).glob('kodim*.png'))]
    return [val[i:i + 20] for i in range(0, len(val), 20)], kod


@torch.inference_mode()
def evaluate(images, snr, dev, new, dec, sc_new, st_new, dep, sc_dep, st_dep, wbits, abits, seed=777):
    fb, fbd = FloatB(), IR.FloatBackend()
    ib = IntB(sc_new, st_new, 32, 32, wbits, abits)
    ibd = IR.IntBackend(sc_dep, st_dep, 32, 32, wbits, abits)
    keys = ['float', 'int_enc', 'int_dec', 'int', 'dep_float', 'dep_int']
    acc = dict.fromkeys(keys, 0.0); n = 0
    gen = torch.Generator(device=dev).manual_seed(seed)
    for x in images:
        x = x.to(dev); _, _, h, w = x.shape; ref = x.double() / 255
        lat = lambda idx, nz: to_q10(iq_to_latent(symbols(idx) + nz, 16, h // 4, w // 4))
        idx_f, idx_i = IR.encode(fb, new, x), IR.encode(ib, new, x).long()
        idd_f, idd_i = IR.encode(fbd, dep, x), IR.encode(ibd, dep, x).long()
        nz = torch.randn(idx_f.shape, generator=gen, device=dev) * math.sqrt(10 ** (-snr / 10) / 2)
        dec_f = lambda q: shallow_decode(fb, dec, q).double()
        outs = {'float': dec_f(lat(idx_f, nz)), 'int_enc': dec_f(lat(idx_i, nz)),
                'int_dec': shallow_decode(ib, dec, lat(idx_f, nz)) / 255,
                'int': shallow_decode(ib, dec, lat(idx_i, nz)) / 255,
                'dep_float': IR.decode(fbd, dep, lat(idd_f, nz)).double(),
                'dep_int': IR.decode(ibd, dep, lat(idd_i, nz)) / 255}
        for k, o in outs.items():
            acc[k] += float((o - ref).square().sum())
        n += ref.numel()
    return {k: -10 * math.log10(v / n) for k, v in acc.items()}, ib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', default='data/cache')
    ap.add_argument('--kodak', default='data/Kodak')
    ap.add_argument('--snr', type=float, nargs='*', default=[10.0, 4.0])
    ap.add_argument('--wbits', type=int, default=8)
    ap.add_argument('--abits', type=int, default=12)
    ap.add_argument('--headroom', type=float, default=1.5)
    ap.add_argument('--pwl-wbits', type=int, default=0, help='PWL slope width (0: --wbits)')
    ap.add_argument('--pwl-form', default='knot', choices=['knot', 'icpt', 'float'])
    a = ap.parse_args()
    IntB.PWL_WBITS = a.pwl_wbits or None
    IntB.PWL_FORM = a.pwl_form
    torch.backends.cudnn.allow_tf32 = False; torch.backends.cuda.matmul.allow_tf32 = False
    dev = torch.device('cuda')
    dep, new, dec, ck = load_models(a.ckpt, dev)
    print(f"checkpoint epoch {ck['epoch']}, val {ck['val_psnr']:.3f}", flush=True)
    # graph check: the float graph reproduces ShallowDecoder.forward
    y = torch.randn(2, 16, 64, 64, device=dev) * 0.4
    with torch.no_grad():
        d = (shallow_decode(FloatB(), dec, to_q10(y)) - dec(to_q10(y).float() / 1024)).abs().max().item()
    print(f'float graph vs module: max |diff| {d:.2e}', flush=True)
    st_new = calibrate(new, dec, a.cache, dev)
    sc_new = pwl_input_scales(IR.scales_from_stats(st_new, a.headroom, a.abits, {'sd.igdn': 2.0}), st_new, a.abits)
    st_dep = calibrate_deployed(dep, a.cache, dev)          # as export_fpga.py (ptq_int16.calibrate)
    sc_dep = IR.scales_from_stats(st_dep, a.headroom, a.abits, {'dec.9.igdn': 2.0})
    val, kod = load_images(a.cache, a.kodak)
    for snr in a.snr:
        for name, imgs in (('DIV2K val', val), ('Kodak', kod)):
            r, ib = evaluate(imgs, snr, dev, new, dec, sc_new, st_new, dep, sc_dep, st_dep, a.wbits, a.abits)
            print(f"{name} {snr:g} dB | shallow float {r['float']:.2f}, int enc {r['int_enc']:.2f}, "
                  f"int dec {r['int_dec']:.2f}, int {r['int']:.2f} (loss {r['float'] - r['int']:.3f}) | "
                  f"deployed float {r['dep_float']:.2f}, int {r['dep_int']:.2f} (loss {r['dep_float'] - r['dep_int']:.3f}) "
                  f"| gap int {r['int'] - r['dep_int']:+.2f}", flush=True)
            sat = {k: v for k, v in ib.saturation.items() if k.startswith('sd.') and v}
            if sat:
                print('  shallow decoder saturation:', sat, flush=True)


if __name__ == '__main__':
    main()
