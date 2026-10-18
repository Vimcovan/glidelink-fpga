"""TX board: USB camera -> 256x256 RGB -> DeepJSCC encoder (OFDM_JSCC_PS_TX overlay), plus a preview stream of the
exact frames handed to the encoder to the PC (UDP, see jscc_udp.py).

  camera  MJPG 640x480 @30, manual exposure/gain (defaults tuned on the bench: exposure 300 = 30 ms, gain 63)
  frame   centre crop 480x480 -> INTER_AREA 256x256 -> RGB uint8 (H, W, C) = encoder NHWC order
  send    only NEW camera frames are sent (no repeats); the encoder back-pressures the DMA (~30 fps max)
  source  camera, demo video (--video, looped) or preset images (--presets), chosen in the RX GUI and kept in
          tx_source.txt; the camera is optional (without one, or without a saved choice, the demo video is sent)
Run as root:  bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 tx_camera.py [--exposure 300 --gain 63]'
"""
import argparse
import os
import socket
import threading
import time
import cv2
import numpy as np
from jscc_tx import JsccTx
import jscc_udp as U
import sscc_pkt
import ad9361_lo

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--exposure", type=float, default=320)      # V4L2 exposure_absolute, 100 us units
ap.add_argument("--gain", type=float, default=63)
# colour (V4L2 units of this webcam; driver defaults 55 / 36 / 140 looked grey): tuned on the bench 2026-10-06
ap.add_argument("--saturation", type=int, default=105)
ap.add_argument("--contrast", type=int, default=44)
ap.add_argument("--gamma", type=int, default=130)
ap.add_argument("--auto", action="store_true", help="auto exposure instead of manual")
ap.add_argument("--no-preview", action="store_true")
ap.add_argument("--video", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "colorful_256.mp4"),
                help="demo video (selectable from the GUI instead of the camera, looped)")
ap.add_argument("--presets", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "presets"),
                help="still test images (256 x 256 PNG, sorted by name), cycled after the demo video by the GUI")
ap.add_argument("--att", type=float, default=0.0,             # TX1 attenuation at start (the config LUT sets 4 dB;
                help="TX attenuation at start, dB")             # EVM measured unchanged at 0 dB)
ap.add_argument("--http-port", type=int, default=8081, help="MJPEG preview for phones (/tx.mjpg); 0 = off")
ap.add_argument("--att-up-dbs", type=float, default=10.0,      # bench, AGC guard on: 10 dB/s 0 s sync loss in 8 ramps
                help="TX attenuation increase rate in dB/s (0 = step at once); decreases always step at once")
args = ap.parse_args()


# ------------------------------------------------------------------ input source (GUI, U.SRC): camera, demo video or
# a preset still image ("preset:<file stem>"); "preset" steps camera -> video -> preset 1 .. n -> video -> ...
PRESETS = {}                                  # file stem -> 256 x 256 RGB
if os.path.isdir(args.presets):
    for fn in sorted(os.listdir(args.presets)):
        im = cv2.imread(os.path.join(args.presets, fn)) if fn.lower().endswith(".png") else None
        if im is not None and im.shape[:2] == (256, 256):
            PRESETS[os.path.splitext(fn)[0]] = np.ascontiguousarray(im[:, :, ::-1])   # BGR -> RGB
CYCLE = ["video"] + [f"preset:{k}" for k in PRESETS]


CAM_OK = {"now": True}                        # cleared while no USB camera is connected (the camera is optional)


def src_ok(v):
    return (v == "camera" and CAM_OK["now"]) or (v == "video" and os.path.exists(args.video)) or \
        (v.startswith("preset:") and v[len("preset:"):] in PRESETS)


def src_next(cur):
    cyc = [v for v in CYCLE if src_ok(v)]
    return cyc[(cyc.index(cur) + 1) % len(cyc)] if cur in cyc else (cyc[0] if cyc else "camera")


SRC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tx_source.txt")   # last choice (kept over reboots)
src = {"now": "video" if src_ok("video") else "camera"}                  # without a saved choice: the demo video
try:
    v = open(SRC_FILE).read().strip()
    if src_ok(v):
        src["now"] = v
