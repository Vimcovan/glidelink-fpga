"""RX board: decoded images + telemetry -> PC over UDP (see jscc_udp.py).

  images     img_pack32 in continuous mode; the DMA is re-armed right after every frame (2 buffers alternate,
             sending happens in another thread). If the PS ever falls behind, the PL frame buffer drops whole
             frames (FB_DROP), the PHY is never stalled.
  telemetry  ~10 Hz: all equalizer stages (pre = LTF1 + 20 symbols, ce / sfo / cpe = 20 symbols x 64) + status
             (pre = LTF1 + LTF2 + 20 symbols with the layout-3 bitstream: the GUI estimates SNR from LTF1 - LTF2)
             + RX spectrum from the raw ADC capture (~2 Hz): PSD inside a PHY frame and of the noise just before it
  SSCC       every PHY frame is also decoded as an SSCC baseline frame in the PL (sscc_rx, telemetry layout >= 4):
             new packets (sscc_pkt.PAY_BYTES, the length is read from the bitstream) are read from the telemetry
             slave and sent as MT_SSCC; LDPC codewords not converged / iterations are logged every 5 s
Run as root:  bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 rx_server.py'
"""
import argparse
import collections
import os
import json
import queue
import socket
import sys
import threading
import time
import numpy as np
from pynq import allocate
from jscc_rx import JsccRx, IMG_BYTES
import jscc_udp as U

ap = argparse.ArgumentParser()
ap.add_argument("--no-download", action="store_true")
ap.add_argument("--tel-hz", type=float, default=10.0)
ap.add_argument("--spec-hz", type=float, default=2.0, help="raw ADC captures per second for the spectrum (0 = off)")
ap.add_argument("--agc-guard-hz", type=float, default=2.0, help="AD9361 stuck-gain watchdog checks per second (0 = off)")
args = ap.parse_args()

rx = JsccRx(download=not args.no_download)
time.sleep(2.0)
lock = threading.Lock()                       # MMIO register writes from two threads

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", U.PORT_RX)); sock.settimeout(0.5)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
try:                                          # root: past net.core.wmem_max (~200 kB), a whole image fits
    sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_SNDBUFFORCE", 32), 4 << 20)
except OSError:
    pass
peers = {}                                    # subscriber address -> time of its last HELLO (several GUIs at once)
decim = {}                                    # subscriber address -> image decimation (HELLO <n>: every n-th image)
jpgp = {}                                     # subscriber address -> wants JPEG images (HELLO <n> jpg)


def ctrl_loop():
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.startswith(U.HELLO):
                peers[addr] = time.time(); decim[addr], jpgp[addr] = U.hello_opts(data)
        except socket.timeout:
            pass


def live_peers():
    now = time.time()
    return [a for a, t in list(peers.items()) if now - t <= 5.0]


def send(mtype, mid, payload):
    for a in live_peers():
        if mtype in (U.MT_RX_IMG, U.MT_RX_JPG) and (mid % decim.get(a, 1) or jpgp.get(a, False) != (mtype == U.MT_RX_JPG)):
            continue
        for pkt in U.chunks(mtype, mid, payload):
            try:
                sock.sendto(pkt, a)
            except OSError:
                break


# ------------------------------------------------------------------ images
txq = queue.Queue(maxsize=2)
stats = {"img_rx": 0, "img_sent": 0, "img_timeouts": 0}


