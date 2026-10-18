"""Shallow decoder experiment (Yang & Mandt, "Computationally-Efficient Neural Image Compression with Shallow
Decoders", ICCV 2023) on DeepJSCC-Q: the deployed encoder is frozen, only a shallow synthesis transform is trained
on its 64-QAM symbols after AWGN, and both decoders are compared over SNR.

Shallow decoder (paper Sec. 3.2, adapted to the 64x64x16 latent and the total upsampling of 4):
    g(y) = sigmoid( conv2( act(conv1(y)) + conv_res(y) ) )
    conv1, conv_res: transposed conv 16 -> N, kernel k1, stride s1      (--no-res drops conv_res; --subpixel: k1 x k1
                     conv 16 -> N * s1^2 + PixelShuffle(s1), the form the RTL generator already supports)
    conv2:           transposed conv N -> 3,  kernel k2, stride s2 = 4 / s1
    act:             iGDN (paper's choice), LeakyReLU 1/128 or none (linear synthesis)
--pre n adds n residual 3x3 blocks (--pre-ch: on more channels than 16) on the 64x64 latent before the upsampling (denoising capacity at low resolution:
the decoder input here is noisy, unlike the quantized latent of a compression decoder). --jpeg-like: a single
transposed conv 16 -> 3, kernel k1, stride 4.

    python train_shallow_dec.py --output-dir runs/shallow_k7n24 --k1 7 --n 24 --epochs 300
    python train_shallow_dec.py --eval-only runs/shallow_k7n24/best.pt      (SNR sweep of an existing run)
    --joint: the encoder is fine-tuned as well (from the deployed weights, --enc-lr, constellation KL --kl-lambda);
    the comparison is always against the deployed encoder + decoder

Outputs in --output-dir: training.csv, best.pt / latest.pt (decoder only + its arguments), sweep.json (PSNR vs SNR
of the shallow and the deployed decoder on the DIV2K validation crops, Kodak at a few SNRs), summary.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from deepjsccq_model import GDN, LEAKY_SLOPE, DeepJSCCQ, count_parameters, iq_to_latent, latent_to_iq
from train_v2 import CachedCrops, kl_to_uniform, psnr, seed_all, tx_stats, worker_init

REPO = Path(__file__).resolve().parents[2]
INF = float("inf")                                    # noiseless channel (64-QAM symbols as sent)
SWEEP_SNR = [0, 2, 4, 6, 8, 10, 12, 15, 20, 25, 30, INF]
KODAK_SNR = [5, 10, 20, INF]


def snr_seed(snr):
    return 9999 if math.isinf(snr) else int(snr * 10)


def tconv(cin, cout, k, s):
    """transposed conv with output size = input size * s for any k >= s"""
    p = (k - s + 1) // 2
    return nn.ConvTranspose2d(cin, cout, k, stride=s, padding=p, output_padding=s - k + 2 * p)


class SubPixel(nn.Module):
    """sub-pixel form of an upsampling layer: k x k conv to cout * s^2 channels, then PixelShuffle(s). It contains the
    stride-s transposed convolutions and maps onto the existing RTL (conv engine + axis_pixel_shuffle)"""
    def __init__(self, cin, cout, k, s):
        super().__init__()
        if k % 2 == 0:
            raise ValueError("sub-pixel kernel must be odd (centred window, as the conv engine)")
        self.conv, self.s = nn.Conv2d(cin, cout * s * s, k, padding=k // 2), s

    def forward(self, x):
        return F.pixel_shuffle(self.conv(x), self.s) if self.s > 1 else self.conv(x)


class PreBlock(nn.Module):
    """residual 3x3 block on the latent (64x64): act(conv(act(conv(x)))) + x"""
    def __init__(self, c, act='leaky'):
        super().__init__()
        self.c1, self.c2 = nn.Conv2d(c, c, 3, padding=1), nn.Conv2d(c, c, 3, padding=1)
        self.act1, self.act2 = make_act(act, c), make_act(act, c)

    def forward(self, x):
        return self.act2(self.c2(self.act1(self.c1(x)))) + x


class PWLAct(nn.Module):
    """learnable piecewise-linear activation per channel: a x + b + sum_k c_k relu(x - t_k), fixed knots t_k
    (per-channel table in hardware); initialised as LeakyReLU (slope 1/128, kink at 0)"""
    def __init__(self, c, rng=2.0):
        super().__init__()
        knots = tuple(float(v) for v in torch.linspace(-rng, rng, 9))   # 9 equally spaced knots on [-rng, rng]
        self.register_buffer('t', torch.tensor(knots, dtype=torch.float32).view(1, 1, -1, 1, 1))
        self.a = nn.Parameter(torch.full((1, c, 1, 1), LEAKY_SLOPE))
        self.b = nn.Parameter(torch.zeros(1, c, 1, 1))
        ck = torch.zeros(1, c, len(knots), 1, 1); ck[:, :, len(knots) // 2] = 1 - LEAKY_SLOPE   # kink at 0
        self.c = nn.Parameter(ck)

    def forward(self, x):
        return self.a * x + self.b + (self.c * F.relu(x.unsqueeze(2) - self.t)).sum(2)


class AbsAct(nn.Module):
    """per-channel a x + b |x| + c (V shapes and ReLU-like shapes; |x| and two multiplications in hardware);
    initialised as LeakyReLU: a = (1 + s) / 2, b = (1 - s) / 2"""
    def __init__(self, c):
        super().__init__()
        self.a = nn.Parameter(torch.full((1, c, 1, 1), (1 + LEAKY_SLOPE) / 2))
        self.b = nn.Parameter(torch.full((1, c, 1, 1), (1 - LEAKY_SLOPE) / 2))
        self.c = nn.Parameter(torch.zeros(1, c, 1, 1))

    def forward(self, x):
        return self.a * x + self.b * x.abs() + self.c


class SwishLinAct(nn.Module):
    """per-channel a x + b x sigmoid(c x + d): Swish plus a linear term. The learned PWL curves (B2-PWL) are Swish-like
    (smooth dip below 0, right slope ~4x the left) but keep a negative left slope, which plain x sigmoid(.) cannot:
    left slope ~ a, right slope ~ a + b, c = sharpness, d = position. Hardware: sigmoid unit + 2 multiplications.
    Initialised close to LeakyReLU (a = 1/128, b = 1 - 1/128, c = 4, d = 0)."""
    def __init__(self, c):
        super().__init__()
        self.a = nn.Parameter(torch.full((1, c, 1, 1), LEAKY_SLOPE))
        self.b = nn.Parameter(torch.full((1, c, 1, 1), 1 - LEAKY_SLOPE))
        self.c = nn.Parameter(torch.full((1, c, 1, 1), 4.0))
        self.d = nn.Parameter(torch.zeros(1, c, 1, 1))

    def forward(self, x):
        return self.a * x + self.b * x * torch.sigmoid(self.c * x + self.d)


class GateAct(nn.Module):
    """per-channel gating x * sigmoid(a x + b) (SiLU / Swish family; existing sigmoid unit + multiplier in hardware);
    initialised as SiLU (a = 1, b = 0)"""
    def __init__(self, c):
        super().__init__()
        self.a, self.b = nn.Parameter(torch.ones(1, c, 1, 1)), nn.Parameter(torch.zeros(1, c, 1, 1))

    def forward(self, x):
        return x * torch.sigmoid(self.a * x + self.b)


PWL_RANGE = [2.0]                                    # set from --pwl-range before the model is built


def make_act(kind, c):
    return {'leaky': lambda: nn.LeakyReLU(LEAKY_SLOPE), 'pwl': lambda: PWLAct(c, PWL_RANGE[0]),
            'gate': lambda: GateAct(c), 'abs': lambda: AbsAct(c), 'swl': lambda: SwishLinAct(c)}[kind]()


class PointwiseBlock(nn.Module):
    """cheap nonlinearity without receptive field: x + 1x1 conv(act(1x1 conv(x))), c -> hidden -> c"""
    def __init__(self, c, hidden=32, act='leaky'):
        super().__init__()
        self.c1, self.c2, self.act = nn.Conv2d(c, hidden, 1), nn.Conv2d(hidden, c, 1), make_act(act, hidden)

    def forward(self, x):
        return x + self.c2(self.act(self.c1(x)))


class BottleneckBlock(nn.Module):
    """residual bottleneck at 128x128: x + 1x1(c/2 -> c)( act(3x3(act(1x1(c -> c/2)(x)))) )"""
    def __init__(self, c, act='pwl'):
        super().__init__()
        m = c // 2
        self.a, self.b, self.c = nn.Conv2d(c, m, 1), nn.Conv2d(m, m, 3, padding=1), nn.Conv2d(m, c, 1)
        self.act1, self.act2 = make_act(act, m), make_act(act, m)

    def forward(self, x):
        return x + self.c(self.act2(self.b(self.act1(self.a(x)))))


class SwiGLUBlock(nn.Module):
    """cross-channel multiplicative block: x + W3 [ (W1 x) * SiLU(W2 x) ] (1x1 convs, c -> hidden -> c)"""
    def __init__(self, c, hidden=32):
        super().__init__()
        self.w1, self.w2, self.w3 = nn.Conv2d(c, hidden, 1), nn.Conv2d(c, hidden, 1), nn.Conv2d(hidden, c, 1)

    def forward(self, x):
        return x + self.w3(self.w1(x) * F.silu(self.w2(x)))


class MidLayer(nn.Module):
    """residual 3x3 conv after the first upsampling (128x128): h + leaky(conv(h)), for high-frequency detail"""
    def __init__(self, c):
        super().__init__()
        self.conv, self.act = nn.Conv2d(c, c, 3, padding=1), nn.LeakyReLU(LEAKY_SLOPE)

    def forward(self, h):
        return h + self.act(self.conv(h))


class ShallowDecoder(nn.Module):
    def __init__(self, cin=16, n=24, k1=7, s1=2, k2=5, act="igdn", res=True, pre=0, jpeg_like=False, subpixel=False,
                 pre_ch=0, mid=0, tail=0, in_igdn=False, pw=0, pw_mid=0, pw_act='leaky', pre_lin=0, glu_mid=0,
                 pre_act='leaky', bneck_mid=0, bneck_act='pwl'):
        super().__init__()
        up = SubPixel if subpixel else tconv
        self.head = nn.Sequential(*([GDN(cin, inverse=True)] if in_igdn else []),   # cheap nonlinearities on the
                                  *[PointwiseBlock(cin) for _ in range(pw)],          # received latent (no RF)
                                  *[nn.Conv2d(cin, cin, 3, padding=1) for _ in range(pre_lin)])   # linear 3x3: RF
        if pre_ch and pre_ch != cin:                   # wider latent-resolution blocks: 3x3 conv cin -> pre_ch first
            self.pre = nn.Sequential(nn.Conv2d(cin, pre_ch, 3, padding=1), nn.LeakyReLU(LEAKY_SLOPE),
                                     *[PreBlock(pre_ch) for _ in range(pre)])
            cin = pre_ch
        else:
            self.pre = nn.Sequential(*[PreBlock(cin, pre_act) for _ in range(pre)])
        self.jpeg_like = jpeg_like
        if jpeg_like:
            self.conv1 = up(cin, 3, k1, 4)
            return
        if 4 % s1:
            raise ValueError("s1 must divide the total upsampling 4")
        self.conv1 = up(cin, n, k1, s1)
        self.conv_res = up(cin, n, k1, s1) if res else None
        self.act = {"igdn": GDN(n, inverse=True), "leaky": nn.LeakyReLU(LEAKY_SLOPE), "none": nn.Identity()}[act]
        s2 = 4 // s1
        self.mid = nn.Sequential(*[MidLayer(n) for _ in range(mid)],      # after the first upsampling (128x128):
                                 *[PointwiseBlock(n, act=pw_act) for _ in range(pw_mid)],  # 3x3 conv averaged noise
                                 *[SwiGLUBlock(n) for _ in range(glu_mid)],
                                 *[BottleneckBlock(n, bneck_act) for _ in range(bneck_mid)])
        if tail:                                       # full-resolution stage, as the last block of the deployed
            self.conv2 = up(n, tail, k2, s2)           # decoder: PixelShuffle -> LeakyReLU -> 3x3 conv to RGB
            self.tail = nn.Sequential(nn.LeakyReLU(LEAKY_SLOPE), nn.Conv2d(tail, 3, 3, padding=1))
        else:
            self.conv2 = up(n, 3, k2, s2) if (k2 > 1 or s2 > 1) else nn.Conv2d(n, 3, 1)
            self.tail = nn.Identity()

    def forward(self, y):
        y = self.pre(self.head(y))
        if self.jpeg_like:
            return torch.sigmoid(self.conv1(y))
        h = self.act(self.conv1(y))
        if self.conv_res is not None:
            h = h + self.conv_res(y)
        return torch.sigmoid(self.tail(self.conv2(self.mid(h))))


def macs_per_frame(dec: nn.Module, h=64, w=64) -> float:
    """multiply-accumulates per 256x256 frame (convolutions and GDN), counted with forward hooks"""
    tot = [0]

    def hook(m, i, o):
        if isinstance(m, (nn.Conv2d, nn.ConvTranspose2d)):
            cin, k = m.in_channels, m.kernel_size[0] * m.kernel_size[1]
            # transposed conv: every input pixel scatters cin*cout*k MACs; conv: every output pixel gathers them
            n = i[0].shape[2] * i[0].shape[3] if isinstance(m, nn.ConvTranspose2d) else o.shape[2] * o.shape[3]
            tot[0] += cin * m.out_channels * k * n
        elif isinstance(m, GDN):
            c = o.shape[1]
            tot[0] += c * c * o.shape[2] * o.shape[3]

    hs = [m.register_forward_hook(hook) for m in dec.modules()]
    with torch.no_grad():
        dec(torch.zeros(1, 16, h, w, device=next(dec.parameters()).device))
    for x in hs:
        x.remove()
    return float(tot[0])


class WeightQuant:
    """quantization-aware training of the weights (STE): before a forward pass the conv weights are replaced by their
    W-bit per-output-channel quantization (as int_ref.IntBackend.conv), the gradient of that pass is applied to the
    float weights (restore() before the optimizer step)"""
    def __init__(self, modules, bits):
        self.convs = [m for mod in modules for m in mod.modules() if isinstance(m, (nn.Conv2d, nn.ConvTranspose2d))]
        self.wmax, self.saved = 2 ** (bits - 1) - 1, None

    @torch.no_grad()
    def quantize(self):
        self.saved = [c.weight.detach().clone() for c in self.convs]
        for c in self.convs:
            w = c.weight
            dims = (1, 2, 3) if isinstance(c, nn.Conv2d) else (0, 2, 3)     # output channels: dim 0 (conv) / 1 (tconv)
            shape = (-1, 1, 1, 1) if isinstance(c, nn.Conv2d) else (1, -1, 1, 1)
            sw = (w.abs().amax(dim=dims) / self.wmax).clamp_min(1e-30).view(shape)
            w.copy_(torch.round(w / sw) * sw)

    @torch.no_grad()
    def restore(self):
        for c, w in zip(self.convs, self.saved):
            c.weight.copy_(w)
        self.saved = None


def n_params(m: nn.Module) -> int:
    return sum(p.numel() for p in m.parameters())          # frozen ones too


def load_encoder(path, device):
    ck = torch.load(path, map_location=device, weights_only=False)
    m = DeepJSCCQ(32, 16, 10.0, quantize=True, paper=ck.get("paper_arch", True)).to(device)
    m.load_state_dict(ck["model"]); m.eval()
    for p in m.parameters():
        p.requires_grad_(False)
    return m


def tx_iq(model, x):
    """encoder -> 64-QAM (straight-through: gradients reach a trainable encoder): (I/Q symbols, latent shape)"""
    z = model.encoder(x)
    return latent_to_iq(model.quantizer(z)), z.shape[1:]


def awgn(iq, snr_db):
    return iq + torch.randn_like(iq) * math.sqrt(10 ** (-snr_db / 10) / 2)


def channel_symbols(model, x, snr_db):
    """encoder -> 64-QAM -> AWGN -> noisy latent (B, 16, 64, 64), as the decoder receives it"""
    iq, shape = tx_iq(model, x)
    return iq_to_latent(awgn(iq, snr_db), *shape)


def pair_symbols(model, enc, x, snr_db):
    """received latents of the deployed encoder and of `enc` (jointly trained, or the same) with the same noise"""
    iq, shape = tx_iq(model, x)
    noisy = awgn(iq, snr_db)
    if enc is model:
        y = iq_to_latent(noisy, *shape)
        return y, y
    iq2, _ = tx_iq(enc, x)
    return iq_to_latent(noisy, *shape), iq_to_latent(iq2 + (noisy - iq), *shape)


@torch.no_grad()
def sweep(model, dec, images, snrs, seed=12345, bs=25, enc=None):
    """PSNR (MSE over all pixels) per SNR: shallow decoder (after `enc`, default the deployed encoder) and the
    deployed encoder + decoder, on the same noise"""
    enc = enc or model; out = {}
    for snr in snrs:
        s_new = s_old = 0.; n = 0
        with torch.random.fork_rng(devices=[images.device]):
            torch.manual_seed(seed + snr_seed(snr))
            for i in range(0, len(images), bs):
                x = images[i:i + bs].float().div(255)
                y_old, y_new = pair_symbols(model, enc, x, snr)
                s_new += (dec(y_new) - x).square().sum().item()
                s_old += (model.decoder(y_old) - x).square().sum().item(); n += x.numel()
        out[snr] = {"shallow": psnr(s_new / n), "deployed": psnr(s_old / n)}
    return out


@torch.no_grad()
def kodak_sweep(model, dec, root, snrs, device, repeats=3, enc=None):
    from PIL import Image
    imgs = [torch.from_numpy(np.asarray(Image.open(p).convert("RGB")).copy()).permute(2, 0, 1)
            for p in sorted(Path(root).glob("kodim*.png"))]
    enc = enc or model; out = {}
    for snr in snrs:
        s_new = s_old = 0.; n = 0
        with torch.random.fork_rng(devices=[device]):
            torch.manual_seed(777 + snr_seed(snr))
            for im in imgs:
                x = im[None].to(device).float().div(255)
                for _ in range(repeats):
                    y_old, y_new = pair_symbols(model, enc, x, snr)
                    s_new += (dec(y_new) - x).square().sum().item()
                    s_old += (model.decoder(y_old) - x).square().sum().item(); n += x.numel()
        out[snr] = {"shallow": psnr(s_new / n), "deployed": psnr(s_old / n)}
    return out


def build(a):
    PWL_RANGE[0] = a.get("pwl_range", 2.0)
    return ShallowDecoder(16, a["n"], a["k1"], a["s1"], a["k2"], a["act"], not a["no_res"], a["pre"], a["jpeg_like"],
                          a.get("subpixel", False), a.get("pre_ch", 0), a.get("mid", 0), a.get("tail", 0),
                          a.get("in_igdn", False), a.get("pw", 0), a.get("pw_mid", 0), a.get("pw_act", "leaky"),
                          a.get("pre_lin", 0), a.get("glu_mid", 0), a.get("pre_act", "leaky"),
                          a.get("bneck_mid", 0), a.get("bneck_act", "pwl"))


def report(model, dec, val, a, device, out, enc=None):
    dec.eval()
    res = {"val_div2k": sweep(model, dec, val, SWEEP_SNR, enc=enc),
           "kodak": kodak_sweep(model, dec, a["kodak"], KODAK_SNR, device, enc=enc) if Path(a["kodak"]).is_dir() else None,
           "shallow_gmac": macs_per_frame(dec) / 1e9, "deployed_gmac": macs_per_frame(model.decoder) / 1e9,
           "shallow_params": n_params(dec), "deployed_params": n_params(model.decoder)}
    if enc is not None:                                     # jointly trained encoder: transmit power, constellation use
        res["joint_tx_power"], res["joint_entropy_bits"] = tx_stats(enc, val)
    (out / "sweep.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"decoder MAC/frame: shallow {res['shallow_gmac']:.3f} G vs deployed {res['deployed_gmac']:.3f} G; "
          f"params {res['shallow_params']:,} vs {res['deployed_params']:,}")
    print("SNR dB | shallow | deployed | diff  (DIV2K val, PSNR dB)")
    for snr, r in res["val_div2k"].items():
        print(f"{snr:6} | {r['shallow']:7.2f} | {r['deployed']:8.2f} | {r['shallow'] - r['deployed']:+.2f}")
    if enc is not None:
        print(f"joint encoder: P = {res['joint_tx_power']:.3f}, constellation entropy {res['joint_entropy_bits']:.2f} bit")
    if res["kodak"]:
        for snr, r in res["kodak"].items():
            print(f"Kodak {snr} dB: shallow {r['shallow']:.2f} deployed {r['deployed']:.2f}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoder", default=str(REPO / "data/model/checkpoint/best.pt"), help="deployed checkpoint")
    ap.add_argument("--cache", default="data/cache")
    ap.add_argument("--kodak", default="data/Kodak")
    ap.add_argument("--output-dir")
    ap.add_argument("--eval-only", default="", help="checkpoint of a shallow decoder: SNR sweep only")
    ap.add_argument("--n", type=int, default=24, help="hidden channels N")
    ap.add_argument("--k1", type=int, default=7)
    ap.add_argument("--s1", type=int, default=2, choices=[1, 2, 4])
    ap.add_argument("--k2", type=int, default=5)
    ap.add_argument("--act", default="igdn", choices=["igdn", "leaky", "none"])
    ap.add_argument("--no-res", action="store_true")
    ap.add_argument("--pre", type=int, default=0, help="residual 3x3 blocks on the 64x64 latent")
    ap.add_argument("--mid", type=int, default=0, help="residual 3x3 conv layers on N channels after the first "
                    "upsampling (128x128 for s1 = 2)")
    ap.add_argument("--tail", type=int, default=0, help="channels of a full-resolution stage: the last upsampling "
                    "gives this many channels at 256x256, then LeakyReLU and a 3x3 conv to RGB (0: last upsampling -> RGB)")
    ap.add_argument("--in-igdn", action="store_true", help="iGDN on the received latent (inverse of the encoder's GDN)")
    ap.add_argument("--pw", type=int, default=0, help="pointwise nonlinear blocks on the latent: 1x1 16->32, LeakyReLU, "
                    "1x1 32->16, residual (no receptive field)")
    ap.add_argument("--pw-mid", type=int, default=0, help="pointwise nonlinear blocks (1x1 N->32, LeakyReLU, 1x1 "
                    "32->N, residual) after the first upsampling: nonlinearity after the first linear averaging")
    ap.add_argument("--bneck-mid", type=int, default=0, help="residual bottleneck blocks after the first upsampling "
                    "(128x128): 1x1 N->N/2, act, 3x3, act, 1x1 N/2->N")
    ap.add_argument("--bneck-act", default="pwl", choices=["leaky", "pwl", "gate", "abs", "swl"])
    ap.add_argument("--glu-mid", type=int, default=0, help="SwiGLU blocks after the first upsampling: "
                    "x + W3[(W1 x) * SiLU(W2 x)], hidden 32 (cross-channel products)")
    ap.add_argument("--pre-act", default="leaky", choices=["leaky", "pwl", "gate", "abs", "swl"],
                    help="activation inside the --pre residual blocks")
    ap.add_argument("--pwl-range", type=float, default=2.0, help="PWL knots: 9 equally spaced on [-r, r]")
    ap.add_argument("--pre-lin", type=int, default=0, help="linear 3x3 convs (16->16, no activation) on the received "
                    "latent: cheap receptive field (noise averaging) before the upsampling")
    ap.add_argument("--pw-act", default="leaky", choices=["leaky", "pwl", "gate", "abs", "swl"],
                    help="activation inside the --pw-mid blocks: LeakyReLU, learnable piecewise linear, gated (SiLU-like)")
    ap.add_argument("--pre-ch", type=int, default=0, help="channels of those blocks (default: the 16 latent channels; "
                    "more: a 3x3 conv 16 -> pre-ch in front)")
    ap.add_argument("--jpeg-like", action="store_true")
    ap.add_argument("--subpixel", action="store_true", help="k x k conv + PixelShuffle instead of transposed conv "
                    "(odd k; deployable with the existing RTL: k1 = 3 or 5 roughly matches a transposed k1 = 6 or 10)")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--plateau-patience", type=int, default=10)
    ap.add_argument("--snr-db", type=float, default=10.0, help="training SNR (deployed decoder: 10 dB; inf: noiseless)")
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--joint", action="store_true", help="fine-tune the encoder too (initialised from the deployed one)")
    ap.add_argument("--enc-lr", type=float, default=1e-4)
    ap.add_argument("--kl-lambda", type=float, default=0.05, help="--joint: KL(P(c) || uniform), as the deployed model")
    ap.add_argument("--init-decoder", default="", help="start from this shallow decoder checkpoint (same structure), "
                    "e.g. the one trained with the frozen encoder")
    ap.add_argument("--qat-wbits", type=int, default=0, help="quantization-aware training of the conv weights of the "
                    "decoder (and the encoder with --joint) at this width, per output channel (0: off)")
    ap.add_argument("--resume-joint", default="", help="--joint: start decoder AND encoder from this joint checkpoint "
                    "(continue a joint fine-tune; the optimizer state restarts)")
    args = ap.parse_args()
    seed_all(args.seed)
    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda")
    model = load_encoder(args.encoder, device)
    cache = Path(args.cache)
    val = torch.from_numpy(np.load(cache / "val_center256.npy")).permute(0, 3, 1, 2).contiguous().to(device)

    if args.eval_only:
        ck = torch.load(args.eval_only, map_location=device, weights_only=False)
        a = {**ck["args"], "kodak": args.kodak}
        dec = build(a).to(device); dec.load_state_dict(ck["decoder"])
        enc = None
        if "encoder" in ck:
            enc = load_encoder(args.encoder, device); enc.encoder.load_state_dict(ck["encoder"])
        report(model, dec, val, a, device, Path(args.eval_only).parent, enc)
        return

    if not args.output_dir:
        raise SystemExit("--output-dir is required for training")
    a = vars(args)
    dec = build(a).to(device)
    if args.init_decoder:
        dec.load_state_dict(torch.load(args.init_decoder, map_location=device, weights_only=False)["decoder"])
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    if (out / "training.csv").exists():
        raise SystemExit(f"{out} already has training.csv; use a new --output-dir")
    (out / "args.json").write_text(json.dumps(a, indent=2), encoding="utf-8")
    print(f"shallow decoder: {count_parameters(dec):,} params, {macs_per_frame(dec) / 1e9:.3f} GMAC/frame "
          f"(deployed decoder {macs_per_frame(model.decoder) / 1e9:.3f} GMAC/frame)", flush=True)
    tl = DataLoader(CachedCrops(cache), args.batch_size, shuffle=True, num_workers=args.workers, pin_memory=True,
                    persistent_workers=args.workers > 0, worker_init_fn=worker_init)
    enc = model                                             # frozen deployed encoder, or a trainable copy (--joint)
    groups = [{"params": dec.parameters(), "lr": args.lr}]
    if args.joint:
        enc = load_encoder(args.encoder, device)
        for p in enc.encoder.parameters():
            p.requires_grad_(True)
        enc.quantizer.set_update_step(10 ** 9)              # sigma_q at its final value (100), as at the end of training
        if args.resume_joint:
            rj = torch.load(args.resume_joint, map_location=device, weights_only=False)
            dec.load_state_dict(rj["decoder"]); enc.encoder.load_state_dict(rj["encoder"])
        groups.append({"params": enc.encoder.parameters(), "lr": args.enc_lr})
    opt = torch.optim.Adam(groups)
    wq = WeightQuant([dec] + ([enc.encoder] if args.joint else []), args.qat_wbits) if args.qat_wbits else None
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, factor=0.5, patience=args.plateau_patience)
    best = -1e9
    with (out / "training.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["epoch", "train_mse", "val_psnr", "deployed_val_psnr", "lr", "seconds"])
        wr.writeheader()
        ref = sweep(model, model.decoder, val, [args.snr_db])[args.snr_db]["deployed"]
        for epoch in range(1, args.epochs + 1):
            t0 = time.time(); dec.train(); sm = 0.; cnt = 0
            if args.joint:
                enc.quantizer.train()                       # batch constellation probabilities for the KL term
            for xb in tl:
                if wq:
                    wq.quantize()
                x = xb.to(device, non_blocking=True).float().div_(255)
                if args.joint:
                    y = channel_symbols(enc, x, args.snr_db)
                    mse = F.mse_loss(dec(y), x)
                    loss = mse + args.kl_lambda * kl_to_uniform(enc.quantizer.probs)
                else:
                    with torch.no_grad():
                        y = channel_symbols(model, x, args.snr_db)  # frozen encoder, fresh noise every batch
                    loss = mse = F.mse_loss(dec(y), x)
                opt.zero_grad(set_to_none=True); loss.backward()
                if wq:
                    wq.restore()                            # STE: quantized-weight gradient on the float weights
                opt.step()
                sm += mse.item() * len(x); cnt += len(x)
            dec.eval(); enc.eval()
            if wq:
                wq.quantize()                               # validate (and pick best.pt) with quantized weights
            v = sweep(model, dec, val, [args.snr_db], enc=enc)[args.snr_db]["shallow"]
            if wq:
                wq.restore()
            sched.step(-v)
            row = {"epoch": epoch, "train_mse": sm / cnt, "val_psnr": v, "deployed_val_psnr": ref,
                   "lr": opt.param_groups[0]["lr"], "seconds": time.time() - t0}
            wr.writerow(row); f.flush()
            ck = {"epoch": epoch, "decoder": dec.state_dict(), "args": a, "val_psnr": v}
            if args.joint:
                ck["encoder"] = enc.encoder.state_dict()
            torch.save(ck, out / "latest.pt")
            if v > best:
                best = v; torch.save(ck, out / "best.pt")
            print(f"epoch={epoch} mse={row['train_mse']:.6f} val={v:.3f} (deployed {ref:.3f}) "
                  f"lr={row['lr']:.1e} t={row['seconds']:.1f}s", flush=True)
    ck = torch.load(out / "best.pt", map_location=device, weights_only=False)
    dec.load_state_dict(ck["decoder"])
    if args.joint:
        enc.encoder.load_state_dict(ck["encoder"])
    res = report(model, dec, val, a, device, out, enc if args.joint else None)
    (out / "summary.json").write_text(json.dumps({"best_epoch": ck["epoch"], "best_val_psnr": best,
                                                  "shallow_gmac": res["shallow_gmac"],
                                                  "deployed_gmac": res["deployed_gmac"]}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