except OSError:
    pass


# ------------------------------------------------------------------ camera thread: always keeps the newest frame
class Camera(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Condition()
        self.frame, self.seq, self.n = None, 0, 0
        self.cap = None
        for _ in range(3):                   # at boot the USB camera can show up late (the service waits up to 20 s)
            if self.open():
                break
            time.sleep(1.0)
        else:
            print("camera: no camera at /dev/video%d, demo video / presets only (retried every 5 s)" % args.device,
                  flush=True)
        CAM_OK["now"] = self.cap is not None

    def open(self):
        if not os.path.exists(f"/dev/video{args.device}"):
            return False
        cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480); cap.set(cv2.CAP_PROP_FPS, 30)
        if args.auto:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
        else:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1); cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)
            cap.set(cv2.CAP_PROP_GAIN, args.gain)
        try:                                 # straight V4L2 controls (device units; OpenCV may rescale these)
            import v4l2ctl
            fd = os.open(f"/dev/video{args.device}", os.O_RDWR)
            ctl = v4l2ctl.controls(fd)
            for k in ("saturation", "contrast", "gamma"):
                v4l2ctl.fcntl.ioctl(fd, v4l2ctl.VIDIOC_S_CTRL, v4l2ctl.Control(ctl[k]["id"], getattr(args, k)))
            print("camera: " + ", ".join(f"{k} {v['value']}" for k, v in v4l2ctl.controls(fd).items()
                                         if k in ("saturation", "contrast", "gamma", "gain", "exposure_time_absolute")),
                  flush=True)
            os.close(fd)
        except (OSError, KeyError, ImportError) as e:
            print("camera colour controls not set:", e, flush=True)
        self.cap = cap
        return True

    def run(self):
        while True:
            if self.cap is None:                     # no camera: look for one now and then (plugged in later)
                time.sleep(5.0)
                if self.open():
                    CAM_OK["now"] = True
                    print("camera: connected", flush=True)
                continue
            if src["now"] != "camera":               # demo video selected: keep the driver queue drained, no decode
                if not self.cap.grab():
                    self.lost(); time.sleep(0.01)
                continue
            ok, f = self.cap.read()
            if not ok:
                self.lost(); time.sleep(0.01); continue
            h, w = f.shape[:2]; s = min(h, w)
            y0, x0 = (h - s) // 2, (w - s) // 2
            img = cv2.resize(f[y0:y0 + s, x0:x0 + s], (256, 256), interpolation=cv2.INTER_AREA)
            img = np.ascontiguousarray(img[:, :, ::-1])              # BGR -> RGB
            with self.lock:
                self.frame = img; self.seq += 1; self.n += 1
                self.lock.notify_all()

    def lost(self):
        """a failed read: if the device node is gone, the camera was unplugged"""
        if os.path.exists(f"/dev/video{args.device}"):
            return
        self.cap.release(); self.cap = None; CAM_OK["now"] = False
        print("camera: disconnected", flush=True)
        if src["now"] == "camera" and src_ok("video"):
            src["now"] = "video"

    def wait_new(self, last_seq, timeout=1.0):
        with self.lock:
            self.lock.wait_for(lambda: self.seq != last_seq, timeout)
            return self.seq, self.frame


class Video(threading.Thread):
    """demo video at its own frame rate (30 fps), looped; 256 x 256 frames are used as they are, others are
    centre-cropped and resized like the camera"""
    def __init__(self, path):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(path)
        self.dt = 1.0 / (self.cap.get(cv2.CAP_PROP_FPS) or 30.0)
        self.lock = threading.Condition()
        self.frame, self.seq, self.n = None, 0, 0

    def run(self):
        t_next = time.time()
        while True:
            if src["now"] != "video":
                time.sleep(0.05); t_next = time.time(); continue
            ok, f = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0); continue
            h, w = f.shape[:2]
            if (h, w) != (256, 256):
                s = min(h, w); y0, x0 = (h - s) // 2, (w - s) // 2
                f = cv2.resize(f[y0:y0 + s, x0:x0 + s], (256, 256), interpolation=cv2.INTER_AREA)
            img = np.ascontiguousarray(f[:, :, ::-1])
            t_next += self.dt
            time.sleep(max(0.0, t_next - time.time()))
            if time.time() - t_next > 0.5:               # fell far behind (e.g. paused): no catch-up burst
                t_next = time.time()
            with self.lock:
                self.frame = img; self.seq += 1; self.n += 1
                self.lock.notify_all()

    def wait_new(self, last_seq, timeout=1.0):
        with self.lock:
            self.lock.wait_for(lambda: self.seq != last_seq, timeout)
            return self.seq, self.frame