def image_loop():
    bufs = [allocate(shape=(IMG_BYTES // 4,), dtype=np.uint32) for _ in range(2)]
    ch = rx.img_dma.recvchannel
    with lock:
        rx.wr("IMG_CTRL", 2)                  # continuous forwarding
    k, fid = 0, 0
    while True:
        b = bufs[k]
        ch.transfer(b)
        if not rx._wait(ch, 2.0):
            stats["img_timeouts"] += 1
            rx._dma_reset(rx.img_dma)
            continue
        b.invalidate()
        img = np.array(b).view(np.uint8).tobytes()
        fid += 1; stats["img_rx"] += 1
        try:
            txq.put_nowait((fid, img))
        except queue.Full:
            pass
        k ^= 1


def sender_loop():
    while True:
        fid, img = txq.get()
        send(U.MT_RX_IMG, fid, img)
        if any(jpgp.get(a) for a in live_peers()):          # phones (web_server.py): JPEG, ~1/10 of the bytes
            import cv2
            ok, b = cv2.imencode(".jpg", np.frombuffer(img, np.uint8).reshape(256, 256, 3)[:, :, ::-1],
                                 [cv2.IMWRITE_JPEG_QUALITY, U.STREAM_Q])
            if ok:
                send(U.MT_RX_JPG, fid, b.tobytes())
        stats["img_sent"] += 1


# ------------------------------------------------------------------ spectrum (raw ADC capture, cap_dma)
# capture triggered 2048 samples before a PHY frame start; the preamble begins ~1394 samples in (sync latency):
# [0, 1024) = noise floor (used only if >= 20 dB below the frame), [1536, 1536 + 8192) = frame
NFFT_S, FS_MHZ, FULL = 256, 20.0, 2048.0
spec = {"res": None}


def psd(x):
    """Welch PSD, Hann, 50 % overlap -> dBFS per bin (fftshift order)"""
    w = np.hanning(NFFT_S)
    n = (len(x) - NFFT_S) // (NFFT_S // 2) + 1
    seg = np.stack([x[k * NFFT_S // 2:k * NFFT_S // 2 + NFFT_S] for k in range(n)]) * w
    p = np.mean(abs(np.fft.fft(seg, axis=1)) ** 2, 0) / (np.sum(w ** 2) * FULL ** 2)
    return np.fft.fftshift(p)


def spec_loop():
    period = 1.0 / args.spec_hz
    f = np.round((np.arange(NFFT_S) - NFFT_S // 2) * FS_MHZ / NFFT_S, 4).tolist()
    avg = None
    while True:
        t0 = time.time()
        try:
            with lock:
                x = rx.capture_adc(1536 + 8192, trig="frame", timeout=0.5)
            nz, sg = x[:1024] - x[:1024].mean(), x[1536:] - x[1536:].mean()
            cur = np.stack([psd(nz), psd(sg)])
            if np.mean(abs(nz) ** 2) > 0.01 * np.mean(abs(sg) ** 2) and avg is not None:
                cur[0] = avg[0]                                                     # signal before the trigger: keep the old floor
            avg = cur if avg is None else 0.6 * avg + 0.4 * cur                    # light smoothing (linear power)
            db = 10 * np.log10(np.maximum(avg, 1e-14))
            spec["res"] = dict(f=f, noi=np.round(db[0], 1).tolist(), sig=np.round(db[1], 1).tolist(), t=time.time())
            spec["frame_db"] = float(10 * np.log10(max(avg[1].sum(), 1e-14)))     # total power in a frame
            spec["gap_db"] = float(10 * np.log10(max(avg[0].sum(), 1e-14)))       # between frames (AGC unlock)
        except Exception as e:
            print("spectrum:", e, flush=True)
        time.sleep(max(0.0, period - (time.time() - t0)))


# ------------------------------------------------------------------ SSCC baseline packets (PL sscc_rx)
SSCC_WORDS = 16360 // 4                             # set from telemetry word 11 (packet bytes) at start


def sscc_loop():
    last = rx._tr(8, 1)[0]
    while True:
        time.sleep(0.004)
        try:
            seq = int(rx._tr(8, 1)[0])
            if seq == last:
                continue
            last = seq
            buf = int(rx._tr(9, 1)[0]) & 1
            pkt = rx._tr(0x5000 + 0x1000 * buf, SSCC_WORDS).astype("<u4").tobytes()
            if int(rx._tr(8, 1)[0]) - seq >= 2:          # overwritten while reading (cannot happen at 30 fps)
                continue
            stats["sscc"] = stats.get("sscc", 0) + 1
            send(U.MT_SSCC, seq & 0xFFFFFFFF, pkt)
        except Exception as e:
            print("sscc:", e, flush=True)
            time.sleep(1.0)


_ldpc_last = None


def ldpc_report():
    """LDPC decoder counters (telemetry words 12 / 13, cumulative): codewords not converged and mean iterations
    since the last report (101 codewords per PHY frame)"""
    global _ldpc_last
    if not sscc_ok or SSCC_WORDS * 4 != 16360:
        return ""
    try:
        cur = (int(rx._tr(12, 1)[0]), int(rx._tr(13, 1)[0]), int(rx.status().get("PHY_FRAMES", 0)))
    except Exception:
        return ""
    last, _ldpc_last = _ldpc_last, cur
    if last is None or cur[2] <= last[2]:
        return ""
    ncw = 101 * (cur[2] - last[2])
    return f", ldpc fail {cur[0] - last[0]}/{ncw} cw, iter {(cur[1] - last[1]) / ncw:.2f}"


# ------------------------------------------------------------------ telemetry
def tel_loop():
    tid, last_seq = 0, None
    period = 1.0 / args.tel_hz
    while True:
        t0 = time.time()
        try:
            with lock:
                tm = rx.telemetry(stages=("pre", "ce", "sfo", "cpe"), period_ms=20)
                st = rx.status()
            if tm["seq"] != last_seq:
                last_seq = tm["seq"]; tid += 1
                meta = dict(status=st, seq=tm["seq"], start_sym=tm["start_sym"], timeouts=tm["timeouts"],
                            n=tm["n"], t=time.time(), server=dict(stats))
                if spec["res"] is not None and time.time() - spec["res"]["t"] < 3.0:
                    meta["spec"] = spec["res"]
                send(U.MT_TEL, tid, U.pack_tel(meta, {k: tm[k] for k in ("pre", "ce", "sfo", "cpe")}))
        except Exception as e:
            print("telemetry:", e, flush=True)
        time.sleep(max(0.0, period - (time.time() - t0)))


# ------------------------------------------------------------------ AGC field record (read-only): snapshot on sync loss
AGC_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agc_events.jsonl")


def tx_atten_now():
    """TX attenuation as reported by the TX board (only while the boards are cabled), else None"""
    try:
        q = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); q.settimeout(0.3)
        q.sendto(U.HELLO, ("192.168.4.1", U.PORT_TX))
        f = q.recv(128)[len(U.ATTS):].split()
        q.close()
        return float(f[0])
    except (OSError, ValueError, IndexError):
        return None


def agc_snapshot(kind, hist):
    snap = dict(t=time.time(), time=time.strftime("%Y-%m-%d %H:%M:%S"), kind=kind, history=list(hist),
                frame_db=spec.get("frame_db"), gap_db=spec.get("gap_db"), tx_atten=tx_atten_now())
    if rx.rf is not None:
        from ad9361_ps import AGC_REGS
        with lock:
            snap["regs"] = {f"0x{a:03X}": rx.rf.rd(a) for a, _ in AGC_REGS}
            snap["fast_agc_state_samples"] = [rx.rf.rd(0x2B3) & 7 for _ in range(20)]
            snap["rssi_samples"] = [rx.rf.rd(0x1A7) for _ in range(20)]
    with lock:
        snap["status"] = rx.status()
    with open(AGC_LOG, "a") as fh:
        fh.write(json.dumps(snap) + "\n")
    print(f"AGC record: {kind} at {snap['time']} (gain {snap['status']['AGC_GAIN_IDX']}, lock {snap['status']['AGC_LOCK']}, "
          f"frame-gap {(snap['frame_db'] or 0) - (snap['gap_db'] or 0):.1f} dB, TX att {snap['tx_atten']})", flush=True)


def agc_watch():
    hist = collections.deque(maxlen=60)                       # 30 s of 0.5 s samples
    last_sync, last_t, t_ok, lost = None, time.time(), time.time(), False
    while True:
        time.sleep(0.5)
        try:
            with lock:
                st = rx.status()
            now = time.time()
            rate = 0.0 if last_sync is None else (st["SYNC_CNT"] - last_sync) / (now - last_t)
            last_sync, last_t = st["SYNC_CNT"], now
            hist.append(dict(t=round(now, 2), sync=round(rate, 1), gain=st["AGC_GAIN_IDX"], lock=st["AGC_LOCK"],
                             fg=None if spec.get("frame_db") is None else round(spec["frame_db"] - spec["gap_db"], 1)))
            if rate > 5:
                t_ok = now
                if lost:
                    lost = False; agc_snapshot("recovered", hist)
            elif not lost and now - t_ok > 1.5:
                lost = True; agc_snapshot("sync_lost", hist)
        except Exception as e:
            print("agc_watch:", e, flush=True); time.sleep(2)


def agc_guard_loop():
    """AD9361 stuck-gain watchdog (ad9361_ps.AgcGuard): PHY sync rate (read only) + gain index"""
    from ad9361_ps import AgcGuard
    guard = AgcGuard(rx.rf, lock, log=lambda m: print(m, flush=True))
    print(f"AGC guard on: {args.agc_guard_hz} checks/s, reset if sync < {guard.MIN_RATE}/s for {guard.STALL} s "
          f"and gain < {guard.gmax} (at most every {guard.HOLDOFF} s)", flush=True)
    while True:
        time.sleep(1.0 / args.agc_guard_hz)
        try:
            c = guard.check()
            if c["reset"]:
                with open(AGC_LOG, "a") as fh:
                    fh.write(json.dumps(dict(t=time.time(), time=time.strftime("%Y-%m-%d %H:%M:%S"), kind="agc_reset",
                                             guard=c, resets=guard.resets)) + "\n")
        except Exception as e:
            print("agc_guard:", e, flush=True); time.sleep(2)


sscc_ok = int(rx._tr(6, 1)[0]) >= 4                  # telemetry layout 4: SSCC packet buffers
if sscc_ok:
    SSCC_WORDS = int(rx._tr(11, 1)[0]) // 4           # 3068 (convolutional code) or 4090 (LDPC)
    print("SSCC packet", 4 * SSCC_WORDS, "bytes", flush=True)
print("SSCC baseline decoder", "present" if sscc_ok else "not in this bitstream", flush=True)
for f in (ctrl_loop, image_loop, sender_loop, tel_loop) + ((spec_loop,) if args.spec_hz > 0 else ()) + ((sscc_loop,) if sscc_ok else ()) + (agc_watch,) + \
        ((agc_guard_loop,) if args.agc_guard_hz > 0 and rx.rf is not None else ()):
    threading.Thread(target=f, daemon=True).start()
print("rx_server running", flush=True)
t_rep = time.time(); last = dict(stats)
__import__("signal").signal(15, lambda *_: sys.exit(0))      # systemctl stop: run the clean-up below
try:
    while True:
        time.sleep(5)
        dt = time.time() - t_rep
        print(f"{time.strftime('%H:%M:%S')} images {(stats['img_rx'] - last['img_rx']) / dt:.1f}/s, "
              f"sent {(stats['img_sent'] - last['img_sent']) / dt:.1f}/s, timeouts {stats['img_timeouts']}, "
              f"peers {live_peers()}, frame-gap {spec.get('frame_db', 0) - spec.get('gap_db', 0):.1f} dB "
              f"(gap {spec.get('gap_db', 0):.1f} dBFS), status {rx.status()}{ldpc_report()}", flush=True)
        last, t_rep = dict(stats), time.time()
finally:
    rx.wr("IMG_CTRL", 0)
    # transfers left pending (e.g. a spectrum capture waiting for a frame) would later write into this process's freed
    # buffers, and the next process's capture would never complete
    lock.acquire(timeout=3)                  # kept: no thread starts another transfer after the reset
    rx._dma_reset(rx.cap_dma); rx._dma_reset(rx.img_dma)
    print("rx_server stopped, DMAs reset", flush=True)
