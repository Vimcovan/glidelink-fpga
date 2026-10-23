"""GPU reference for the deployed network (jointly trained encoder + shallow decoder): latency, throughput and energy
per frame on a desktop GPU, to compare with the FPGA implementation.

GPUs have no 8-bit x 12-bit integer datapath, so the network runs in the GPU's own precisions:
    fp32    float32, TF32 off
    fp16    float16 weights and activations (channels_last)
    w8a12   float32 with W8A12 fake quantization: per-output-channel 8-bit weights (quantized once), every conv input
            rounded and clamped to 12 bits with a per-tensor scale from a calibration pass. Same numerics class as the
            FPGA model, float32 cost plus the quantization ops (not bit-exact: int_ref.py is the bit-exact reference)
Encoder = image (uint8 on the GPU) -> 64QAM symbols (hard decision); decoder = symbols -> image (uint8). Batch-1 latency
uses CUDA graphs (no Python or launch overhead); throughput sweeps the batch size. Board power of the GPU is sampled
with nvidia-smi (power.draw.instant, 100 ms): idle, paced at 30 fps (the FPGA operating point) and flat out.
The host CPU, memory and PCIe copies are not included (images are generated on the GPU).

    python bench_gpu.py --cache <data/cache> [--out data/measurements/gpu_bench/rtx5060ti.json]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

import quant_shallow as Q

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------- GPU power sampling (nvidia-smi)
class Power:
    def __init__(self):
        self.p = subprocess.Popen(['nvidia-smi', '--query-gpu=power.draw.instant', '--format=csv,noheader,nounits',
                                   '-lms', '100'], stdout=subprocess.PIPE, text=True)
        self.samples, self.on = [], False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                v = float(line.strip())
            except ValueError:
                continue
            if self.on:
                self.samples.append(v)

    def measure(self, fn, seconds):
        """run fn() repeatedly for `seconds`; returns (mean power W, calls)"""
        self.samples, n = [], 0
        torch.cuda.synchronize(); self.on = True
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            fn(); n += 1
        torch.cuda.synchronize(); self.on = False
        return statistics.mean(self.samples), n, time.perf_counter() - t0

    def close(self):
        self.p.terminate()


# ---------------------------------------------------------------------- W8A12 fake quantization
def fake_quant(modules, calib_fn, abits=12, wbits=8):
    """8-bit per-channel weights (in place) and a 12-bit per-tensor input quantizer on every Conv2d"""
    convs = [m for root in modules for m in root.modules() if isinstance(m, torch.nn.Conv2d)]
    amax = {m: 0.0 for m in convs}
    hooks = [m.register_forward_pre_hook(lambda m, i: amax.__setitem__(m, max(amax[m], i[0].abs().max().item()))) for m in convs]
    calib_fn()
    for h in hooks:
        h.remove()
    qa, qw = 2 ** (abits - 1) - 1, 2 ** (wbits - 1) - 1
    with torch.no_grad():
        for m in convs:
            s = m.weight.abs().amax(dim=(1, 2, 3), keepdim=True).clamp_min(1e-12) / qw
            m.weight.copy_(torch.round(m.weight / s).clamp(-qw - 1, qw) * s)
            sa = max(amax[m], 1e-12) / qa
            m.register_forward_pre_hook(lambda m, i, sa=sa: (torch.round(i[0] / sa).clamp(-qa - 1, qa) * sa,))
    return len(convs)


# ---------------------------------------------------------------------- the network as encode / decode functions
def make_fns(enc_model, dec, dtype):
    k = math.sqrt(42.0)                                     # unit-power 64QAM: levels (+-1, +-3, +-5, +-7) / sqrt(42)

    def encode(img_u8):                                     # (B, 3, 256, 256) uint8 -> (B, 16, 64, 64) 64QAM symbols
        z = enc_model.encoder(img_u8.to(dtype) / 255)
        return ((torch.round((z * k + 7) / 2).clamp(0, 7) * 2 - 7) / k).to(dtype)   # hard decision as deployed

    def decode(sym, noise=None):                            # symbols (+ channel noise) -> (B, 3, 256, 256) uint8
        y = sym if noise is None else sym + noise.to(dtype)
        return (dec(y.contiguous(memory_format=torch.channels_last)).clamp(0, 1) * 255).round().to(torch.uint8)

    return encode, decode


def graphed(fn, example, warm=3):
    """CUDA graph of fn(example) with a static input / output; returns run() that replays it"""
    s = torch.cuda.Stream(); s.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(s):
        for _ in range(warm):
            fn(example)
    torch.cuda.current_stream().wait_stream(s)
    g = torch.cuda.CUDAGraph()
    with torch.cuda.graph(g):
        out = fn(example)
    return g, out


def latency(g, n=300):
    ev = [(torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)) for _ in range(n)]
    for _ in range(20):
        g.replay()
    torch.cuda.synchronize()
    for a, b in ev:
        a.record(); g.replay(); b.record()
    torch.cuda.synchronize()
    t = sorted(a.elapsed_time(b) for a, b in ev)
    return {"median_ms": t[n // 2], "p99_ms": t[int(n * 0.99)], "min_ms": t[0]}


def psnr_check(encode, decode, val, snr_db=10.0, seed=1):
    """mean PSNR over the validation images through an AWGN channel (unit-power symbols)"""
    g = torch.Generator(device='cuda').manual_seed(seed)
    out = []
    for k in range(0, len(val), 8):
        x = val[k:k + 8]
        sym = encode(x)
        sig = math.sqrt(10 ** (-snr_db / 10) / 2)          # per real dimension, unit-power complex symbols
        y = decode(sym, torch.randn(sym.shape, device='cuda', generator=g) * sig)
        mse = ((y.float() - x.float()) ** 2).mean(dim=(1, 2, 3))
        out += (10 * torch.log10(255 ** 2 / mse)).tolist()
    return float(np.mean(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', default=str(REPO / 'data/model_shallow/checkpoint/best.pt'))
    ap.add_argument('--cache', required=True, help='folder with val_center256.npy (DIV2K validation centre crops)')
    ap.add_argument('--out', default=str(REPO / 'data/measurements/gpu_bench/gpu_bench.json'))
    ap.add_argument('--batches', default='1,2,4,8,16,32,64')
    ap.add_argument('--power-s', type=float, default=20.0)
    a = ap.parse_args()
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.allow_tf32 = False; torch.backends.cuda.matmul.allow_tf32 = False
    dev = torch.device('cuda')
    val = torch.from_numpy(np.load(Path(a.cache) / 'val_center256.npy')).permute(0, 3, 1, 2).contiguous().to(dev)
    res = {"gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "cudnn": torch.backends.cudnn.version(),
           "modes": {}}
    pw = Power()
    try:
        run(a, dev, val, res, pw)
    finally:
        pw.close()
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1), encoding='utf-8')
    print('written', out)


def run(a, dev, val, res, pw):
    time.sleep(3)
    p_idle, _, _ = pw.measure(lambda: time.sleep(0.05), 8.0)
    res["idle_w"] = p_idle
    print(f"{res['gpu']}: idle {p_idle:.1f} W", flush=True)
    for mode in ('fp32', 'fp16', 'w8a12'):
        _, new, dec, _ = Q.load_models(a.ckpt, dev)
        enc_model = new
        dtype = torch.float16 if mode == 'fp16' else torch.float32
        for m in (enc_model, dec):
            m.to(dtype).eval()
            for c in m.modules():                           # channels_last for the conv weights (4-D) only
                if isinstance(c, torch.nn.Conv2d):
                    c.weight.data = c.weight.data.contiguous(memory_format=torch.channels_last)
        encode, decode = make_fns(enc_model, dec, dtype)
        r = {}
        with torch.inference_mode():
            if mode == 'w8a12':
                r["convs_quantized"] = fake_quant([enc_model.encoder, dec], lambda: [decode(encode(val[k:k + 8]))
                                                                                       for k in range(0, 64, 8)])
            r["psnr_10db"] = psnr_check(encode, decode, val)
            x1 = val[:1].clone()
            ge, idx1 = graphed(encode, x1)
            gd, _ = graphed(decode, idx1.clone())
            gf, _ = graphed(lambda x: decode(encode(x)), x1)
            r["latency_b1"] = {"encoder": latency(ge), "decoder": latency(gd), "total": latency(gf)}
            thr = {}
            for b in [int(v) for v in a.batches.split(',')]:
                xb = val[torch.arange(b) % len(val)].clone()
                g, _ = graphed(lambda x: decode(encode(x)), xb)
                for _ in range(5):
                    g.replay()
                torch.cuda.synchronize(); t0 = time.perf_counter(); n = 0
                while time.perf_counter() - t0 < 2.0:
                    g.replay(); n += 1
                torch.cuda.synchronize()
                thr[b] = b * n / (time.perf_counter() - t0)
                del g
            r["throughput_fps"] = thr
            bbest = max(thr, key=thr.get)
            gb, _ = graphed(lambda x: decode(encode(x)), val[torch.arange(bbest) % len(val)].clone())
            p_max, calls, dt = pw.measure(gb.replay, a.power_s)
            fps_max = bbest * calls / dt
            nxt = [time.perf_counter()]

            def paced():                                    # one frame every 1/30 s, batch 1 (the FPGA operating point)
                gf.replay(); torch.cuda.synchronize()
                nxt[0] += 1 / 30
                time.sleep(max(0.0, nxt[0] - time.perf_counter()))
            nxt[0] = time.perf_counter()
            p_30, calls30, dt30 = pw.measure(paced, a.power_s)
            r["power"] = {"max": {"batch": bbest, "fps": fps_max, "w": p_max, "mj_per_frame": 1000 * p_max / fps_max,
                                  "dyn_mj_per_frame": 1000 * (p_max - p_idle) / fps_max},
                          "paced_30fps": {"fps": calls30 / dt30, "w": p_30, "mj_per_frame": 1000 * p_30 / (calls30 / dt30),
                                          "dyn_mj_per_frame": 1000 * (p_30 - p_idle) / (calls30 / dt30)}}
        res["modes"][mode] = r
        L = r["latency_b1"]
        print(f"{mode}: PSNR(10 dB) {r['psnr_10db']:.2f} | b1 latency enc {L['encoder']['median_ms']:.2f} dec "
              f"{L['decoder']['median_ms']:.2f} total {L['total']['median_ms']:.2f} ms (p99 {L['total']['p99_ms']:.2f}) | "
              f"max {fps_max:.0f} fps @ b{bbest}, {p_max:.1f} W, {r['power']['max']['mj_per_frame']:.2f} mJ/frame | "
              f"30 fps: {p_30:.1f} W, {r['power']['paced_30fps']['mj_per_frame']:.0f} mJ/frame", flush=True)
        del gb, gf, ge, gd, enc_model, dec, new
        torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