class Still(threading.Thread):
    """the selected preset image, repeated at 30 fps (every frame is encoded and sent, like a still video)"""
    def __init__(self):
        super().__init__(daemon=True)
        self.dt = 1.0 / 30
        self.lock = threading.Condition()
        self.frame, self.seq, self.n = None, 0, 0

    def run(self):
        t_next = time.time()
        while True:
            img = PRESETS.get(src["now"][len("preset:"):]) if src["now"].startswith("preset:") else None
            if img is None:
                time.sleep(0.05); t_next = time.time(); continue
            t_next += self.dt
            time.sleep(max(0.0, t_next - time.time()))
            if time.time() - t_next > 0.5:
                t_next = time.time()
            with self.lock:
                self.frame = img; self.seq += 1; self.n += 1
                self.lock.notify_all()

    def wait_new(self, last_seq, timeout=1.0):
        with self.lock:
            self.lock.wait_for(lambda: self.seq != last_seq, timeout)
            return self.seq, self.frame


# ------------------------------------------------------------------ preview subscriber (PC GUI sends HELLO)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", U.PORT_TX)); sock.settimeout(0.5)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
try:                                          # root: past net.core.wmem_max (~200 kB), a whole image fits
    sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_SNDBUFFORCE", 32), 4 << 20)
except OSError:
    pass
peers = {}                                    # subscriber address -> time of its last HELLO (several GUIs at once)
decim = {}                                    # subscriber address -> preview decimation (HELLO <n>: every n-th frame)


tx = None                                     # JsccTx once the overlay is up (below)
att = {"db": None, "target": None,            # TX1 attenuation now / requested (None: no SPI access in this bitstream)
       "rate": args.att_up_dbs}               # increase rate of the current move, dB/s (0: at once)
att_lock = threading.Lock()
RAMP_S = 0.1                                  # attenuation increases at att["rate"] (RX AGC up-tracking), decreases at once


mode = {"want": "jscc", "now": "jscc", "ok": False}   # DeepJSCC or SSCC baseline (ok: bitstream supports SSCC)


def att_report():
    db = att["db"] if att["db"] is not None else -1.0
    tg = att["target"] if att["target"] is not None else -1.0
    return U.ATTS + b" %.2f %.2f %s %s" % (db, tg, mode["now"].encode(), src["now"].encode())


def att_loop():
    while True:
        time.sleep(RAMP_S)
        if att["db"] is None or att["target"] == att["db"]:
            continue
        step = round(att["rate"] * RAMP_S * 4) / 4 if att["rate"] > 0 else 90.0     # 0.25 dB attenuator steps
        nxt = min(att["target"], att["db"] + max(step, 0.25)) if att["target"] > att["db"] else att["target"]
        try:
            with att_lock:
                att["db"] = tx.set_tx_atten(nxt)
            if att["db"] == att["target"]:
                print(f"TX attenuation {att['db']:.2f} dB", flush=True)
        except (RuntimeError, TimeoutError) as e:
            print("attenuation:", e, flush=True)


def ctrl_loop():
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.startswith(U.TIME):                  # clock offset query (latency measurement)
                sock.sendto(U.TIME_R + b" %.6f" % time.time(), addr)
                continue
            if data.startswith(U.HELLO):
                peers[addr] = time.time(); decim[addr] = U.hello_decim(data)
            elif data.startswith(U.SRC):
                v = data[len(U.SRC):].strip().decode()
                if v == "preset":
                    v = src_next(src["now"])
                if src_ok(v):
                    src["now"] = v
                    try:
                        open(SRC_FILE, "w").write(v + "\n")
                    except OSError:
                        pass
                    print(f"input {v} (from {addr[0]})", flush=True)
            elif data.startswith(U.STAT):                # state query (also broadcast): answer, no subscription
                pass
            elif data.startswith(U.MODE):
                m = data[len(U.MODE):].strip().decode()
                if m in ("jscc", "sscc") and (m == "jscc" or mode["ok"]):
                    mode["want"] = m
                    print(f"mode {m} requested (from {addr[0]})", flush=True)
            elif data.startswith(U.ATTN) and att["db"] is not None:
                f = data[len(U.ATTN):].split()           # "<dB> [<increase rate dB/s>]" (rate only for this move)
                att["rate"] = float(f[1]) if len(f) > 1 else args.att_up_dbs
                att["target"] = round(min(max(float(f[0]), 0.0), 89.75) * 4) / 4
                print(f"TX attenuation target {att['target']:.2f} dB, up {att['rate']:g} dB/s (from {addr[0]})", flush=True)
            else:
                continue
            sock.sendto(att_report(), addr)
        except socket.timeout:
            pass
        except (ValueError, RuntimeError, TimeoutError, OSError) as e:
            print("ctrl:", e, flush=True)


threading.Thread(target=ctrl_loop, daemon=True).start()
threading.Thread(target=att_loop, daemon=True).start()


# ------------------------------------------------------------------ MJPEG preview for phones on the same WiFi
class Mjpeg:
    """newest frame handed to the encoder; JPEG-encoded per HTTP client, only while one is connected"""
    def __init__(self):
        self.cv = threading.Condition(); self.img = None; self.seq = 0; self.clients = 0

    def offer(self, img):
        if self.clients:
            with self.cv:
                self.img = img; self.seq += 1; self.cv.notify_all()


mjpeg = Mjpeg()
if args.http_port:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class MjpegHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/tx.mjpg":
                self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache"); self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.connection.settimeout(U.STREAM_STALL)
            mjpeg.clients += 1; last = -1; t_sent = time.time()
            try:
                while True:
                    with mjpeg.cv:
                        mjpeg.cv.wait_for(lambda: mjpeg.seq != last, 2.0)
                        img, seq = mjpeg.img, mjpeg.seq
                    if img is None or seq == last:
                        continue
                    last = seq; now = time.time()
                    if now - t_sent < 0.8 / U.STREAM_FPS:
                        continue
                    if U.unsent(self.connection) > U.STREAM_BACKLOG:   # WiFi behind: skip frames; phone gone: close
                        if now - t_sent > U.STREAM_STALL:
                            break
                        continue
                    t_sent = now
                    ok, b = cv2.imencode(".jpg", img[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, U.STREAM_Q])
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(b)
                                     + b.tobytes() + b"\r\n")
            except OSError:
                pass
            finally:
                mjpeg.clients -= 1

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", args.http_port), MjpegHandler); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def live_peers():
    now = time.time()
    return [a for a, t in list(peers.items()) if now - t <= 5.0]


preview_q = []                                # newest (fid, img) only; sent by its own thread so that the
preview_ev = threading.Event()                # encoder loop never waits for the network (USB to the RX board is slower)


def preview_loop():
    while True:
        preview_ev.wait(); preview_ev.clear()
        if preview_q:
            fid, img = preview_q.pop()
            _send_preview(fid, img)


threading.Thread(target=preview_loop, daemon=True).start()


def send_preview(fid, img, t_src=None):
    if not args.no_preview and live_peers():
        if t_src is not None:                         # small datagram, sent at once (the preview follows in its thread)
            for a in live_peers():
                try:
                    sock.sendto(U.TS + b" %d %.6f" % (fid & 0xFFFFFFFF, t_src), a)
                except OSError:
                    pass
        preview_q[:] = [(fid, img)]; preview_ev.set()


def _send_preview(fid, img):
    data = img.tobytes()
    for a in live_peers():
        if fid % decim.get(a, 1):
            continue
        for pkt in U.chunks(U.MT_TX_IMG, fid, data):
            try:
                sock.sendto(pkt, a)
            except OSError:
                break


# ------------------------------------------------------------------ TX overlay: load until the DAC side takes data
for attempt in range(5):
    tx = JsccTx(); time.sleep(3)
    lo_mhz = ad9361_lo.configured_mhz(os.path.dirname(os.path.abspath(__file__)))
    if lo_mhz != ad9361_lo.DEFAULT_MHZ and tx.has_spi():     # carrier other than the 915 MHz of the PL init
        tx.set_carrier(lo_mhz, log=lambda m: print(f"{time.strftime('%H:%M:%S')} {m}", flush=True))
    tx.set_source("prbs"); time.sleep(0.5)
    a0 = tx.rd("TX_FRAMES"); time.sleep(1); r = tx.rd("TX_FRAMES") - a0
    print(f"{time.strftime('%H:%M:%S')} load {attempt}: PRBS {r} frames/s", flush=True)
    if r > 300:
        break
tx.set_source("encoder"); time.sleep(0.2); tx.clear_counters()
if tx.has_spi():                              # demo control: TX attenuation from the GUI (U.ATTN)
    try:
        with att_lock:
            att["db"] = att["target"] = tx.set_tx_atten(round(min(max(args.att, 0.0), 89.75) * 4) / 4)
        print(f"TX attenuation {att['db']:.2f} dB, remote control enabled", flush=True)
    except (RuntimeError, TimeoutError) as e:
        print("TX attenuation control off:", e, flush=True)

mode["ok"] = tx.has_sscc()
print(f"SSCC baseline {'available' if mode['ok'] else 'not in this bitstream'}", flush=True)
jfit = sscc_pkt.JpegFit()

cam = Camera(); cam.start()
if not src_ok(src["now"]) and src_ok("video"):     # saved choice "camera" but no camera connected
    src["now"] = "video"
vid = Video(args.video) if os.path.exists(args.video) else None
if vid is not None:
    vid.start()
still = Still(); still.start()
print(f"input {src['now']}" + ("" if vid else f" (no demo video at {args.video})") + f", presets {list(PRESETS)}",
      flush=True)
seq, sent, t_rep = 0, 0, time.time()
cur_src = None
print("streaming", flush=True)
while True:
    s_obj = vid if (src["now"] == "video" and vid is not None) else still if src["now"].startswith("preset:") else cam
    if s_obj is not cur_src:                       # switched: the sequence numbers of the other source do not apply
        cur_src, seq = s_obj, -1
    seq, img = s_obj.wait_new(seq)
    if img is None:
        continue
    t_src = time.time()                               # frame taken from its source: start of the end-to-end latency
    if mode["want"] != mode["now"]:
        if mode["want"] == "sscc":
            time.sleep(0.06)                        # the encoder finishes its last frame (~33 ms) before the switch
        tx.set_mode(mode["want"]); mode["now"] = mode["want"]
        print(f"mode {mode['now']}", flush=True)
    if mode["now"] == "sscc":
        jpg, q = jfit.encode(img)
        tx.send_packet(sscc_pkt.pack(jpg, seq, q), timeout=2.0)
    else:
        tx.send_image(img, timeout=2.0)             # returns when the encoder has taken the frame
    sent += 1
    send_preview(seq, img, t_src)
    mjpeg.offer(img)
    if time.time() - t_rep > 5.0:
        st = tx.status()
        print(f"{time.strftime('%H:%M:%S')} {src['now']} {s_obj.n / (time.time() - t_rep):.1f} fps, sent {sent / (time.time() - t_rep):.1f} fps, "
              f"tx frames {st['TX_FRAMES']}, tlast err {st['TLAST_ERR']}, mode {mode['now']}"
              + (f" (JPEG q {jfit.q})" if mode["now"] == "sscc" else "") + f", preview -> {live_peers()}", flush=True)
        cam.n, still.n, sent, t_rep = 0, 0, 0, time.time()
        if vid is not None:
            vid.n = 0
