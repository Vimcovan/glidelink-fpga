"""DeepJSCC-Q over OFDM: live GUI on the PC (USB-Ethernet / UDP from both PYNQ-ZU boards).

  TX board 192.168.4.1:5006  -> camera frames handed to the encoder (256x256 RGB)
  RX board 192.168.3.1:5005  -> decoded images + telemetry (final constellation, LTF |H|, status counters)
The GUI subscribes by sending HELLO to both boards once a second (see jscc_udp.py).

  page "Image link" : TX frame | RX reconstruction, PSNR (RX vs best-matching recent TX frame), end-to-end latency (TX source -> RX), link statistics
  page "PHY detail" : constellations of all equalizer stages with EVM, CSI (|H| 3-D, |H| + phase residual now),
                      per-subcarrier SNR, EVM history, AGC / sync
usage: python jscc_gui.py [--tx 192.168.4.1] [--rx 192.168.3.1] [--shot out.png secs] [--page 1]
       on the RX board (monitor on DisplayPort, no PC, no TX preview):  python3 jscc_gui.py --rx 127.0.0.1 --lite
"""
import sys, os, time, socket, argparse, collections
import numpy as np
try:                                     # PC: PyQt6;  PYNQ board (Ubuntu 22.04 packages): PyQt5
    from PyQt6 import QtCore, QtWidgets, QtGui
except ImportError:
    from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph as pg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jscc_udp as U
import sscc_pkt
try:
    import cv2                               # SSCC JPEG decoding (falls back to QImage)
except ImportError:
    cv2 = None

ap = argparse.ArgumentParser()
ap.add_argument("--tx", default="192.168.4.1")
ap.add_argument("--rx", default="192.168.3.1")
ap.add_argument("--shot", nargs=2, metavar=("PNG", "SECONDS"))
ap.add_argument("--page", default=None, help="page shown at start (0 image link, 1 PHY detail, 2 style, 3 about, 4 phone (lite))")
ap.add_argument("--lite", action="store_true", help="board display: no TX preview / PSNR / latency, 2-D |H| waterfall")
ap.add_argument("--compact", action="store_true", help="lite: small-screen layout (default when the screen is < 800 px high)")
ap.add_argument("--no-compact", action="store_true")
ap.add_argument("--no-gpu3d", action="store_true", help="lite: 2-D waterfall even if the Mali-400 (lima) is available")
ap.add_argument("--theme", default=None, help="colour theme (default: the last one picked on the style page, else 'tech')")
ap.add_argument("--remote-port", type=int, default=0, help="UDP remote control for the demo-video director (e.g. 5010); default off")
ap.add_argument("--bench", type=float, default=0, metavar="SECONDS", help="run, print load statistics, quit")
ap.add_argument("--web-port", type=int, default=8080, help="lite: port of the phone control page (web_server.py), "
                "shown with a QR code on the phone page")
ap.add_argument("--touch-dev", default="auto", help="lite: touch screen evdev node for press-and-hold / drag "
                "('auto': the multi-touch CTP panel, 'off')")
args, _ = ap.parse_known_args()

# ---- subcarrier layout (fftshift order, index = subcarrier + 32) ----
NULLS = set(range(0, 6)) | {32} | set(range(59, 64))
PILOTS = [11, 25, 39, 53]
DATA = [k for k in range(64) if k not in NULLS and k not in PILOTS]
LTF_ONE = [6, 7, 10, 11, 13, 15, 16, 17, 18, 19, 20, 23, 24, 26, 28, 29, 30, 31, 33, 36, 37, 39, 41, 47, 48, 51, 53, 55, 56, 57, 58]
LTF_MINUS = [8, 9, 12, 14, 21, 22, 25, 27, 34, 35, 38, 40, 42, 43, 44, 45, 46, 49, 50, 52, 54]
LTF_SIGN = np.zeros(64); LTF_SIGN[LTF_ONE] = 1; LTF_SIGN[LTF_MINUS] = -1
USED = np.array(sorted(LTF_ONE + LTF_MINUS))
QAM_LV = np.array([-1106, -790, -474, -158, 158, 474, 790, 1106])
SSCC_FAIL_IMG = np.full((256, 256, 3), 128, np.uint8)          # SSCC frame the JPEG decoder rejects: mid-gray
NSYM, PAD = 683, 16

# ---- colour themes: C_CLAY = primary accent, C_BLUE = secondary accent (names kept from the first, light theme).
# DATA / PILOT: constellation points, GRID: grid and 3-D axes, MESH: 3-D surface mesh lines, CMAP: |H| colour map.
_SANS, _MONO = ("Inter", "Segoe UI", "Roboto", "DejaVu Sans"), ("JetBrains Mono", "Consolas", "DejaVu Sans Mono")
_DARK = dict(DARK=True, FONTS=_SANS, MONO=_MONO)
THEMES = {
    "tech": dict(_DARK, LABEL="科技青", SUB="Cyber Cyan", NAMES=("cyan", "amber"),
                 BG="#070B16", PLOT="#0B1222", CARD="#0D1628", ACC="#22D3EE", ACC_DK="#0E7490", INK="#E2E8F0",
                 MUTED="#7C8BA5", LINE="#1C2A44", SEC="#F59E0B", HDR_BG="#0C1C30", HDR_FG="#22D3EE", TAB="#0D1628",
                 TAB_SEL="#0C2A3A", TAB_SEL_FG="#22D3EE", CARD_BORDER="#164E63",
                 DATA=(34, 211, 238), PILOT=(245, 158, 11), GRID=(148, 163, 184), MESH=(8, 23, 41, 217),
                 CMAP=[(23, 37, 84), (6, 182, 212), (224, 250, 255)]),
    "amd": dict(_DARK, LABEL="AMD 红黑", SUB="AMD Red", NAMES=("red", "white"),
                BG="#050505", PLOT="#0C0C0E", CARD="#141416", ACC="#ED1C24", ACC_DK="#9B1016", INK="#F2F2F2",
                MUTED="#8C8C92", LINE="#26262A", SEC="#E5E5E5", HDR_BG="#1A0B0C", HDR_FG="#FF4A50", TAB="#141416",
                TAB_SEL="#2A0D0F", TAB_SEL_FG="#FF4A50", CARD_BORDER="#5A1418",
                DATA=(255, 64, 70), PILOT=(240, 240, 240), GRID=(160, 160, 168), MESH=(20, 4, 6, 217),
                CMAP=[(45, 8, 10), (237, 28, 36), (255, 225, 215)]),
    "matrix": dict(_DARK, LABEL="矩阵绿", SUB="Matrix Green", NAMES=("green", "yellow"),
                   BG="#020A06", PLOT="#04120B", CARD="#071A10", ACC="#34D399", ACC_DK="#047857", INK="#D1FAE5",
                   MUTED="#6B9A85", LINE="#12301F", SEC="#FACC15", HDR_BG="#062216", HDR_FG="#34D399", TAB="#071A10",
                   TAB_SEL="#0A2E1D", TAB_SEL_FG="#34D399", CARD_BORDER="#065F46",
                   DATA=(52, 211, 153), PILOT=(250, 204, 21), GRID=(134, 180, 158), MESH=(2, 26, 14, 217),
                   CMAP=[(6, 40, 28), (16, 185, 129), (220, 255, 235)]),
    "neon": dict(_DARK, LABEL="霓虹紫", SUB="Neon Violet", NAMES=("violet", "pink"),
                 BG="#0A0616", PLOT="#110A22", CARD="#160D2C", ACC="#C084FC", ACC_DK="#7E22CE", INK="#F3E8FF",
                 MUTED="#9183B0", LINE="#2A1D45", SEC="#F472B6", HDR_BG="#1E1038", HDR_FG="#D8B4FE", TAB="#160D2C",
                 TAB_SEL="#2B1650", TAB_SEL_FG="#E9D5FF", CARD_BORDER="#6B21A8",
                 DATA=(192, 132, 252), PILOT=(244, 114, 182), GRID=(167, 150, 200), MESH=(20, 8, 40, 217),
                 CMAP=[(30, 12, 64), (168, 85, 247), (255, 214, 245)]),
    "daylight": dict(DARK=False, FONTS=_SANS, MONO=_MONO, LABEL="日光蓝", SUB="Daylight Blue", NAMES=("blue", "orange"),
                     BG="#EEF2F7", PLOT="#F8FAFC", CARD="#FFFFFF", ACC="#2563EB", ACC_DK="#1D4ED8", INK="#0F172A",
                     MUTED="#64748B", LINE="#D5DDE8", SEC="#F97316", HDR_BG="#2563EB", HDR_FG="#FFFFFF", TAB="#E2E8F0",
                     TAB_SEL="#2563EB", TAB_SEL_FG="#FFFFFF", CARD_BORDER="#CBD5E1",
                     DATA=(37, 99, 235), PILOT=(249, 115, 22), GRID=(51, 65, 85), MESH=(30, 58, 138, 255),
                     CMAP=[(219, 234, 254), (59, 130, 246), (30, 41, 110)]),
    "light": dict(DARK=False, LABEL="暖陶土", SUB="Warm Clay", NAMES=("clay", "blue"),
                  BG="#F4F1EA", PLOT="#FAF9F5", CARD="#FFFFFF", ACC="#D97757", ACC_DK="#B85C3E", INK="#3D3929",
                  MUTED="#8A857A", LINE="#E6E1D8", SEC="#6A9BCC", HDR_BG="#D97757", HDR_FG="#FFFFFF", TAB="#E6E1D8",
                  TAB_SEL="#D97757", TAB_SEL_FG="#FFFFFF", CARD_BORDER="#E6E1D8",
                  DATA=(217, 119, 87), PILOT=(106, 155, 204), GRID=(61, 57, 41), MESH=(115, 51, 28, 255),
                  CMAP=[(232, 230, 220), (217, 119, 87), (120, 52, 30)],
                  FONTS=("Anthropic Serif", "Georgia", "DejaVu Serif"), MONO=("Anthropic Serif", "Georgia", "DejaVu Serif")),
}
THEME_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_theme.txt")   # last choice on the style page


def saved_theme():
    try:
        t = open(THEME_FILE).read().strip()
    except OSError:
        return "tech"
    return t if t in THEMES else "tech"


args.theme = args.theme or saved_theme()
TH = THEMES[args.theme]
C_BG, C_PLOT, C_CARD = TH["BG"], TH["PLOT"], TH["CARD"]
C_CLAY, C_CLAY_DK, C_INK, C_MUTED, C_LINE = TH["ACC"], TH["ACC_DK"], TH["INK"], TH["MUTED"], TH["LINE"]
C_BLUE = TH["SEC"]
C_BAD = "#ef4444"                            # error text (SSCC CRC / decode failure)


def _family(cands, generic):
    try:
        fams = QtGui.QFontDatabase.families()
    except TypeError:
        fams = QtGui.QFontDatabase().families()
    return next((f for f in cands if f in fams), generic)


def serif_family():
    """UI font of the theme (serif for 'light', sans for the others)."""
    return _family(TH["FONTS"], "serif" if args.theme == "light" else "sans-serif")


def mono_family():
    """numbers / readouts"""
    return _family(TH["MONO"], "monospace")


def evm_db(z):
    ref = QAM_LV[np.argmin(abs(z.real[:, None] - QAM_LV), 1)] + 1j * QAM_LV[np.argmin(abs(z.imag[:, None] - QAM_LV), 1)]
    return 10 * np.log10(np.mean(abs(z - ref) ** 2) / np.mean(abs(ref) ** 2))


def net_proc(conn, peers, want_rx, att_req, att_seq, mode_req, mode_seq, src_req, src_seq):
    """Separate process: HELLO subscription + UDP reception + reassembly (~4500 datagrams/s at 30 img/s).
    In a thread of the GUI process this costs the GIL and stalls painting; here only complete messages
    (~40/s) cross the pipe."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 << 20)
    try:                                     # as root on the board: past net.core.rmem_max (~200 kB would drop packets)
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_RCVBUFFORCE", 33), 16 << 20)
    except OSError:
        pass
    sock.bind(("0.0.0.0", 0)); sock.settimeout(0.2)
    ra = U.Reassembler(); t_hello = 0.0; seq_done = 0; mseq_done = 0; sseq_done = 0
    t_clk, q_clk, have_clk = 0.0, None, False         # clock offset to the TX board (end-to-end latency)
    while True:
        if time.time() - t_clk > (30.0 if have_clk else 3.0):
            t_clk = q_clk = time.time()
            try:
                sock.sendto(U.TIME, peers[1])
            except OSError:
                pass
        if src_seq.value != sseq_done:               # camera / demo video / preset request from the GUI
            sseq_done = src_seq.value
            try:
                sock.sendto(U.SRC + b" " + src_req.value, peers[1])
            except OSError:
                pass
        if mode_seq.value != mseq_done:              # JSCC / SSCC request from the GUI
            mseq_done = mode_seq.value
            try:
                sock.sendto(U.MODE + (b" sscc" if mode_req.value else b" jscc"), peers[1])
            except OSError:
                pass
        if att_seq.value != seq_done:                # TX attenuation request from the GUI (newest value only)
            seq_done = att_seq.value
            try:
                sock.sendto(U.ATTN + b" %.2f" % att_req.value, peers[1])
            except OSError:
                pass
        if time.time() - t_hello > 1.0:
            t_hello = time.time()
            for p in peers:
                try:
                    sock.sendto(U.HELLO, p)
                except OSError:
                    pass
            conn.send(("drop", ra.dropped))
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            time.sleep(0.1); continue
        if data.startswith(U.TS):                    # TX time when the frame was taken from its source
            f = data.split()
            conn.send(("ts", int(f[1]), float(f[2]))); continue
        if data.startswith(U.TIME_R):                # clock offset: TX clock - this board's clock
            if q_clk is not None:
                t2 = time.time()
                conn.send(("clk", float(data.split()[1]) - (q_clk + t2) / 2, t2 - q_clk)); q_clk = None; have_clk = True
            continue
        if data.startswith(U.ATTS):
            try:
                f = data[len(U.ATTS):].split()           # current dB (ramping), target dB, mode
                conn.send(("atten", float(f[0]), time.time(), f[2].decode() if len(f) > 2 else "jscc",
                           f[3].decode() if len(f) > 3 else "camera"))
            except ValueError:
                pass
            continue
        m = ra.add(data)
        if m is not None and (m[0] not in (U.MT_RX_IMG, U.MT_TX_IMG, U.MT_SSCC) or want_rx.value):
            conn.send((m[0], m[1], m[2], time.time()))


class _Drops:
    dropped = 0


class Receiver(QtCore.QThread):
    tx_img = QtCore.pyqtSignal(int, object, float)
    rx_img = QtCore.pyqtSignal(int, object, float)
    tel = QtCore.pyqtSignal(dict, dict, float)
    atten = QtCore.pyqtSignal(float, float, str, str)  # TX attenuation (dB, -1 = no control), time, mode, source
    sscc = QtCore.pyqtSignal(int, object, float)  # SSCC packet decoded by the RX PL

    def __init__(self):
        super().__init__()
        import multiprocessing as mp
        self.running = True
        self.ra = _Drops()
        self.n_rx = self.n_tel = 0           # GUI skips everything but the newest RX image / telemetry (no backlog)
        # the board display also subscribes to the TX board: reachable only when the boards are cabled (demo step 2)
        peers = [(args.rx, U.PORT_RX), (args.tx, U.PORT_TX)]
        self.want_rx = mp.Value("b", 1, lock=False)      # lite: images are not even passed on while the PHY page is shown
        self.att_req, self.att_seq = mp.Value("d", 0.0, lock=False), mp.Value("i", 0, lock=False)
        self.mode_req, self.mode_seq = mp.Value("b", 0, lock=False), mp.Value("i", 0, lock=False)
        self.src_req, self.src_seq = mp.Array("c", 64, lock=False), mp.Value("i", 0, lock=False)
        self.n_sscc = 0
        self.tsmap = collections.OrderedDict()         # TX frame id -> TX source time (TX clock)
        self.clk_off = None                            # TX clock - this board's clock, s
        self.conn, child = mp.Pipe(duplex=False)
        self.proc = mp.Process(target=net_proc, args=(child, peers, self.want_rx, self.att_req, self.att_seq,
                                                         self.mode_req, self.mode_seq, self.src_req, self.src_seq),
                                daemon=True)
        self.proc.start()

    def set_atten(self, db):
        self.att_req.value = db; self.att_seq.value += 1

    def set_mode(self, mode):
        self.mode_req.value = int(mode == "sscc"); self.mode_seq.value += 1

    def set_source(self, source):
        self.src_req.value = source.encode()[:63]; self.src_seq.value += 1   # camera / video / preset[:<name>]

    def run(self):
        while self.running:
            if not self.conn.poll(0.2):
                continue
            m = self.conn.recv()
            if m[0] == "drop":
                self.ra.dropped = m[1]; continue
            if m[0] == "atten":
                self.atten.emit(m[1], m[2], m[3], m[4]); continue
            if m[0] == "ts":
                self.tsmap[m[1]] = m[2]
                while len(self.tsmap) > 300:
                    self.tsmap.popitem(last=False)
                continue
            if m[0] == "clk":
                if m[2] < 0.005:                       # keep only answers with a short round trip
                    self.clk_off = m[1]
                continue
            mtype, mid, payload, now = m
            if mtype in (U.MT_TX_IMG, U.MT_RX_IMG) and len(payload) == 256 * 256 * 3:
                img = np.frombuffer(payload, np.uint8).reshape(256, 256, 3)
                if mtype == U.MT_TX_IMG:
                    self.tx_img.emit(mid, img, now)
                else:
                    self.n_rx += 1; self.rx_img.emit(self.n_rx, img, now)
            elif mtype == U.MT_SSCC and len(payload) == sscc_pkt.PAY_BYTES:
                self.n_sscc += 1; self.sscc.emit(self.n_sscc, payload, now)
            elif mtype == U.MT_TEL:
                meta, arrays = U.unpack_tel(payload)
                self.n_tel += 1; meta["_n"] = self.n_tel
                self.tel.emit(meta, arrays, now)


COMPACT = False                              # set in Gui(): small touch screen (1024x600)


MONO = "monospace"                          # set in Gui() once Qt is up (mono_family)


NAN_RE = __import__("re").compile(r"-?nan( dB| ms)?")      # no data (e.g. sync lost): show a dash, not "nan"


def tiles_html(tiles, column=False):
    """KPI tiles (big value, small label) for the small screen: one row, or one column (column=True)."""
    html = f"<table width='100%' cellspacing='0' cellpadding='{6 if column else 4}'>" + ("" if column else "<tr>")
    for label, value in tiles:
        value = NAN_RE.sub("–", value)
        html += (("<tr>" if column else "") +
                 f"<td align='center'><span style='font-size:{16 if column else 17}pt; color:{C_CLAY if TH['DARK'] else C_INK}; "
                 f"font-family:\"{MONO}\"'><b>{value}</b></span><br>"
                 f"<span style='font-size:9pt; color:{C_MUTED}; letter-spacing:1px'>{label.upper() if TH['DARK'] else label}</span></td>"
                 + ("</tr>" if column else ""))
    return html + ("" if column else "</tr>") + "</table>"


def card_html(rows, center=False):
    html = "<table cellspacing='0' cellpadding='2'" + (" align='center'>" if center else ">")
    rows = [r if r is None else (r[0], NAN_RE.sub("–", r[1])) for r in rows]
    for r in rows:
        html += ("<tr><td colspan='2' style='font-size:6pt'>&nbsp;</td></tr>" if r is None else
                 f"<tr><td style='padding-right:28px; color:{C_MUTED}'>{r[0]}</td><td align='right' style='color:{C_INK}; "
                 f"font-family:\"{MONO}\"'><b>{r[1]}</b></td></tr>")
    return html + "</table>"


def const_plot(title, lim=1400):
    p = pg.PlotWidget()
    p.setAspectLocked(True); p.showGrid(x=True, y=True, alpha=0.25)
    p.disableAutoRange(); p.setXRange(-lim, lim, padding=0); p.setYRange(-lim, lim, padding=0)
    p.setTitle(title, color=C_INK, size="12pt")
    if args.lite:                            # board: one density image (grid baked in) instead of ~1000 symbols + axes
        p.hideAxis("left"); p.hideAxis("bottom"); p.showGrid(x=False, y=False)
        d = ConstImage(); p.addItem(d)
        return p, d, None
    d = p.plot([], [], pen=None, symbol="o", symbolSize=3, symbolBrush=pg.mkBrush(*TH["DATA"], 170), symbolPen=None)
    pl = p.plot([], [], pen=None, symbol="x", symbolSize=6, symbolBrush=None, symbolPen=pg.mkPen(C_BLUE, width=2))
    return p, d, pl


class ImgView(QtWidgets.QWidget):
    """Board display: 256x256 image drawn as RGB32 with integer nearest-neighbour zoom in paintEvent
    (A53 measurements: ~7.5 ms/frame at 3x, vs ~15 ms for QImage.scaled and ~15 ms for pyqtgraph ImageItem)."""
    def __init__(self):
        super().__init__()
        self.buf = np.zeros((256, 256, 4), np.uint8); self.buf[..., 3] = 255
        self.qi = QtGui.QImage(self.buf.data, 256, 256, 1024, QtGui.QImage.Format.Format_RGB32)   # shares self.buf
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)

    def set_rgb(self, img):
        self.buf[..., 0] = img[..., 2]; self.buf[..., 1] = img[..., 1]; self.buf[..., 2] = img[..., 0]   # BGRA in memory
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(C_BG))
        n = min(self.width(), self.height()) / 256
        if n >= 2 or (args.lite and not COMPACT and n >= 1):
            n = int(n)                                                    # integer zoom: crisp
        # else fractional (nearest neighbour), e.g. 1.5x on the 1024x600 touch screen
        w = int(256 * n)
        y = 0 if COMPACT else (self.height() - w) // 2          # small screen: right under the title
        p.drawImage(QtCore.QRect((self.width() - w) // 2, y, w, w), self.qi)
        p.end()


def lima_available():
    """True if the lima kernel driver (Mali-400) has a render node (modules loaded, see gpu_lima/)."""
    import glob
    return any(os.path.basename(os.path.realpath(d + "/device/driver")) == "lima" for d in glob.glob("/sys/class/drm/renderD*"))


def h3d_colors():
    """theme colours for h3d_render.py (JSON on its command line)"""
    import json
    c = QtGui.QColor(C_PLOT)
    return json.dumps(dict(bg=[c.red(), c.green(), c.blue()], cmap=TH["CMAP"], grid=list(TH["GRID"]), mesh=list(TH["MESH"])))


class H3DWorker(QtCore.QThread):
    """Talks to h3d_render.py (GLES2 on the Mali-400, separate process): at most one request in flight, the newest
    pending |H| history replaces older ones."""
    done = QtCore.pyqtSignal(object, int, int, float, float, float)

    def __init__(self):
        super().__init__()
        import threading
        self.spawn()
        self.lock, self.ev, self.req, self.running = threading.Lock(), threading.Event(), None, True

    def spawn(self):
        import subprocess
        self.proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "h3d_render.py"),
                                      "--colors", h3d_colors()],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)

    def request(self, w, h, hdb, ts, zlo, zhi):
        with self.lock:
            self.req = (w, h, np.ascontiguousarray(hdb, np.float32), np.ascontiguousarray(ts, np.float32), zlo, zhi)
        self.ev.set()

    def run(self):
        import h3d_render as R
        while self.running:
            if not self.ev.wait(0.5):
                continue
            with self.lock:
                req, self.req = self.req, None; self.ev.clear()
            if req is None:
                continue
            w, h, hdb, ts, zlo, zhi = req
            try:
                self.proc.stdin.write(R.REQ.pack(b"H3DR", w, h, hdb.shape[0], hdb.shape[1], zlo, zhi) + hdb.tobytes() + ts.tobytes())
                self.proc.stdin.flush()
                _, w2, h2, ms = R.RSP.unpack(R.read_exact(self.proc.stdout, R.RSP.size))
                img = R.read_exact(self.proc.stdout, w2 * h2 * 4)
            except (OSError, EOFError):                  # renderer exited (e.g. its GPU-context watchdog): new one
                if not self.running:
                    return
                self.proc.kill(); self.proc.wait()
                time.sleep(0.5); self.spawn()
                continue
            self.done.emit(img, w2, h2, ms, zlo, zhi)

    def stop(self):
        self.running = False; self.ev.set(); self.wait(1000); self.proc.kill()


class H3DView(QtWidgets.QWidget):
    """Shows the GPU-rendered |H| surface 1:1 and paints the axis labels (same camera as h3d_render)."""
    def __init__(self):
        super().__init__()
        self.img = self.qimg = None; self.zr = (-1.0, 1.0); self.ms = 0.0
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)

    def show_image(self, img, w, h, ms, zlo, zhi):
        self.img = img                       # QImage below shares this buffer
        self.qimg = QtGui.QImage(img, w, h, w * 4, QtGui.QImage.Format.Format_RGBA8888)
        self.zr, self.ms = (zlo, zhi), ms
        self.update()

    def paintEvent(self, e):
        import h3d_render as R
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(C_PLOT))
        if self.qimg is None:
            p.end(); return
        w, h = self.qimg.width(), self.qimg.height()
        p.drawImage(0, 0, self.qimg)
        zlo, zhi = self.zr; zf = zlo * R.Z_SCALE; T, XE = R.T_SPAN, R.XE
        lab = [((sc, -T - 4, zf), str(sc), C_INK) for sc in (-26, -13, 0, 13, 26)]
        lab += [((0, -T - 10, zf), "subcarrier", C_CLAY)]
        lab += [((XE + 4, -sec * T / R.T_WIN, zf), f"-{sec} s" if sec else "0 s", C_INK) for sec in range(0, int(R.T_WIN) + 1)]
        lab += [((XE + 12, -T / 2, zf), "time", C_CLAY)]
        lab += [((XE + 2, 7, db * R.Z_SCALE), f"{db:+d}", C_INK) for db in range(int(np.ceil(zlo)), int(np.floor(zhi)) + 1)]
        lab += [((XE + 2, 7, zhi * R.Z_SCALE + 6), "|H| dB", C_CLAY)]
        xy = R.project(R.camera(w, h, zlo, zhi), [a for a, _, _ in lab], w, h)
        f = p.font(); f.setPointSize(8 if COMPACT else 11); p.setFont(f)
        for (x, y), (_, txt, col) in zip(xy, lab):
            p.setPen(QtGui.QColor(col))
            p.drawText(QtCore.QRectF(x - 60, y - 12, 120, 24), QtCore.Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class ConstImage(pg.ImageItem):
    """Constellation as a 2-D histogram image (data clay, pilots blue): cost independent of the point count."""
    N = 200

    def draw(self, d, pp, lim):
        e = np.linspace(-lim, lim, self.N + 1)
        hd = np.histogram2d(d.imag, d.real, (e, e))[0]
        hp = np.histogram2d(pp.imag, pp.real, (e, e))[0]
        rgba = np.zeros((self.N, self.N, 4), np.uint8)
        c = (np.arange(self.N) + 0.5) / self.N * 2 * lim - lim            # bin centres
        bnd = (QAM_LV[1:] + QAM_LV[:-1]) / 2                             # 64-QAM decision boundaries: points sit mid-cell
        dark = TH["DARK"]
        for g in (bnd if lim > 900 else np.arange(-lim // 200 * 200, lim, 200)):
            k = int(np.argmin(abs(c - g))); rgba[k, :] = rgba[:, k] = (*TH["GRID"], (38 if g else 70) if dark else (22 if g else 45))
        if not dark:                                 # light background: 1-bin (~1.5 px) dots vanish -> 2 x 2 bins, opaque
            grow = lambda h: np.maximum.reduce([h, np.roll(h, 1, 0), np.roll(h, 1, 1), np.roll(np.roll(h, 1, 0), 1, 1)])
            hd, hp = grow(hd), grow(hp)
        a = np.minimum(1.0, hd / 2.0)
        m = a > 0
        rgba[m] = (*TH["DATA"], 0); rgba[m, 3] = (150 + a[m] * 105).astype(np.uint8) if dark else 255
        m = hp > 0
        rgba[m] = (*TH["PILOT"], 255)
        self.setImage(rgba, levels=None, autoLevels=False)
        self.setRect(QtCore.QRectF(-lim, -lim, 2 * lim, 2 * lim))


def line_plot(left, units=None, bottom="time", bunits="s"):
    p = pg.PlotWidget(); p.showGrid(x=True, y=True, alpha=0.25)
    p.setLabel("left", left, units=units); p.setLabel("bottom", bottom, units=bunits)
    for ax in ("left", "bottom", "right"):
        p.getAxis(ax).enableAutoSIPrefix(False)          # no "mdB" / "ks"
    return p


ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def logo_pixmap(name, tint=None):
    """logo from assets/; tint = colour: the alpha channel is kept and every pixel painted in that colour"""
    pm = QtGui.QPixmap(os.path.join(ASSETS, name))
    if tint is not None and not pm.isNull():
        p = QtGui.QPainter(pm)
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), QtGui.QColor(tint)); p.end()
    return pm


class LogoBar(QtWidgets.QWidget):
    """style page banner: AMD logo, project title, FPGA contest 10th anniversary logo"""
    def __init__(self, fam):
        super().__init__()
        self.fam = fam
        self.amd = logo_pixmap("amd_logo.png", C_INK)
        self.contest = logo_pixmap("fpga_contest_10th.png", C_INK if TH["DARK"] else None)
        self.setMinimumHeight(96)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QtGui.QPen(QtGui.QColor(TH["CARD_BORDER"]), 1)); p.setBrush(QtGui.QColor(C_CARD))
        p.drawRoundedRect(r, 14, 14)
        p.fillRect(QtCore.QRectF(r.left() + 14, r.bottom() - 3, r.width() - 28, 3), QtGui.QColor(C_CLAY))
        h = r.height()
        x = r.left() + 24
        if not self.amd.isNull():                                    # left: AMD
            ah = min(h * 0.36, self.amd.height() * 1.2); aw = self.amd.width() * ah / self.amd.height()
            p.drawPixmap(QtCore.QRectF(x, r.center().y() - ah / 2, aw, ah), self.amd, QtCore.QRectF(self.amd.rect()))
            x += aw + 24
            p.fillRect(QtCore.QRectF(x, r.top() + h * 0.22, 1, h * 0.56), QtGui.QColor(C_LINE)); x += 24
        xr = r.right() - 24
        if not self.contest.isNull():                                # right: contest 10th anniversary
            ch = min(h * 0.74, self.contest.height() * 1.1); cw = self.contest.width() * ch / self.contest.height()
            xr -= cw
            p.drawPixmap(QtCore.QRectF(xr, r.center().y() - ch / 2, cw, ch), self.contest, QtCore.QRectF(self.contest.rect()))
            xr -= 24
        f = QtGui.QFont(self.fam); f.setPointSize(15); f.setBold(True); p.setFont(f); p.setPen(QtGui.QColor(C_INK))
        tr = QtCore.QRectF(x, r.top(), xr - x, h / 2 + 2)
        p.drawText(tr, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom, "DeepJSCC-Q  ·  OFDM 实时图像传输")
        f.setPointSize(10); f.setBold(False); p.setFont(f); p.setPen(QtGui.QColor(C_MUTED))
        p.drawText(QtCore.QRectF(x, r.top() + h / 2 + 6, xr - x, h / 2 - 6),
                   QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop, "AD9361  +  Zynq UltraScale+ ZU5EG  ·  Mali-400 GPU")
        p.end()


class ThemeCard(QtWidgets.QWidget):
    """one preset on the style page, drawn in its own colours (mini constellation, bars, |H| colour map)"""
    picked = QtCore.pyqtSignal(str)

    def __init__(self, key, fam):
        super().__init__()
        self.key, self.t, self.fam = key, THEMES[key], fam
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def mouseReleaseEvent(self, e):
        if self.rect().contains(e.pos()):
            self.picked.emit(self.key)

    def paintEvent(self, e):
        t, cur = self.t, self.key == args.theme
        C = QtGui.QColor
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QtGui.QPen(C(t["ACC"] if cur else C_LINE), 3 if cur else 1)); p.setBrush(C(t["BG"]))
        p.drawRoundedRect(r, 12, 12)
        # header strip like the panel titles of that theme
        hr = QtCore.QRectF(r.left() + 10, r.top() + 10, r.width() - 20, 30)
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(C(t["HDR_BG"])); p.drawRoundedRect(hr, 5, 5)
        p.fillRect(QtCore.QRectF(hr.left(), hr.top(), 3, hr.height()), C(t["ACC"]))
        f = QtGui.QFont(self.fam); f.setPointSize(12); f.setBold(True); p.setFont(f); p.setPen(C(t["HDR_FG"]))
        p.drawText(hr.adjusted(12, 0, -8, 0), QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, t["LABEL"])
        f.setPointSize(9); f.setBold(cur); p.setFont(f); p.setPen(C(t["MUTED"] if t["HDR_BG"] != t["ACC"] else t["HDR_FG"]))
        p.drawText(hr.adjusted(12, 0, -10, 0), QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight,
                   "✓ 当前" if cur else t["SUB"])
        # body: mini constellation | bars + colour map
        body = QtCore.QRectF(r.left() + 10, hr.bottom() + 8, r.width() - 20, r.bottom() - hr.bottom() - 18)
        side = min(body.height(), body.width() * 0.42)
        cr = QtCore.QRectF(body.left(), body.top(), side, side)
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(C(t["PLOT"])); p.drawRoundedRect(cr, 6, 6)
        p.setPen(QtGui.QPen(C(*t["GRID"], 60), 1))
        for k in (1, 2, 3):
            p.drawLine(QtCore.QPointF(cr.left() + cr.width() * k / 4, cr.top() + 4), QtCore.QPointF(cr.left() + cr.width() * k / 4, cr.bottom() - 4))
            p.drawLine(QtCore.QPointF(cr.left() + 4, cr.top() + cr.height() * k / 4), QtCore.QPointF(cr.right() - 4, cr.top() + cr.height() * k / 4))
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(C(*t["DATA"])); d = max(2.5, side / 30)
        for i in range(4):
            for j in range(4):
                p.drawEllipse(QtCore.QPointF(cr.left() + cr.width() * (i + 0.5) / 4, cr.top() + cr.height() * (j + 0.5) / 4), d, d)
        p.setBrush(C(*t["PILOT"]))
        for i in (0.5, 3.5):
            p.drawEllipse(QtCore.QPointF(cr.left() + cr.width() * i / 4, cr.center().y()), d * 1.3, d * 1.3)
        br = QtCore.QRectF(cr.right() + 10, body.top(), body.right() - cr.right() - 10, side)
        p.setBrush(C(t["PLOT"])); p.drawRoundedRect(br, 6, 6)
        cmh = max(8.0, side * 0.14)                                  # |H| colour map strip
        grad = QtGui.QLinearGradient(br.left(), 0, br.right(), 0)
        for k, c in enumerate(t["CMAP"]):
            grad.setColorAt(k / (len(t["CMAP"]) - 1), C(*c))
        p.setBrush(QtGui.QBrush(grad)); p.drawRoundedRect(QtCore.QRectF(br.left() + 6, br.bottom() - cmh - 6, br.width() - 12, cmh), 3, 3)
        hs = [0.55, 0.7, 0.62, 0.8, 0.74, 0.9, 0.66, 0.78, 0.6, 0.72]
        bw = (br.width() - 12) / len(hs); top = br.top() + 8; bot = br.bottom() - cmh - 12
        p.setBrush(C(t["ACC"]))
        for k, v in enumerate(hs):
            p.drawRect(QtCore.QRectF(br.left() + 6 + k * bw + 1, bot - (bot - top) * v, bw - 2, (bot - top) * v))
        p.end()


TX_ATT0 = 0.0                                 # TX1 attenuation set by tx_camera.py at start (--att): the "0 dB" point


class AttenBar(QtWidgets.QFrame):
    """touch control of the TX attenuation (extra dB on top of the config LUT value); shown only while the TX board
    answers (boards cabled, demo step 2). Several bars (one per page) stay in step through Gui.set_atten."""
    MAX = 48                                    # slider range, dB (at the bench distance sync is lost near +45 dB)
    STEP = 4                                    # slider units per dB (smooth animation)

    def __init__(self, gui, presets, two_rows=False, modes=True, sources=False, side=False):
        """side: the source / mode buttons and the label + value go to a separate frame (self.top_frame, placed by
        the caller, e.g. a side column); this frame keeps the - slider + presets row"""
        super().__init__()
        self.gui = gui
        self.setObjectName("attbar")
        self.top_frame = None
        if side:
            h = QtWidgets.QHBoxLayout(self); h.setContentsMargins(10, 6, 10, 6); h.setSpacing(8)
            self.top_frame = QtWidgets.QFrame(); self.top_frame.setObjectName("attbar")
            tv = QtWidgets.QVBoxLayout(self.top_frame); tv.setContentsMargins(10, 8, 10, 8); tv.setSpacing(6)
            top_src, top_mode, top = (QtWidgets.QHBoxLayout() for _ in range(3))
            for r in (top_src, top_mode, top):
                tv.addLayout(r)
        elif two_rows:                               # narrow column: label + value above, - slider + below
            v = QtWidgets.QVBoxLayout(self); v.setContentsMargins(10, 6, 10, 6); v.setSpacing(6 if COMPACT else 2)
            top = QtWidgets.QHBoxLayout(); v.addLayout(top); h = QtWidgets.QHBoxLayout(); h.setSpacing(8); v.addLayout(h)
        else:
            h = top = QtWidgets.QHBoxLayout(self); h.setContentsMargins(10, 4, 10, 4); h.setSpacing(8)
        if not side:
            top_src = top_mode = top
        self.mbtn, self.sbtn = {}, {}
        for v, label in ((("camera", "摄像头"), ("preset", "预设")) if sources else ()):   # 预设: video -> stills -> video
            b = QtWidgets.QPushButton(label); b.setCheckable(True); b.setObjectName("attmode"); top_src.addWidget(b)
            b.clicked.connect(lambda _=False, v=v: self.gui.set_source(v)); self.sbtn[v] = b
        if sources and not side:
            top.addSpacing(10)
        for m in (("jscc", "sscc") if modes else ()):
            b = QtWidgets.QPushButton(m.upper()); b.setCheckable(True); b.setObjectName("attmode"); top_mode.addWidget(b)
            b.clicked.connect(lambda _=False, m=m: self.gui.set_mode(m)); self.mbtn[m] = b
        lab = QtWidgets.QLabel("TX 衰减"); lab.setObjectName("attlab"); top.addWidget(lab)
        self.minus = QtWidgets.QPushButton("−"); self.plus = QtWidgets.QPushButton("+")
        self.minus.setObjectName("attpm"); self.plus.setObjectName("attpm")
        self.sl = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); self.sl.setRange(0, self.MAX * self.STEP); self.sl.setPageStep(5 * self.STEP)
        self.hw = 40 if COMPACT else 26              # slider handle width (touch screen: finger sized)
        if COMPACT:
            self.sl.setMinimumHeight(48)
        self.val = QtWidgets.QLabel(); self.val.setObjectName("attval"); self.val.setMinimumWidth(78)
        self.val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        if two_rows or side:
            top.addStretch(1); top.addWidget(self.val)
            h.addWidget(self.minus); h.addWidget(self.sl, 1); h.addWidget(self.plus)
        else:
            h.addWidget(self.minus); h.addWidget(self.sl, 1); h.addWidget(self.plus); h.addWidget(self.val)
        self.pbtn = {}
        for v in presets:
            b = QtWidgets.QPushButton(f"{v}"); b.setObjectName("attpre"); h.addWidget(b); self.pbtn[v] = b
            b.clicked.connect(lambda _=False, v=v: self.gui.set_atten(v))
        for b in (self.minus, self.plus):            # press and hold: 1 dB every 100 ms after 400 ms (= TX up ramp 10 dB/s)
            b.setAutoRepeat(True); b.setAutoRepeatDelay(400); b.setAutoRepeatInterval(100)
        self.minus.clicked.connect(lambda: self.gui.set_atten(round(self.gui.att_extra) - 1))
        self.plus.clicked.connect(lambda: self.gui.set_atten(round(self.gui.att_extra) + 1))
        self.sl.sliderReleased.connect(lambda: self.gui.set_atten(round(self.sl.value() / self.STEP)))
        self.sl.sliderMoved.connect(lambda v: self.show_value(self.gui.att_disp))   # label / colour while dragging
        self.show_value(0)

    def setVisible(self, on):
        super().setVisible(on)
        if self.top_frame is not None:
            self.top_frame.setVisible(on)

    def show_mode(self, mode, source=None):
        for m, b in self.mbtn.items():
            b.setChecked(m == mode)
        for v, b in self.sbtn.items():
            b.setChecked(v == source or (v == "preset" and source is not None and source != "camera"))

    GRAD = [(0.0, (34, 197, 94)), (0.5, (234, 179, 8)), (1.0, (239, 68, 68))]    # green -> yellow -> red (full scale)

    @classmethod
    def grad_at(cls, f):
        for (f0, c0), (f1, c1) in zip(cls.GRAD, cls.GRAD[1:]):
            if f <= f1:
                a = (f - f0) / (f1 - f0)
                return tuple(int(round(x0 + a * (x1 - x0))) for x0, x1 in zip(c0, c1))
        return cls.GRAD[-1][1]

    def paint_fill(self, f):
        """filled part = the full-scale gradient cut at f (green at 0 dB, red at the end), handle ring in its colour"""
        k = int(round(f * 64))
        if k == getattr(self, "_fill_k", None):
            return
        self._fill_k = k; f = k / 64
        rgb = lambda c: "rgb(%d,%d,%d)" % c
        end = self.grad_at(f)
        stops = [f"stop:0 {rgb(self.GRAD[0][1])}"]
        if f > 0.5:
            stops.append(f"stop:{0.5 / f:.3f} {rgb(self.GRAD[1][1])}")
        stops.append(f"stop:1 {rgb(end)}")
        g, hw = (12, self.hw) if COMPACT else (8, self.hw)
        self.sl.setStyleSheet(
            "QSlider { background: transparent; }"
            f"QSlider::groove:horizontal {{ height: {g}px; background: {C_LINE}; border-radius: {g // 2}px; }}"
            f"QSlider::sub-page:horizontal {{ border-radius: {g // 2}px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"{', '.join(stops)}); }}"
            f"QSlider::handle:horizontal {{ background: {C_INK}; border: {3 if COMPACT else 2}px solid {rgb(end)}; "
            f"width: {hw}px; margin: -{(hw - g) // 2}px 0; border-radius: {hw // 2}px; }}")
        self.val.setStyleSheet(f"color: {rgb(end)};")

    def show_value(self, disp):
        """disp: attenuation shown now (animated towards the target, see Gui.anim_atten)"""
        if self.sl.isSliderDown():                   # dragging: the handle and the label follow the finger
            self.val.setText(f"+{self.sl.value() / self.STEP:.0f} dB")
            self.paint_fill(self.sl.value() / (self.MAX * self.STEP))
            return
        self.sl.blockSignals(True); self.sl.setValue(int(round(disp * self.STEP))); self.sl.blockSignals(False)
        self.val.setText(f"+{disp:.0f} dB")
        self.paint_fill(disp / self.MAX)


class Ripple(QtWidgets.QWidget):
    """transparent overlay: an expanding ring where the remote director 'taps', and a white flash (sync mark)"""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.rings, self.flash_t = [], 0.0
        self.tm = QtCore.QTimer(self); self.tm.timeout.connect(self.tick)

    def tap(self, pos):
        self.rings.append((pos, time.time())); self.raise_(); self.show(); self.tm.start(33)

    def flash(self):
        self.flash_t = time.time(); self.raise_(); self.show(); self.tm.start(33)

    def tick(self):
        now = time.time()
        self.rings = [r for r in self.rings if now - r[1] < 0.8]
        if not self.rings and now - self.flash_t > 0.35:
            self.tm.stop(); self.hide()
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        now = time.time()
        if now - self.flash_t < 0.25:
            p.fillRect(self.rect(), QtGui.QColor(255, 255, 255))
        for pos, t0 in self.rings:
            a = (now - t0) / 0.8
            c = QtGui.QColor(C_CLAY); c.setAlphaF(max(0.0, 1.0 - a))
            p.setPen(QtGui.QPen(c, 4)); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            r = 12 + 38 * a
            p.drawEllipse(QtCore.QPointF(pos), r, r)
            c.setAlphaF(max(0.0, 0.5 - a)); p.setBrush(c); p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.drawEllipse(QtCore.QPointF(pos), 10, 10)
        p.end()


class Remote(QtCore.QThread):
    """UDP text commands for the demo director (PC, 192.168.3.x or local), answered with one JSON line:
        tap tab:N | tap mode:jscc|sscc | tap src:camera|video | tap att:V | tap att+ | tap att-   (ripple + press)
        page N | mode M | src S | atten V          (same effect without the ripple)
        flash                                      (white frame: sync mark for the recordings)
        status                                     (link / display state)"""
    cmd = QtCore.pyqtSignal(str, object)

    def __init__(self, port):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port)); self.sock.settimeout(0.5)
        self.running = True

    def run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            if not (addr[0].startswith("192.168.3.") or addr[0] == "127.0.0.1"):
                continue
            self.cmd.emit(data.decode(errors="replace").strip(), addr)

    def reply(self, addr, obj):
        import json
        try:
            self.sock.sendto(json.dumps(obj).encode(), addr)
        except OSError:
            pass


class SystemDiagram(QtWidgets.QWidget):
    """System structure (the demo video's opening picture, demo_video/make_intro_assets.py 'chain'), drawn in the
    theme colours and scaled to the widget. Logical canvas 1920 x 1060: larger type than the picture (it fills the
    left part of the About page on the 1024 x 600 screen)."""
    TX = [("摄像头", "USB · MJPG\n/ 演示视频", ""), ("采集 / 裁剪", "256×256 RGB\nDMA → PL", "PS"),
          ("DeepJSCC-Q\n编码器", "CNN · W8A12 · 250 MHz\n→ 64QAM 星座点", "PL"), ("OFDM 发射", "64 点 IFFT · 导频\n前导 · 帧缓存", "PL"),
          ("AD9361", "915 MHz\n20 MSPS", "子卡"), ("天线", "", "")]
    RX = [("触摸屏", "实时显示", ""), ("重建图像", "PL → DMA → PS", "PS"),
          ("DeepJSCC-Q\n解码器", "均衡后软符号直接输入\n无硬判决", "PL"), ("OFDM 接收", "同步 · 双 LTF 信道估计\nSFO / CPE 跟踪", "PL"),
          ("AD9361", "AGC\n20 MSPS", "子卡"), ("天线", "", "")]
    WID, GAP = [220, 240, 380, 350, 240, 140], 36
    CW, CH = 1920, 1060
    GY, GH, BY, BH = (20, 620), 420, 90, 290           # group tops, group height, box offset in the group, box height

    def __init__(self, fam):
        super().__init__()
        self.fam = fam
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        x = (self.CW - sum(self.WID) - self.GAP * (len(self.WID) - 1)) / 2
        self.BX = []
        for w in self.WID:
            self.BX.append((x, w)); x += w + self.GAP

    @staticmethod
    def _mix(a, b, f):
        a, b = QtGui.QColor(a), QtGui.QColor(b)
        return QtGui.QColor(int(a.red() + (b.red() - a.red()) * f), int(a.green() + (b.green() - a.green()) * f),
                            int(a.blue() + (b.blue() - a.blue()) * f))

    def _font(self, px):
        """regular weight: most theme fonts have no CJK bold, the synthetic one smears at this scale"""
        f = QtGui.QFont(self.fam); f.setPixelSize(px); return f

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        s = min(self.width() / self.CW, self.height() / self.CH)
        p.translate((self.width() - self.CW * s) / 2, (self.height() - self.CH * s) / 2); p.scale(s, s)
        acc, sec, card, ink, muted = (QtGui.QColor(c) for c in (C_CLAY, C_BLUE, C_CARD, C_INK, C_MUTED))
        AC, AT, AH = QtCore.Qt.AlignmentFlag.AlignCenter, QtCore.Qt.AlignmentFlag.AlignTop, QtCore.Qt.AlignmentFlag.AlignHCenter
        x_l, x_r = self.BX[0][0] - 30, self.BX[-1][0] + self.BX[-1][1] + 30
        for gi, (title, items, gcol) in enumerate((("发射端 TX · PYNQ-ZU（Zynq UltraScale+ ZU5EG）", self.TX, acc),
                                                  ("接收端 RX · PYNQ-ZU（Zynq UltraScale+ ZU5EG）", self.RX, sec))):
            gy = self.GY[gi]
            p.setPen(QtGui.QPen(gcol, 3, QtCore.Qt.PenStyle.DashLine)); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(x_l, gy, x_r - x_l, self.GH), 24, 24)
            p.setFont(self._font(38)); p.setPen(gcol)
            p.drawText(QtCore.QRectF(x_l + 26, gy + 12, 1700, 60), QtCore.Qt.AlignmentFlag.AlignVCenter, title)
            by = gy + self.BY
            for k, ((name, sub, tag), (x, w)) in enumerate(zip(items, self.BX)):
                border = acc if tag == "PL" else (sec if tag == "子卡" else muted)
                p.setPen(QtGui.QPen(border, 3)); p.setBrush(self._mix(card, border, 0.14 if tag in ("PL", "子卡") else 0.0))
                p.drawRoundedRect(QtCore.QRectF(x, by, w, self.BH), 14, 14)
                if tag:
                    p.setFont(self._font(24)); p.setPen(border)
                    p.drawText(QtCore.QRectF(x + 14, by + 8, 100, 30), QtCore.Qt.AlignmentFlag.AlignVCenter, tag)
                p.setFont(self._font(42)); p.setPen(ink)
                p.drawText(QtCore.QRectF(x, by + 36, w, 116 if sub else self.BH - 72), AC, name)
                if sub:
                    p.setFont(self._font(28)); p.setPen(muted)
                    p.drawText(QtCore.QRectF(x + 6, by + 166, w - 12, 110), AT | AH, sub)
                if k < len(items) - 1:                       # arrow to the next block (TX: right, RX: left)
                    x0, x1 = x + w + 5, self.BX[k + 1][0] - 5
                    if gi == 1:
                        x0, x1 = x1, x0
                    self._arrow(p, QtCore.QPointF(x0, by + self.BH / 2), QtCore.QPointF(x1, by + self.BH / 2), muted)
        # air interface: TX antenna -> RX antenna
        ax = self.BX[-1][0] + self.BX[-1][1] / 2
        y0, y1 = self.GY[0] + self.BY + self.BH, self.GY[1] + self.BY
        p.setPen(QtGui.QPen(ink, 4, QtCore.Qt.PenStyle.DashLine)); p.drawLine(QtCore.QPointF(ax, y0 + 6), QtCore.QPointF(ax, y1 - 20))
        self._arrow(p, QtCore.QPointF(ax, y1 - 30), QtCore.QPointF(ax, y1 - 4), ink)
        p.setFont(self._font(38)); p.setPen(ink)
        p.drawText(QtCore.QRectF(ax - 480, (y0 + y1) / 2 - 30, 450, 60),
                   QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter, "射频空口  ·  915 MHz")
        p.end()

    @staticmethod
    def _arrow(p, a, b, col):
        p.setPen(QtGui.QPen(col, 4)); p.drawLine(a, b)
        d = b - a; n = max((d.x() ** 2 + d.y() ** 2) ** 0.5, 1e-6); ux, uy = d.x() / n, d.y() / n
        h = 16
        poly = QtGui.QPolygonF([b, QtCore.QPointF(b.x() - ux * h - uy * h * 0.6, b.y() - uy * h + ux * h * 0.6),
                                QtCore.QPointF(b.x() - ux * h + uy * h * 0.6, b.y() - uy * h - ux * h * 0.6)])
        p.setBrush(col); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPolygon(poly); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)


class FitImage(QtWidgets.QWidget):
    """picture scaled to fit (aspect kept), centred; a tap emits clicked (About page: enlarge / back)"""
    clicked = QtCore.pyqtSignal(object)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.pm = QtGui.QPixmap(path)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)

    def set_path(self, path):
        self.path = path; self.pm = QtGui.QPixmap(path); self.update()

    def mouseReleaseEvent(self, e):
        self.clicked.emit(self.path)

    def paintEvent(self, e):
        if self.pm.isNull():
            return
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        s = min(self.width() / self.pm.width(), self.height() / self.pm.height())
        w, h = self.pm.width() * s, self.pm.height() * s
        r = QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.drawPixmap(r, self.pm, QtCore.QRectF(self.pm.rect()))
        p.setPen(QtGui.QPen(QtGui.QColor(C_LINE), 1)); p.drawRect(r)
        p.end()


# About page topics: (button, title, text (rich), pictures in assets/about/). Facts as in the demo video.
ABOUT_TOPICS = [
    ("系统简介", "系统简介", """
本作品在两块 <b>PYNQ-ZU</b>（Zynq UltraScale+ ZU5EG）开发板上实现<b>实时无线图像传输</b>，两板各接一块
<b>AD9361</b> 射频子卡，经 915 MHz 射频空口连接。<br><br>
<b>发射端</b>：采集 USB 摄像头或演示视频，裁剪为 256×256 彩色图像，由 PL 中的 <b>DeepJSCC-Q 编码器</b>直接映射为
64QAM 星座点，经类 802.11a 的 OFDM 发射机送入 AD9361。<br><br>
<b>接收端</b>：完成同步、双 LTF 信道估计和逐符号的 SFO / CPE 跟踪，把均衡后的软符号直接送入
<b>DeepJSCC-Q 解码器</b>重建图像，显示在本触摸屏上。<br><br>
整条链路实时运行于 <b>30 fps</b>：每帧即一幅图像 = 32768 个 64QAM 符号 = 683 个 OFDM 符号。
系统内置分离式编码（SSCC）基线，与 DeepJSCC-Q 共用同一物理层，可运行时切换对比。""", ["hardware.jpg"]),
    ("对比实验", "对比实验 · SSCC 与 DeepJSCC-Q", """
依据<b>香农分离定理</b>，信源编码与信道编码可以独立设计。<br><br>
<b>SSCC 基线</b>：JPEG 压缩（每帧 16,360 字节）→ 扰码 → LDPC（802.11n，n = 1944，R = 2/3）→ 64QAM。<br>
<b>DeepJSCC-Q</b>：单个神经网络，把图像直接映射为信道符号。<br><br>
<b>对照条件</b>：两者每幅图像都占 32768 个 64QAM 符号、683 个 OFDM 符号，发射功率相同，
使用同一个 OFDM 发射链和同一组板卡。<br><br>
<b>结果</b>：增加 TX 衰减后，SSCC 的误码一旦超出纠错能力，图像整体失效，即<b>悬崖效应</b>
（分离定理的最优性依赖无限码长与已知信道）。DeepJSCC-Q 在 SSCC 已经失效的衰减下
质量基本不变，继续加大衰减时平滑下降，<b>没有悬崖</b>。可在「图像链路」页用衰减滑条亲自对比。""", ["fairness.png"]),
    ("星座约束", "DeepJSCC-Q · 星座约束", """
原始 DeepJSCC 的编码器输出<b>任意复数值</b>，现有通信硬件无法直接发送。
作为对照，GLOBECOM 2025 的 FPGA 实现为此<b>改造了商用 5G 基站与终端的 I/Q 接口</b>。<br><br>
<b>DeepJSCC-Q</b> 在训练中把编码器输出约束于 <b>64QAM 星座</b>，因此可以直接接入标准的
OFDM 发射链；接收端看到的是标准的 64 点格（见「PHY detail」页的星座图）。<br><br>
解码器直接使用均衡后的<b>软符号</b>，不做硬判决，也不需要信道译码。
物理层沿用类 802.11a 的 OFDM 帧：64 点 FFT，4 个导频，长短训练序列前导。""", ["globecom_iq.png"]),
    ("采样频偏", "接收机 · 长帧采样频偏补偿", """
参考实现 openofdm 每个 OFDM 符号只补偿一个<b>公共相位误差（CPE）</b>，适用于短帧。<br><br>
本系统<b>一帧即一幅图像</b>，长 683 个 OFDM 符号，是 802.11a 最长帧（152 个符号）的 <b>4.5 倍</b>。<br><br>
收发两端 <b>1 ppm</b> 的时钟偏差，会在帧尾使边缘子载波偏转约 <b>8°</b>，
超出 64QAM 外圈 ±4.7° 的判决余量。<br><br>
因此接收机引入<b>逐符号跟踪的采样频偏（SFO）补偿</b>：补偿前星座随符号旋转，补偿后收敛为格点。""",
     ["frame_length.png", "sfo_phase.png"]),
    ("硬件架构", "硬件 · 逐层流式卷积引擎", """
对照设计（GLOBECOM 2025，ZCU111）采用分块并行，把完整的中间特征图存于片上，<b>BRAM 占用约 96%</b>。<br><br>
本设计采用<b>逐层流式卷积引擎</b>：<br>
· <b>行缓冲</b>：每层只缓存 K−1 行，而非整幅特征图；<br>
· <b>并行度</b>：各层并行度按该层运算量，取满足 30 fps 的最小值；<br>
· <b>权重存储</b>：每拍 P 路 × 8 bit 权重，打包进 BRAM36 的 72 bit 宽字。<br><br>
结果：每帧计算量（编 / 解 0.913 / 1.417 GMAC）约为对照设计的 <b>10 倍</b>，BRAM 用量（100 / 100 块）
约为其<b>十分之一</b>，在更小的 ZU5EG 上实时运行于 30 fps。""", ["our_arch.png", "compare_table.png"]),
    ("操作说明", "操作说明", """
<b>图像链路</b>：显示 RX 重建图像（两板连线时同时显示 TX 原图）和 PSNR、端到端延时（TX 取帧 → RX 收到图像）、帧率、EVM 等指标。<br>
· 摄像头 / 预设：切换发射端的图像来源；「预设」每点一次依次切换：演示视频 → 5 张 DIV2K 测试图 → 演示视频；<br>
· JSCC / SSCC：切换编码方案；<br>
· TX 衰减：拖动滑条，或点按 − / +（长按连续调整），或用 0 / 10 / 20 / 30 预设；
衰减增大时以 10 dB/s 平滑爬升。<br><br>
<b>PHY detail</b>：信道估计后与最终的星座图、|H| 随时间变化的三维图、每子载波 SNR、接收频谱。<br><br>
<b>风格</b>：切换界面主题，选择会被记住。<br><br>
<b>介绍</b>：本页。点击右侧按钮查看各部分说明，「系统结构」回到结构图。""", []),
]


class ClawdOverlay(QtWidgets.QWidget):
    """Easter egg (About page: tap the title 5 times): Clawd, the Claude Code mascot, as simple pixel art walks in,
    waves, says hello and walks out. Transparent overlay over the whole window; taps go through."""
    BODY = ["..XXXXXXXXXX..",
            "..XXXXXXXXXX..",
            "..XXEXXXXEXX..",
            "AAXXEXXXXEXXAA",
            "AAXXXXXXXXXXAA",
            "..XXXXXXXXXX.."]
    LEGS = ([3, 5, 8, 10], [4, 6, 7, 9])               # leg columns, two walking frames
    CLAY, EYE = QtGui.QColor("#D97757"), QtGui.QColor("#1A1A1A")
    WALK_IN, STAY, WALK_OUT = 3.0, 4.5, 3.0            # seconds
    SAY = ("嗨，我是 Clawd！", "本系统由参赛者与 AI Agent 协作完成。")

    def __init__(self, parent, fam):
        super().__init__(parent)
        self.fam = fam
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.tm = QtCore.QTimer(self); self.tm.timeout.connect(self.update)
        self.t0 = 0.0
        self.hide()

    def start(self):
        if self.isVisible():
            return
        self.setGeometry(self.parentWidget().rect()); self.raise_(); self.show()
        self.t0 = time.time(); self.tm.start(60)

    def paintEvent(self, e):
        t = time.time() - self.t0
        total = self.WALK_IN + self.STAY + self.WALK_OUT
        if t > total:
            self.tm.stop(); self.hide(); return
        px = max(6, self.height() // 75)                # pixel size (8 px on the 600 px screen)
        cw, ch = 14 * px, 8 * px
        xc = (self.width() - cw) / 2
        y = self.height() - ch - int(self.height() * 0.07)
        walking = t < self.WALK_IN or t > self.WALK_IN + self.STAY
        if t < self.WALK_IN:
            x = self.width() + (xc - self.width()) * (t / self.WALK_IN)
        elif t <= self.WALK_IN + self.STAY:
            x = xc
        else:
            x = xc - (xc + cw + 10) * ((t - self.WALK_IN - self.STAY) / self.WALK_OUT)
        step = int(t * 8) % 2
        bob = -px // 2 if (walking and step) else 0
        stay = t - self.WALK_IN
        blink = (not walking) and (0.8 < stay < 0.95 or 2.6 < stay < 2.75)
        wave = (not walking) and 1.2 < stay < 2.4 and int(stay * 6) % 2 == 0
        p = QtGui.QPainter(self)
        for r, row in enumerate(self.BODY):
            for c, ch_ in enumerate(row):
                if ch_ == ".":
                    continue
                if ch_ == "A" and c >= 12 and wave:     # right arm up: drawn one row higher
                    rr = r - 2
                else:
                    rr = r
                col = self.EYE if ch_ == "E" and not (blink and r == 2) else self.CLAY
                p.fillRect(QtCore.QRectF(x + c * px, y + rr * px + bob, px, px), col)
        legs = self.LEGS[step] if walking else self.LEGS[0]
        for c in legs:
            p.fillRect(QtCore.QRectF(x + c * px, y + 6 * px + bob, px, 2 * px), self.CLAY)
        if not walking and stay > 0.4:                  # speech bubble
            f = QtGui.QFont(self.fam); f.setPixelSize(max(14, px * 2)); p.setFont(f)
            fm = QtGui.QFontMetrics(f)
            tw = max(fm.horizontalAdvance(s) for s in self.SAY) + 4 * px
            th = fm.height() * len(self.SAY) + 3 * px
            bx = min(max(8, x + cw / 2 - tw / 2), self.width() - tw - 8); by = y - th - 3 * px
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(QtGui.QPen(self.CLAY, 2)); p.setBrush(QtGui.QColor(C_CARD))
            p.drawRoundedRect(QtCore.QRectF(bx, by, tw, th), 10, 10)
            tail = QtGui.QPolygonF([QtCore.QPointF(x + cw / 2 - px, by + th - 1), QtCore.QPointF(x + cw / 2 + px, by + th - 1),
                                    QtCore.QPointF(x + cw / 2, by + th + 2 * px)])
            p.drawPolygon(tail)
            p.setPen(QtGui.QColor(C_INK))
            for k, s in enumerate(self.SAY):
                p.drawText(QtCore.QRectF(bx, by + 1.5 * px + k * fm.height(), tw, fm.height()),
                           QtCore.Qt.AlignmentFlag.AlignCenter, s)
        p.end()


class TapLabel(QtWidgets.QLabel):
    """QLabel that counts quick taps: 'triggered' after n taps within `window` seconds"""
    triggered = QtCore.pyqtSignal()

    def __init__(self, n=5, window=3.0):
        super().__init__()
        self.n, self.window, self.taps = n, window, []

    def mousePressEvent(self, e):
        now = time.time()
        self.taps = [t for t in self.taps if now - t < self.window] + [now]
        if len(self.taps) >= self.n:
            self.taps = []; self.triggered.emit()


class AboutPage(QtWidgets.QWidget):
    """structure diagram (large) + topic buttons on the right; a topic covers the diagram with text + pictures"""
    def __init__(self, fam, compact):
        super().__init__()
        h = QtWidgets.QHBoxLayout(self); h.setContentsMargins(8, 4, 8, 6); h.setSpacing(8)
        left = QtWidgets.QVBoxLayout(); left.setSpacing(6); h.addLayout(left, 1)
        self.title = TapLabel(); self.title.setObjectName("hdr"); left.addWidget(self.title, 0)
        self.title.triggered.connect(self.easter_egg); self.fam = fam; self.clawd = None   # 5 quick taps
        self.stack = QtWidgets.QStackedWidget(); left.addWidget(self.stack, 1)
        self.stack.addWidget(SystemDiagram(fam))
        self.titles = ["DeepJSCC-Q 实时无线图像传输系统  ·  系统结构"]
        pt = 13 if compact else 16
        for _, title, html, pics in ABOUT_TOPICS:
            w = QtWidgets.QWidget(); r = QtWidgets.QHBoxLayout(w); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(10)
            t = QtWidgets.QLabel(" ".join(html.split("\n")).strip()); t.setObjectName("card"); t.setWordWrap(True)
            t.setTextFormat(QtCore.Qt.TextFormat.RichText); t.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            f = t.font(); f.setFamily(fam); f.setPointSize(pt); t.setFont(f)
            t.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
            r.addWidget(t, 12 if pics else 1)
            if pics:
                col = QtWidgets.QVBoxLayout(); col.setSpacing(8); r.addLayout(col, 10)
                for name in pics:
                    im = FitImage(os.path.join(ASSETS, "about", name)); im.clicked.connect(self.zoom_in)
                    col.addWidget(im, 1)
                hint = QtWidgets.QLabel("点击图片放大"); hint.setStyleSheet(f"color: {C_MUTED};")
                hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); col.addWidget(hint, 0)
            self.stack.addWidget(w); self.titles.append(title)
        # enlarged picture (covers the content area; a tap goes back to the topic)
        zw = QtWidgets.QWidget(); zl = QtWidgets.QVBoxLayout(zw); zl.setContentsMargins(0, 0, 0, 0); zl.setSpacing(4)
        self.zoom = FitImage(""); self.zoom.clicked.connect(lambda _: self.show_topic(self.cur))
        zl.addWidget(self.zoom, 1)
        zh = QtWidgets.QLabel("点击图片返回"); zh.setStyleSheet(f"color: {C_MUTED};")
        zh.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); zl.addWidget(zh, 0)
        self.stack.addWidget(zw); self.cur = 0
        right = QtWidgets.QVBoxLayout(); right.setSpacing(8); h.addLayout(right, 0)
        self.group = QtWidgets.QButtonGroup(self); self.group.setExclusive(True)
        for k, label in enumerate(["系统结构"] + [t[0] for t in ABOUT_TOPICS]):
            b = QtWidgets.QPushButton(label); b.setCheckable(True); b.setObjectName("aboutbtn")
            b.clicked.connect(lambda _=False, k=k: self.show_topic(k))
            self.group.addButton(b, k); right.addWidget(b, 1)
        self.show_topic(0)

    def show_topic(self, k):
        self.cur = k
        self.stack.setCurrentIndex(k); self.title.setText(self.titles[k]); self.group.button(k).setChecked(True)

    def easter_egg(self):
        if self.clawd is None:
            self.clawd = ClawdOverlay(self.window(), self.fam)
        self.clawd.start()

    def zoom_in(self, path):
        self.zoom.set_path(path); self.stack.setCurrentIndex(self.stack.count() - 1)
        self.title.setText(self.titles[self.cur] + "  ·  放大")


class TouchHold(QtCore.QThread):
    """board touch screen: finger down / move / up straight from evdev.

    The panel (wch.cn USB2IIC_CTP_CONTROL, a 10-finger HID digitizer) runs on hid-generic (the PYNQ kernel has no
    hid-multitouch): the empty finger slots clear BTN_TOUCH / BTN_TOOL_FINGER in every report, so libinput / X see
    every touch as an instant tap (no hold, no drag). The reports themselves are fine: ~90 per second while the finger
    is down (ABS_X / ABS_Y of finger 0, 0..32767), and one with X = Y = 0 when it is lifted."""
    down = QtCore.pyqtSignal(int, int)               # global screen pixels
    move = QtCore.pyqtSignal(int, int)
    up = QtCore.pyqtSignal()
    FULL, GAP = 32767, 0.15                          # logical maximum; no report for GAP s = lifted

    @staticmethod
    def find(dev):
        if dev != "auto":
            return dev if dev != "off" and os.access(dev, os.R_OK) else None
        try:
            for blk in open("/proc/bus/input/devices").read().split("\n\n"):
                if "USB2IIC_CTP" in blk:
                    ev = [w for w in blk.split() if w.startswith("event")]
                    p = "/dev/input/" + ev[0] if ev else None
                    return p if p and os.access(p, os.R_OK) else None
        except OSError:
            pass
        return None

    def __init__(self, path, size):
        super().__init__()
        self.path, self.size = path, size            # size: QSize of the screen the panel covers

    def run(self):
        import select, struct
        fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        x = y = 0; fx = fy = None; active = False; t_last = 0.0
        while True:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                if active and time.time() - t_last > self.GAP:
                    active = False; self.up.emit()
                continue
            try:
                buf = os.read(fd, 24 * 64)
            except BlockingIOError:
                continue
            for i in range(0, len(buf) - 23, 24):
                _, _, typ, code, val = struct.unpack_from("llHHi", buf, i)
                if typ == 3 and code in (0, 1):      # ABS_X / ABS_Y (finger 0)
                    if code == 0: fx = val
                    else: fy = val
                elif typ == 0 and code == 0:         # SYN_REPORT: one HID report
                    x = x if fx is None else fx; y = y if fy is None else fy
                    lifted = fx == 0 and fy == 0
                    fx = fy = None; t_last = time.time()
                    gx = int(x * (self.size.width() - 1) / self.FULL); gy = int(y * (self.size.height() - 1) / self.FULL)
                    if lifted:
                        if active:
                            active = False; self.up.emit()
                    elif not active:
                        active = True; self.down.emit(gx, gy)
                    else:
                        self.move.emit(gx, gy)


def wifi_link(dev="wlan0"):
    """(IPv4 address, SSID) of the board's WiFi, None where not connected"""
    import subprocess
    def run(*c):
        try:
            return subprocess.run(c, capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    f = run("ip", "-4", "-o", "addr", "show", dev).split()
    ip = f[f.index("inet") + 1].split("/")[0] if "inet" in f else None
    ssid = next((l.split(":", 1)[1].strip() for l in run("iw", "dev", dev, "link").splitlines()
                 if l.strip().startswith("SSID:")), None)
    return ip, ssid


class QrView(QtWidgets.QWidget):
    """QR code, black on white with the 4-module quiet zone, whole-pixel modules (crisp for phone cameras)"""
    def __init__(self):
        super().__init__(); self.qr = None
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def set_text(self, text):
        import qrcodegen
        self.qr = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM) if text else None
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        if self.qr is None:
            return
        n = self.qr.get_size(); m = min(self.width(), self.height()) // (n + 8)
        side = m * (n + 8); x0 = (self.width() - side) // 2; y0 = (self.height() - side) // 2
        p.fillRect(x0, y0, side, side, QtGui.QColor("#FFFFFF"))
        black = QtGui.QColor("#000000")
        for y in range(n):
            for x in range(n):
                if self.qr.get_module(x, y):
                    p.fillRect(x0 + (x + 4) * m, y0 + (y + 4) * m, m, m, black)


class PhonePage(QtWidgets.QWidget):
    """address + QR code of the phone control page (web_server.py on this board, over the board's WiFi): the hotspot
    decides the address, so the phone gets it from the screen"""
    def __init__(self, fam, compact, port):
        super().__init__(); self.port = port; self.key = ()
        h = QtWidgets.QHBoxLayout(self); h.setContentsMargins(24, 12, 24, 12); h.setSpacing(28)
        self.code = QrView(); h.addWidget(self.code, 5)
        col = QtWidgets.QVBoxLayout(); col.setSpacing(14); h.addLayout(col, 6)
        hdr = QtWidgets.QLabel("手机控制"); hdr.setObjectName("hdr"); col.addWidget(hdr, 0)
        col.addStretch(1)
        self.addr = QtWidgets.QLabel(); self.addr.setWordWrap(True)
        f = self.addr.font(); f.setFamily(mono_family()); f.setPointSize(20 if compact else 26); f.setBold(True)
        self.addr.setFont(f); self.addr.setStyleSheet(f"color: {C_CLAY};"); col.addWidget(self.addr, 0)
        self.ssid = QtWidgets.QLabel(); col.addWidget(self.ssid, 0)
        self.hint = QtWidgets.QLabel(); self.hint.setWordWrap(True); self.hint.setStyleSheet(f"color: {C_MUTED};")
        col.addWidget(self.hint, 0)
        col.addStretch(2)
        for w in (self.ssid, self.hint):
            f = w.font(); f.setFamily(fam); f.setPointSize(13 if compact else 15); w.setFont(f)
        self.tm = QtCore.QTimer(self); self.tm.timeout.connect(self.refresh); self.tm.start(5000)
        self.refresh()

    def refresh(self):
        ip, ssid = wifi_link()
        if (ip, ssid) == self.key:
            return
        self.key = (ip, ssid)
        url = f"http://{ip}:{self.port}" if ip else None
        self.code.set_text(url)
        self.addr.setText(url or "WiFi 未连接")
        self.ssid.setText(f"WiFi 热点：{ssid}" if ssid else "")
        self.hint.setText("手机连上同一个热点后扫码，或在浏览器中输入上面的地址：两路实时画面、物理层指标，"
                          "以及 TX 衰减、模式和信源控制。" if url else
                          "板子开机后会自动连接已配置的热点（src/sw/board_setup/wifi），连上后这里显示地址和二维码。")


class Gui(QtWidgets.QMainWindow):
    T_WIN, T_SPAN, Z_SCALE, XE = 5.0, 50.0, 5.0, 28.0
    STAGES = [("pre", "Pre-EQ (FFT out)"), ("ce", "After CE"), ("sfo", "After SFO rotation"), ("cpe", "After CPE (final)")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepJSCC-Q over OFDM  ·  live link")
        pg.setConfigOptions(antialias=not args.lite, background=C_PLOT, foreground=C_INK, imageAxisOrder="row-major")
        fam = serif_family()
        self.tabs = QtWidgets.QTabWidget(); self.setCentralWidget(self.tabs)

        # ======================= page 1: image link =======================
        global COMPACT
        scr = QtWidgets.QApplication.primaryScreen().size()
        COMPACT = self.compact = args.lite and not args.no_compact and (args.compact or scr.height() < 800)
        p1 = QtWidgets.QWidget(); self.tabs.addTab(p1, "Image link")
        if self.compact:                     # small screen: images left, KPI column + mode buttons right, slider row below
            l1v = QtWidgets.QVBoxLayout(p1); l1v.setContentsMargins(6, 4, 6, 4); l1v.setSpacing(6)
            l1top = QtWidgets.QHBoxLayout(); l1top.setSpacing(8); l1v.addLayout(l1top, 1)
            l1 = QtWidgets.QHBoxLayout(); l1top.addLayout(l1, 1)
            rcol_w = QtWidgets.QWidget(); rcol_w.setFixedWidth(232); l1top.addWidget(rcol_w, 0)
            rcol = QtWidgets.QVBoxLayout(rcol_w); rcol.setContentsMargins(0, 0, 0, 0); rcol.setSpacing(6)
        else:
            l1 = QtWidgets.QHBoxLayout(p1)
        self.im, self.im_title = {}, {}      # ImgView (integer zoom), much cheaper than a pyqtgraph ImageItem
        self.tx_widgets = []
        for key, title in [("tx", "TX camera" if self.compact else "TX camera → encoder"),
                           ("rx", "RX decoded" if self.compact else "RX decoder output")]:
            col = QtWidgets.QVBoxLayout(); l1.addLayout(col, 2)
            self.im_title[key] = QtWidgets.QLabel(title); self.im_title[key].setObjectName("hdr"); col.addWidget(self.im_title[key])
            self.im_title[key].setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)  # text change: no re-layout
            self.im[key] = ImgView(); col.addWidget(self.im[key], 1)
            if key == "tx":
                self.tx_widgets = [self.im_title[key], self.im[key]]
        self.tx_link = not args.lite         # board display: TX column only while TX previews arrive (boards cabled)
        for w in self.tx_widgets:
            w.setVisible(self.tx_link)
        side = QtWidgets.QVBoxLayout()
        if not self.compact:
            l1.addLayout(side, 2)
        if not args.lite:
            self.ps_plot = line_plot("PSNR", "dB"); side.addWidget(self.ps_plot, 1)
            self.ps_curve = self.ps_plot.plot([], [], pen=pg.mkPen(C_CLAY, width=2))
            self.lat_plot = line_plot("E2E latency (TX source → RX)", "ms"); side.addWidget(self.lat_plot, 1)
            self.lat_curve = self.lat_plot.plot([], [], pen=pg.mkPen(C_BLUE, width=2))
        self.card1 = QtWidgets.QLabel()
        self.att_bars = [AttenBar(self, (0, 10, 20, 30) if self.compact else (), sources=True, side=self.compact)]
        if self.compact:
            rcol.addWidget(self.card1, 1); rcol.addWidget(self.att_bars[0].top_frame, 0)
            l1v.addWidget(self.att_bars[0], 0)
        else:
            side.addWidget(self.card1, 1); side.addWidget(self.att_bars[0], 0)

        # ======================= page 2: PHY detail =======================
        p2 = QtWidgets.QWidget(); self.tabs.addTab(p2, "PHY detail")
        if self.compact:                     # small screen: row A = 2 constellations + 3-D |H|, row B = SNR + card
            l2 = QtWidgets.QVBoxLayout(p2); l2.setContentsMargins(6, 4, 6, 4); l2.setSpacing(6)
            rowA, rowB = QtWidgets.QHBoxLayout(), QtWidgets.QHBoxLayout()
            l2.addLayout(rowA, 3); l2.addLayout(rowB, 2)
            cgrid = rowA
        else:
            l2 = QtWidgets.QHBoxLayout(p2)
            cgrid = QtWidgets.QGridLayout(); l2.addLayout(cgrid, 1)
        self.const = {}
        stages = [st_ for st_ in self.STAGES if st_[0] in ("ce", "cpe")] if self.compact else self.STAGES
        for n, (key, title) in enumerate(stages):
            if self.compact:                 # small screen: after CE and final only, stacked
                title = {"ce": "After CE", "cpe": "Final"}[key]
            p, d, pl = const_plot(title)
            if self.compact:
                cgrid.addWidget(p, 5)
            else:
                cgrid.addWidget(p, n // 2, n % 2)
            self.const[key] = (p, d, pl, title)
        r2 = QtWidgets.QVBoxLayout()
        if self.compact:
            rowA.addLayout(r2, 6)
        else:
            l2.addLayout(r2, 1)
        self.h_title = QtWidgets.QLabel("Channel |H| from LTF"); r2.addWidget(self.h_title)
        self.surf, self.labels, self.h_hist, self.h_ref = None, [], [], None
        self.cmap = pg.ColorMap([0.0, 0.5, 1.0], TH["CMAP"])
        self.h3d = None
        if args.lite and not args.no_gpu3d and lima_available():      # board + Mali-400: GLES2 off-screen renderer
            self.h3d_view = H3DView(); r2.addWidget(self.h3d_view, 3)
            self.h3d = H3DWorker(); self.h3d.done.connect(self.h3d_view.show_image); self.h3d.start()
        elif args.lite:                      # board without GPU driver: 2-D waterfall
            self.wf_plot = line_plot("time", "s", "subcarrier", None); r2.addWidget(self.wf_plot, 3)
            self.wf_img = pg.ImageItem(); self.wf_plot.addItem(self.wf_img)
            self.wf_img.setLookupTable(self.cmap.getLookupTable(nPts=256))
            self.wf_plot.setXRange(-26.5, 26.5, padding=0); self.wf_plot.setYRange(-self.T_WIN, 0, padding=0)
        else:
            import pyqtgraph.opengl as gl
            globals()["gl"] = gl
            self.gv = gl.GLViewWidget(); self.gv.setBackgroundColor(C_PLOT); r2.addWidget(self.gv, 3)
            self.gv.setCameraPosition(pos=QtGui.QVector3D(0, -25, 0), distance=140, elevation=26, azimuth=-55)
        row = rowB if self.compact else QtWidgets.QHBoxLayout()
        if not self.compact:
            r2.addLayout(row, 2)
        self.csi_plot = line_plot("|H|", "dB", "subcarrier", None)
        if not self.compact:                 # small screen: no CSI-now plot (the 3-D view shows |H|)
            row.addWidget(self.csi_plot, 1)
        self.csi_plot.setTitle(f"CSI now: |H| dB ({TH['NAMES'][0]}), phase residual ({TH['NAMES'][1]})", color=C_INK, size="11pt")
        self.csi_mag = self.csi_plot.plot([], [], pen=pg.mkPen(C_CLAY, width=2),
                                        **({} if args.lite else dict(symbol="o", symbolSize=4, symbolBrush=C_CLAY)))
        self.csi_ph_vb = pg.ViewBox(); self.csi_plot.showAxis("right"); self.csi_plot.scene().addItem(self.csi_ph_vb)
        self.csi_plot.getAxis("right").linkToView(self.csi_ph_vb); self.csi_ph_vb.setXLink(self.csi_plot)
        self.csi_plot.getAxis("right").setLabel("phase residual", units="deg")
        self.csi_ph = pg.PlotCurveItem(pen=pg.mkPen(C_BLUE, width=2)); self.csi_ph_vb.addItem(self.csi_ph)
        self.csi_plot.getViewBox().sigResized.connect(lambda: self.csi_ph_vb.setGeometry(self.csi_plot.getViewBox().sceneBoundingRect()))
        self.snr_plot = line_plot("SNR", "dB", "subcarrier", None); row.addWidget(self.snr_plot, 3 if self.compact else 1)
        self.snr_plot.setTitle("SNR per subcarrier (2×LTF)" if self.compact else "Per-subcarrier SNR (2×LTF, last 16 snapshots)",
                               color=C_INK, size="9pt" if self.compact else "11pt")
        self.snr_bar = pg.BarGraphItem(x=[], height=[], width=0.8, brush=pg.mkBrush(*TH["DATA"], 200)); self.snr_plot.addItem(self.snr_bar)
        self.spec_plot = line_plot("PSD", "dBFS", "frequency", "MHz"); row.addWidget(self.spec_plot, 3 if self.compact else 1)
        self.spec_plot.setTitle("RX spectrum (ADC)", color=C_INK, size="9pt" if self.compact else "11pt")
        self.spec_plot.setXRange(-10, 10, padding=0)
        self.spec_plot.getAxis("bottom").setTicks([[(v, str(v)) for v in (-8, -4, 0, 4, 8)]])   # +-10 labels get clipped
        self.spec_noi = self.spec_plot.plot([], [], pen=pg.mkPen(C_MUTED, width=1))          # before the frame: noise floor
        self.spec_sig = self.spec_plot.plot([], [], pen=pg.mkPen(C_CLAY, width=2 if not args.lite else 1.5),
                                            fillLevel=-120, brush=pg.mkBrush(*TH["DATA"], 40))
        row2 = rowB if self.compact else QtWidgets.QHBoxLayout()
        if not self.compact:
            r2.addLayout(row2, 2)
        self.evm_plot = line_plot("EVM", "dB")
        if not self.compact:                 # small screen: no EVM history plot
            row2.addWidget(self.evm_plot, 1)
        self.evm_plot.addLegend(offset=(-10, -10))
        self.evm_curves = {k: self.evm_plot.plot([], [], pen=pg.mkPen(c, width=2), name=n)
                           for k, c, n in [("ce", C_MUTED, "after CE"), ("cpe", C_CLAY, "final")]}
        self.card2 = QtWidgets.QLabel()
        c2col = QtWidgets.QVBoxLayout(); c2col.setSpacing(6); row2.addLayout(c2col, 2 if self.compact else 1)
        c2col.addWidget(self.card2, 1)
        self.att_bars.append(AttenBar(self, (), two_rows=self.compact)); c2col.addWidget(self.att_bars[1], 0)
        self.att_extra, self.att_rep, self.t_att = 0.0, None, 0.0       # requested extra dB, reported total dB, time
        self.att_disp = 0.0                                              # extra dB shown (animated)
        self.att_timer = QtCore.QTimer(); self.att_timer.timeout.connect(self.anim_atten); self.att_timer.start(40)
        for b in self.att_bars:
            b.setVisible(False)
        # board touch screen: press and hold on - / +, drag on the slider (X only sees taps, see TouchHold)
        self.hold = self.drag = self.drag_v = None; self.t_drag_tx = 0.0
        self.hold_timer = QtCore.QTimer(); self.hold_timer.timeout.connect(self.hold_step)
        tdev = TouchHold.find(args.touch_dev) if args.lite else None
        if tdev:
            self.touch = TouchHold(tdev, QtWidgets.QApplication.primaryScreen().geometry().size())
            self.touch.down.connect(self.touch_down); self.touch.move.connect(self.touch_move)
            self.touch.up.connect(self.touch_up); self.touch.start()

        # ======================= page 3: style presets + logos =======================
        p3 = QtWidgets.QWidget(); self.tabs.addTab(p3, "风格" if self.compact else "Style")
        l3 = QtWidgets.QVBoxLayout(p3); l3.setContentsMargins(8, 6, 8, 4); l3.setSpacing(8)
        l3.addWidget(LogoBar(fam), 0)
        tg = QtWidgets.QGridLayout(); tg.setSpacing(10); l3.addLayout(tg, 1)
        for n, key in enumerate(THEMES):
            c = ThemeCard(key, fam); c.picked.connect(self.switch_theme); tg.addWidget(c, n // 3, n % 3)
        self.style_hint = QtWidgets.QLabel("点击卡片切换界面风格（界面重启几秒，链路不受影响；选择会被记住）")
        self.style_hint.setStyleSheet(f"color: {C_MUTED};"); l3.addWidget(self.style_hint, 0)

        # ======================= page 4: about (structure diagram + topics) =======================
        self.tabs.addTab(AboutPage(fam, self.compact), "介绍" if self.compact else "About")
        if args.lite:                        # page 5: phone control page address (board WiFi)
            self.tabs.addTab(PhonePage(fam, self.compact, args.web_port), "手机")

        global MONO
        MONO = mono_family()
        for c in (self.card1, self.card2):
            f = c.font(); f.setFamily(fam); f.setPointSize(10 if self.compact else 14); c.setFont(f)
            c.setTextFormat(QtCore.Qt.TextFormat.RichText); c.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop); c.setObjectName("card")
        if self.compact:                     # small screen: PHY readout centred in its card, slightly larger
            f = self.card2.font(); f.setPointSize(12); self.card2.setFont(f)
            self.card2.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.card1.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)    # KPI column
        self.h_title.setObjectName("hdr")
        tab_css = "padding: 10px 14px; min-width: 88px; font-size: 14pt;" if self.compact else "padding: 8px 22px; min-width: 150px; font-size: 13pt;"
        # attenuation bar: finger-sized on the touch screen (>= ~46 px targets)
        btn_h, btn_w, pm_w, pm_pt, pre_w, mode_w, small_pt = ((46, 46, 66, 22, 60, 78, 13) if self.compact else
                                                              (30, 34, 34, 13, 40, 54, 11))
        hdr_pt, hdr_pad, card_pad = (11, "3px 8px", "8px 10px") if self.compact else (13, "6px 12px", "14px 18px")
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {C_BG}; color: {C_INK}; }}
            QTabWidget::pane {{ border: none; }}
            QTabBar {{ font-family: '{fam}'; font-size: {14 if self.compact else 13}pt; }}  /* tab widths are measured with this font */
            QTabBar::tab {{ background: {TH["TAB"]}; color: {C_MUTED}; {tab_css} margin-right: 4px; border-radius: 6px;
                            border: 1px solid {C_LINE}; font-family: '{fam}'; }}
            QTabBar::tab:selected {{ background: {TH["TAB_SEL"]}; color: {TH["TAB_SEL_FG"]}; border: 1px solid {C_CLAY}; }}
            QLabel#hdr {{ background: {TH["HDR_BG"]}; color: {TH["HDR_FG"]}; font-family: '{fam}'; font-size: {hdr_pt}pt;
                         font-weight: bold; padding: {hdr_pad}; border-radius: 6px; border-left: 3px solid {C_CLAY}; }}
            QLabel#card {{ background: {C_CARD}; border: 1px solid {TH["CARD_BORDER"]}; border-radius: 14px; padding: {card_pad}; }}
            QStatusBar {{ color: {C_MUTED}; }}
            QFrame#attbar {{ background: {C_CARD}; border: 1px solid {TH["CARD_BORDER"]}; border-radius: 10px; }}
            QFrame#attbar QLabel {{ background: transparent; }}
            QLabel#attlab {{ color: {C_MUTED}; font-size: {small_pt}pt; }}
            QLabel#attval {{ color: {C_CLAY}; font-size: {16 if self.compact else 14}pt; font-weight: bold; font-family: '{mono_family()}'; }}
            QFrame#attbar QPushButton {{ background: {TH["TAB"]}; color: {C_INK}; border: 1px solid {C_LINE}; border-radius: 8px;
                                       min-width: {btn_w}px; min-height: {btn_h}px; font-size: 13pt; }}
            QFrame#attbar QPushButton:pressed {{ background: {TH["TAB_SEL"]}; color: {TH["TAB_SEL_FG"]}; border-color: {C_CLAY}; }}
            QFrame#attbar QPushButton#attpm {{ min-width: {pm_w}px; font-size: {pm_pt}pt; font-weight: bold; }}
            QPushButton#attpre {{ min-width: {pre_w}px; font-size: {small_pt}pt; }}
            QFrame#attbar QPushButton#attmode {{ min-width: {mode_w}px; font-size: {small_pt}pt; font-weight: bold; color: {C_MUTED}; }}
            QPushButton#aboutbtn {{ background: {TH["TAB"]}; color: {C_INK}; border: 1px solid {C_LINE}; border-radius: 8px;
                                    min-width: {116 if self.compact else 150}px; min-height: {btn_h}px;
                                    font-size: {13 if self.compact else 14}pt; font-family: '{fam}'; padding: 0 10px; }}
            QPushButton#aboutbtn:checked {{ background: {C_CLAY}; color: {C_BG}; border-color: {C_CLAY}; }}
            QFrame#attbar QPushButton#attmode:checked {{ background: {C_CLAY}; color: {C_BG}; border-color: {C_CLAY}; }}
            QSlider::groove:horizontal {{ height: 8px; background: {C_LINE}; border-radius: 4px; }}
            QSlider::sub-page:horizontal {{ background: {C_CLAY}; border-radius: 4px; }}
            QSlider::handle:horizontal {{ background: {C_INK}; border: 2px solid {C_CLAY}; width: 26px; margin: -10px 0;
                                        border-radius: 13px; }}
        """)
        self.status = self.statusBar()
        if self.compact:                             # small screen: the room goes to the finger-sized controls
            self.status.hide()
        self.clock = QtWidgets.QLabel()                  # top right, next to the tabs
        f = self.clock.font(); f.setFamily(mono_family()); f.setPointSize(13 if self.compact else 15); f.setBold(True)
        self.clock.setFont(f); self.clock.setStyleSheet(f"color: {C_CLAY}; padding: 0 10px; background: transparent;")
        self.tabs.setCornerWidget(self.clock, QtCore.Qt.Corner.TopRightCorner)
        self.tick_clock()
        self.clk_timer = QtCore.QTimer(); self.clk_timer.timeout.connect(self.tick_clock); self.clk_timer.start(1000)

        # ======================= state =======================
        self.tx_buf = collections.deque(maxlen=30); self.t_tx = 0.0
        self.rate = {k: collections.deque(maxlen=60) for k in ("tx", "rx", "tel", "sscc")}
        self.mode_tx, self.t_sscc_ok = "jscc", 0.0       # mode reported by the TX board; last SSCC packet with a good CRC
        self.src_tx = "camera"                           # TX input: camera / demo video
        self.sscc_hist = collections.deque(maxlen=30)    # CRC ok of the last packets
        self.sscc_q, self.sscc_status = 0, ""
        self.sscc_win = dict(fail=0, crc=0)              # SSCC frames since the last card refresh (present)
        self.ps_win = []                                 # PSNR of the frames shown since the last card refresh
        self.t0 = time.time(); self.last_tel = None; self.st_hist = collections.deque(); self.rates = {}
        self.ps_hist = collections.deque(maxlen=900); self.lat_hist = collections.deque(maxlen=900)
        self.evm_hist = {k: collections.deque(maxlen=600) for k in ("ce", "cpe")}
        self.psnr_now = self.lat_now = self.evm_now = self.snr_now = self.snr_dd = float("nan"); self.ltf_n = 0
        self.ltf_sig, self.ltf_noi = collections.deque(maxlen=16), collections.deque(maxlen=16)   # last 16 snapshots
        self.n_ltf = 1
        self.bn = collections.Counter()
        self.rx_thread = Receiver()
        self.rx_thread.tx_img.connect(self.on_tx); self.rx_thread.rx_img.connect(self.on_rx); self.rx_thread.tel.connect(self.on_tel)
        self.rx_thread.atten.connect(self.on_atten)
        self.rx_thread.sscc.connect(self.on_sscc)
        self.rx_thread.start()
        if args.lite:
            self.tabs.currentChanged.connect(lambda i: setattr(self.rx_thread.want_rx, "value", int(i == 0)))
        self.timer = QtCore.QTimer(); self.timer.timeout.connect(self.refresh_cards); self.timer.start(500)
        self.ripple = Ripple(self); self.ripple.hide()
        self.remote = None
        if args.remote_port:
            try:
                self.remote = Remote(args.remote_port); self.remote.cmd.connect(self.on_remote); self.remote.start()
            except OSError as e:
                print(f"remote control off: {e}", file=sys.stderr)
        if not args.lite:                    # PSNR / latency curves: 5 Hz (redrawing them per frame cost ~60 % of a core)
            self.ctimer = QtCore.QTimer(); self.ctimer.timeout.connect(self.refresh_curves); self.ctimer.start(200)

    def refresh_curves(self):
        if self.tabs.currentIndex() != 0 or not self.ps_hist:
            return
        x, y = zip(*self.ps_hist); self.ps_curve.setData(np.array(x), np.array(y))
        x, y = zip(*self.lat_hist); self.lat_curve.setData(np.array(x), np.array(y))
        self.im_title["rx"].setText(f"RX decoder output  ·  PSNR {self.psnr_now:.2f} dB")

    # ---------------- images (page 1)
    def on_tx(self, fid, img, t):
        self.rate["tx"].append(t); self.t_tx = t
        if self.tabs.currentIndex() == 0 and self.tx_link:
            self.im["tx"].set_rgb(img)
        self.tx_buf.append((t, fid, img, img[::4, ::4].astype(np.float32)))   # full frame kept as uint8

    def on_rx(self, fid, img, t):
        self.rate["rx"].append(t)
        if fid != self.rx_thread.n_rx or self.tabs.currentIndex() != 0 or self.cur_mode() == "sscc":
            return
        self.show_rx(img, t)                          # PSNR shown = mean of the frames since the last refresh

    def cur_mode(self):
        """mode of the TX board when it answers (boards cabled), else: SSCC while good SSCC packets arrive"""
        if time.time() - self.t_att < 3.0:
            return self.mode_tx
        return "sscc" if time.time() - self.t_sscc_ok < 1.5 else "jscc"

    def on_sscc(self, n, pkt, t):
        p = sscc_pkt.unpack(pkt)
        self.sscc_hist.append(p["ok"])
        if p["ok"]:
            self.t_sscc_ok = t; self.sscc_q = p["quality"]
        if self.cur_mode() != "sscc":
            return
        self.rate["sscc"].append(t)
        if n != self.rx_thread.n_sscc or self.tabs.currentIndex() != 0:
            return
        img = None
        if p["jpeg"]:                                  # with bit errors: show whatever the JPEG decoder makes of it
            if cv2 is not None:
                a = cv2.imdecode(np.frombuffer(p["jpeg"], np.uint8), cv2.IMREAD_COLOR)
                if a is not None and a.shape == (256, 256, 3):
                    img = np.ascontiguousarray(a[:, :, ::-1])
            else:
                q = QtGui.QImage.fromData(p["jpeg"], "JPG")
                if not q.isNull() and q.width() == 256 and q.height() == 256:
                    q = q.convertToFormat(QtGui.QImage.Format.Format_RGB888)
                    img = np.array(q.constBits().asarray(256 * 256 * 3) if hasattr(q.constBits(), "asarray")
                                   else q.constBits(), np.uint8).reshape(256, 256, 3).copy()
        # every frame is shown as it comes; a frame the JPEG decoder rejects is shown (and scored) as a mid-gray
        # image, never the last good one. The 2 Hz card refresh shows the status of the frames since the last refresh
        # (decode failure > CRC error > good) and, as for JSCC, their mean PSNR
        w = self.sscc_win
        if img is None:
            w["fail"] += 1
            self.show_rx(SSCC_FAIL_IMG, t, latency=False)
            return
        w["crc"] += not p["ok"]
        self.show_rx(img, t, frame=p["frame"] if p["ok"] else None, latency=bool(p["ok"]))

    def present(self):
        """2 Hz: PSNR = mean over the frames shown since the last refresh (JSCC and SSCC alike), SSCC status"""
        if self.ps_win:
            self.psnr_now = float(np.mean(self.ps_win))
        w = self.sscc_win
        self.sscc_status = "解码失败" if w["fail"] else ("CRC 错误" if w["crc"] else "")
        self.ps_win = []
        self.sscc_win = dict(fail=0, crc=0)

    def show_rx(self, img, t, frame=None, latency=True):
        """draw; PSNR of every frame against the TX preview of the same frame (SSCC: frame number) or the best
        matching one (DeepJSCC: no frame number); end-to-end latency = arrival here - TX source time of that frame
        (TXTS, mapped to this board's clock by the JSCC-TIME offset), only when the pairing is unambiguous"""
        self.bn["rx_drawn"] += 1
        self.im["rx"].set_rgb(img)
        if not self.tx_buf or t - self.t_tx > 2.0:
            return None
        self.bn["rx_psnr"] += 1
        # candidates: TX previews of the last 350 ms (latency ~40 ms), not all 30 -> PSNR of every frame is cheap
        amb = False
        e = next((x for x in reversed(self.tx_buf) if x[1] & 0xFFFF == frame), None) if frame is not None else None
        if e is None:
            cand = [x for x in self.tx_buf if -0.05 < t - x[0] < 0.35] or list(self.tx_buf)
            rc = img[::4, ::4].astype(np.float32)
            mse = sorted((float(np.mean((rc - x[3]) ** 2)), k) for k, x in enumerate(cand))
            e = cand[mse[0][1]]
            amb = frame is not None or (len(mse) > 1 and mse[0][0] >= 0.5 * mse[1][0])   # repeated / still content
        _, fid, ref, _ = e
        if cv2 is not None:
            ps = float(cv2.PSNR(img, ref))               # 10 log10(255^2 / MSE), C speed
        else:
            ps = 10 * np.log10(255 ** 2 / max(float(np.mean((img.astype(np.float32) - ref) ** 2)), 1e-6))
        self.ps_win.append(ps)
        self.ps_hist.append((t - self.t0, ps))
        rt = self.rx_thread
        ts = rt.tsmap.get(fid)
        if latency and not amb and ts is not None and rt.clk_off is not None:
            self.lat_now = (t - (ts - rt.clk_off)) * 1e3
            self.lat_hist.append((t - self.t0, self.lat_now))

    # ---------------- telemetry (page 2)
    def on_tel(self, meta, arrays, t):
        self.rate["tel"].append(t)
        st = meta.get("status", {})
        self.st_hist.append((t, st))
        while len(self.st_hist) > 2 and t - self.st_hist[0][0] > 2.0:
            self.st_hist.popleft()
        if len(self.st_hist) >= 2:
            (ta, sa), (tb, sb) = self.st_hist[0], self.st_hist[-1]
            dt = max(1e-3, tb - ta)
            self.rates = {k: (sb.get(k, 0) - sa.get(k, 0)) / dt for k in ("SYNC_CNT", "PHY_FRAMES", "IMG_FRAMES", "FB_DROP")}
        self.last_tel = meta
        if meta.get("_n") != self.rx_thread.n_tel:
            return
        self.n_ltf = 0
        if arrays.get("pre") is not None:
            P = np.asarray(arrays["pre"]).reshape(-1, 64)
            self.n_ltf = len(P) - 20
            if self.n_ltf == 2:
                self.add_ltf(P[0], P[1])
        if self.tabs.currentIndex() != 1:
            if "cpe" in arrays and t - getattr(self, "_t_evm", 0) > 1.0:      # image page card: final EVM at 1 Hz
                self._t_evm = t
                d = np.asarray(arrays["cpe"]).reshape(-1, 64)[:, DATA].ravel()
                self.evm_now = evm_db(d[:-PAD] if meta.get("start_sym", 0) + 19 == NSYM - 1 else d)
            return
        self.bn["tel_drawn"] += 1
        tail = meta.get("start_sym", 0) + 20 - 1 == NSYM - 1
        now = time.time()
        if args.lite and now - getattr(self, "_t_draw", 0) < 0.2:          # board: PHY page at most 5 updates / s
            return
        self._t_draw = now
        self._k = getattr(self, "_k", 0) + 1
        slow = not args.lite or self._k % 2 == 0          # board: constellations 5 Hz, CSI / SNR / EVM history 2.5 Hz
        if slow and arrays.get("pre") is not None:     # channel estimate: mean of LTF1 / LTF2 (rows 0 / 1 of "pre")
            P = np.asarray(arrays["pre"]).reshape(-1, 64)
            H = (P[:self.n_ltf].mean(0) * LTF_SIGN)[USED]
            hdb = 20 * np.log10(np.maximum(abs(H), 1e-3))
            self.push_h(t, hdb)
            if not self.compact:
                k = USED - 32
                ph = np.unwrap(np.angle(H)); ph_res = np.degrees(ph - np.polyval(np.polyfit(k, ph, 1), k))
                self.csi_mag.setData(k.astype(float), hdb - (self.h_ref or 0)); self.csi_ph.setData(k.astype(float), ph_res)
        if slow and meta.get("spec"):
            self.draw_spec(meta["spec"])
        for key, (p, dp, pl, title) in self.const.items():
            S = arrays.get(key)
            if S is None:
                continue
            S = np.asarray(S).reshape(-1, 64)
            if key == "pre":
                S = S[self.n_ltf:]
            d = S[:, DATA].ravel(); pp = S[:, PILOTS].ravel()
            if tail:
                d = d[:-PAD]
            if key == "pre":
                lim = 1.1 * max(1.0, float(np.max(np.abs(np.r_[d.real, d.imag, pp.real, pp.imag]))))
                p.setXRange(-lim, lim, padding=0); p.setYRange(-lim, lim, padding=0)
                p.setTitle(title, color=C_INK, size="10pt" if self.compact else "12pt")
            else:
                e = evm_db(d)
                p.setTitle(f"{title}  ·  EVM {e:.1f} dB", color=C_INK, size="10pt" if self.compact else "12pt")
                if key in self.evm_hist:
                    self.evm_hist[key].append((t - self.t0, e))
                    if slow and not self.compact:
                        x, y = zip(*self.evm_hist[key]); self.evm_curves[key].setData(np.array(x), np.array(y))
                if key == "cpe" and slow:
                    self.evm_now = e
                    Z = S[:, DATA]
                    if tail:
                        Z = Z[:-1]                                       # last OFDM symbol is partly zero padding
                    ref = QAM_LV[np.argmin(abs(Z.real[..., None] - QAM_LV), -1)] + 1j * QAM_LV[np.argmin(abs(Z.imag[..., None] - QAM_LV), -1)]
                    self.snr_dd = 10 * np.log10(np.mean(abs(ref) ** 2) / max(float(np.mean(abs(Z - ref) ** 2)), 1e-9))
                    lt = self.ltf_snr()
                    if lt is not None:                   # two-LTF estimate (PL telemetry layout 3)
                        sc, self.snr_now, self.ltf_n = lt
                        self.snr_bar.setOpts(x=USED - 32, height=sc)
                    else:                                # older bitstream: decision directed on the final symbols
                        snr_sc = 10 * np.log10(np.mean(abs(ref) ** 2, 0) / np.maximum(np.mean(abs(Z - ref) ** 2, 0), 1e-9))
                        self.snr_now, self.ltf_n = self.snr_dd, 0
                        self.snr_bar.setOpts(x=np.array(DATA) - 32, height=snr_sc)
            if pl is None:
                dp.draw(d, pp, p.getViewBox().viewRange()[0][1])
            else:
                dp.setData(d.real, d.imag); pl.setData(pp.real, pp.imag)

    def add_ltf(self, y1, y2):
        """LTF1 / LTF2 FFT outputs (fftshift order): noise |Y1 - Y2|^2 / 2, signal |(Y1 + Y2) / 2|^2 - noise / 2"""
        y2 = y2 * np.exp(-1j * np.angle(np.vdot(y1[USED], y2[USED])))      # residual common phase (CFO) between them
        n = abs((y1 - y2)[USED]) ** 2 / 2
        self.ltf_noi.append(n); self.ltf_sig.append(abs(((y1 + y2) / 2)[USED]) ** 2 - n / 2)

    def ltf_snr(self):
        """(per used subcarrier dB, overall dB, snapshots) averaged over the last 16 snapshots, or None"""
        if not self.ltf_sig:
            return None
        sg, nz = np.mean(self.ltf_sig, 0), np.maximum(np.mean(self.ltf_noi, 0), 1e-9)
        return 10 * np.log10(np.maximum(sg, 1e-9) / nz), 10 * np.log10(max(float(sg.sum()), 1e-9) / float(nz.sum())), len(self.ltf_sig)

    def draw_spec(self, sp):
        f = np.asarray(sp["f"], float)
        self.spec_sig.setData(f, np.asarray(sp["sig"], float)); self.spec_noi.setData(f, np.asarray(sp["noi"], float))
        lo = float(np.min(sp["noi"])) - 5; hi = float(np.max(sp["sig"])) + 5
        self.spec_plot.setYRange(lo, hi, padding=0); self.spec_sig.setFillLevel(lo)

    def fps(self, key):
        q = self.rate[key]
        if q and time.time() - q[-1] > 1.5:          # nothing arrives any more (e.g. sync lost): not the last rate
            return 0.0
        return (len(q) - 1) / (q[-1] - q[0]) if len(q) > 2 and q[-1] > q[0] else 0.0

    def set_atten(self, extra):
        """touch: TX attenuation = config value + extra dB (sent to the TX board, which answers with ATTS)"""
        self.att_extra = float(min(max(extra, 0), AttenBar.MAX))
        self.rx_thread.set_atten(TX_ATT0 + self.att_extra)

    # ---- board touch screen (TouchHold): the tap itself still arrives through X as a normal click
    def _touched(self, gx, gy):
        """(bar, widget) under a global point among the visible attenuation bars"""
        w = QtWidgets.QApplication.widgetAt(QtCore.QPoint(gx, gy))
        for bar in self.att_bars:
            if bar.isVisible() and w is not None and (w is bar or bar.isAncestorOf(w)):
                for c in (bar.minus, bar.plus, bar.sl):
                    if w is c or c.isAncestorOf(w):
                        return bar, c
        return None, None

    def touch_down(self, gx, gy):
        bar, c = self._touched(gx, gy)
        if c is None:
            return
        if c is bar.sl:
            self.drag, self.drag_v = bar, None; self.touch_move(gx, gy)
        else:                                        # - / +: the X tap already stepped 1 dB; repeat after 400 ms
            self.hold = (bar, c, -1 if c is bar.minus else 1)
            self.hold_timer.start(400)

    def hold_step(self):
        if self.hold is None:
            self.hold_timer.stop(); return
        self.hold_timer.start(100)                   # 1 dB / 100 ms = the TX up ramp (10 dB/s)
        self.set_atten(round(self.att_extra) + self.hold[2])

    def touch_move(self, gx, gy):
        if self.hold is not None:
            bar, c, _ = self.hold
            if self._touched(gx, gy)[1] is not c:    # slid off the button: stop
                self.touch_up()
        elif self.drag is not None:
            sl = self.drag.sl
            p = sl.mapFromGlobal(QtCore.QPoint(gx, gy))
            hw = self.drag.hw                                                  # handle width
            f = min(max((p.x() - hw / 2) / max(sl.width() - hw, 1), 0.0), 1.0)
            self.drag_v = v = round(f * self.drag.MAX)
            now = time.time()
            if v != round(self.att_extra) and now - self.t_drag_tx >= 0.1:   # follow the finger, <= 10 updates/s;
                self.t_drag_tx = now; self.set_atten(v)    # the handle shows the real attenuation (anim_atten: the TX
                                                           # ramps increases at 10 dB/s), not the finger

    def touch_up(self):
        self.hold = None; self.hold_timer.stop()
        if self.drag is not None:
            self.drag = None
            if self.drag_v is not None:
                self.set_atten(self.drag_v)
            for b in self.att_bars:                  # undo a page step of the X tap on the groove
                b.show_value(self.att_disp)

    UP_DBS, DOWN_DBS = 10.0, 40.0                   # TX ramps up at 10 dB/s (tx_camera --att-up-dbs), steps down at once

    def anim_atten(self):
        """slider animation: towards the target at the TX ramp rate (up) / quickly (down)"""
        if not self.att_bars[0].isVisible() and not self.att_bars[1].isVisible():
            return
        d = self.att_extra - self.att_disp
        if abs(d) < 1e-3:
            return
        step = (self.UP_DBS if d > 0 else self.DOWN_DBS) * 0.04
        self.att_disp = self.att_extra if abs(d) <= step else self.att_disp + (step if d > 0 else -step)
        for b in self.att_bars:
            b.show_value(self.att_disp)

    def set_mode(self, mode):
        self.rx_thread.set_mode(mode)
        for b in self.att_bars:
            b.show_mode(mode, self.src_tx)

    def set_source(self, source):
        self.rx_thread.set_source(source)
        for b in self.att_bars:
            b.show_mode(self.cur_mode(), source)

    def on_atten(self, db, t, mode="jscc", source="camera"):
        self.mode_tx, self.src_tx = mode, source
        first = time.time() - self.t_att > 3.0
        self.att_rep, self.t_att = db, t
        if db < 0:                                   # TX bitstream without attenuation control
            return
        cur = max(0.0, db - TX_ATT0)
        if first:                                    # (re)appeared, e.g. TX restarted: follow the board
            self.att_extra = self.att_disp = cur
            for b in self.att_bars:
                b.show_value(cur)
        elif abs(cur - self.att_disp) > 2.0 and abs(cur - self.att_extra) > 0.3:
            self.att_disp = cur                      # the TX ramp and the animation drifted apart: resync

    def tick_clock(self):
        self.clock.setText(time.strftime("%Y-%m-%d  %H:%M:%S"))

    def refresh_cards(self):
        now = time.time()
        self.present()
        if not self.rate["tel"] or now - self.rate["tel"][-1] > 2.0:          # no telemetry: PHY not synchronised
            self.evm_now = self.snr_now = self.snr_dd = float("nan")
        if not self.rate["rx"] or now - self.rate["rx"][-1] > 2.0:
            self.psnr_now = self.lat_now = float("nan")
        att_ok = time.time() - self.t_att < 3.0         # TX board answers (cabled, TX bitstream with SPI access)
        for b in self.att_bars:
            if b.isVisible() != att_ok:
                b.setVisible(att_ok)
        st = (self.last_tel or {}).get("status", {})
        recent = [v for _, v in list(self.ps_hist)[-30:]]
        lat = [v for _, v in list(self.lat_hist)[-30:]]
        page = self.tabs.currentIndex()
        if args.lite:                        # demo step 2: TX previews arriving over the board-to-board cable
            link = time.time() - self.t_tx < 2.0
            if link != self.tx_link:
                self.tx_link = link
                for w in self.tx_widgets:
                    w.setVisible(link)
                if not link:
                    self.tx_buf.clear(); self.psnr_now = self.lat_now = float("nan"); self.ps_hist.clear(); self.lat_hist.clear()
            sscc = self.cur_mode() == "sscc"
            name = ("RX SSCC" if sscc else "RX decoded") if self.compact else \
                   ("RX JPEG (SSCC baseline)" if sscc else "RX decoder output")
            bad = self.sscc_status if sscc else ""
            for b in self.att_bars:
                b.show_mode(self.cur_mode(), self.src_tx)
            if self.src_tx.startswith("preset:"):        # still test image, e.g. preset:div2k_0801 -> "DIV2K 0801"
                pname = self.src_tx[len("preset:"):].replace("div2k_", "DIV2K ").replace("_", " ")
                tname = f"TX {pname}" if self.compact else f"TX {pname} → encoder"
            else:
                tname = ("TX video" if self.src_tx == "video" else "TX camera") if self.compact else \
                        ("TX demo video → encoder" if self.src_tx == "video" else "TX camera → encoder")
            if self.im_title["tx"].text() != tname:
                self.im_title["tx"].setText(tname)
            has_ps = link and self.psnr_now == self.psnr_now
            lab = self.im_title["rx"]
            lab.setTextFormat(QtCore.Qt.TextFormat.RichText)        # always (the plain variants hold &nbsp; too)
            fm, room = lab.fontMetrics(), lab.width() - 24          # padding + left border
            # longest variant that fits the title bar (small screen: one image column, ~380 px)
            for ps in ((f"PSNR {self.psnr_now:.2f} dB", f"PSNR {self.psnr_now:.1f}", f"{self.psnr_now:.1f} dB") if has_ps else ("",)):
                plain = "  ·  ".join(t for t in (name, bad, ps) if t)
                if fm.horizontalAdvance(plain) <= room:
                    break
            sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
            text = sep.join(t for t in (name, f"<span style='color:{C_BAD}'>{bad}</span>" if bad else "", ps) if t)
            if lab.text() != text:
                lab.setText(text)
        if page == 0 or not args.lite:
            self._card1(st, recent, lat)
        if page == 1 or not args.lite:
            self._card2(st)
        self.status.showMessage(("" if args.lite else f"TX {args.tx}: {'receiving' if self.fps('tx') > 0 else 'no data'}   ·   ") +
                                f"RX {args.rx}: {'receiving' if self.fps('rx') > 0 or self.fps('tel') > 0 else 'no data'}")

    def _card1(self, st, recent, lat):
        if self.compact:                     # small screen: one row of KPI tiles
            agc = f"{st.get('AGC_GAIN_IDX', 0)}{'' if st.get('AGC_LOCK') else ' (unlocked)'}"
            if self.tx_link and self.cur_mode() == "sscc":
                fer = 1 - np.mean(self.sscc_hist) if self.sscc_hist else float("nan")
                tiles = [("PSNR (dB)", f"{self.psnr_now:.2f}"),
                         ("PSNR mean / min", f"{np.mean(recent):.1f} / {np.min(recent):.1f}" if recent else "-"),
                         ("帧错误率 (CRC)", f"{100 * fer:.0f} %"), ("JPEG 质量", f"{self.sscc_q}"),
                         ("EVM (dB)", f"{self.evm_now:.1f}")]
            elif self.tx_link:
                tiles = [("PSNR (dB)", f"{self.psnr_now:.2f}"),
                         ("PSNR mean / min", f"{np.mean(recent):.1f} / {np.min(recent):.1f}" if recent else "-"),
                         ("E2E latency (ms)", f"{np.mean(lat):.0f}" if lat else "-"),
                         ("RX fps", f"{self.fps('rx'):.1f}"), ("EVM (dB)", f"{self.evm_now:.1f}")]
            else:
                tiles = [("RX fps", f"{self.fps('rx'):.1f}"), ("PHY frames / s", f"{self.rates.get('PHY_FRAMES', 0):.1f}"),
                         ("drops / s", f"{self.rates.get('FB_DROP', 0):.1f}"), ("EVM (dB)", f"{self.evm_now:.1f}"), ("AGC gain idx", agc)]
            self.card1.setText(tiles_html(tiles, column=True))
            return
        if args.lite:                        # board display: PSNR / latency only while the boards are cabled
            link = [("TX camera frames", f"{self.fps('tx'):.1f} fps"),
                    ("PSNR now", f"{self.psnr_now:.2f} dB"),
                    ("PSNR mean / min (last 30)", f"{np.mean(recent):.2f} / {np.min(recent):.2f} dB" if recent else "–"),
                    ("E2E latency now / mean", f"{self.lat_now:.0f} / {np.mean(lat):.0f} ms" if lat else "–"), None] if self.tx_link else []
            self.card1.setText(card_html(link + [
                ("RX decoded images", f"{self.fps('rx'):.1f} fps"), None,
                ("PHY frames / s", f"{self.rates.get('PHY_FRAMES', 0):.1f}"), ("decoder frames / s", f"{self.rates.get('IMG_FRAMES', 0):.1f}"),
                ("frame buffer drops / s", f"{self.rates.get('FB_DROP', 0):.1f}"), None,
                ("EVM final (64-QAM)", f"{self.evm_now:.1f} dB"),
                ("AGC", f"gain idx {st.get('AGC_GAIN_IDX', 0)}  {'locked' if st.get('AGC_LOCK') else 'unlocked'}")], center=True))
            return
        self.card1.setText(card_html([
            ("TX camera frames", f"{self.fps('tx'):.1f} fps"), ("RX decoded images", f"{self.fps('rx'):.1f} fps"), None,
            ("PSNR now", f"{self.psnr_now:.2f} dB"),
            ("PSNR mean / min (last 30)", f"{np.mean(recent):.2f} / {np.min(recent):.2f} dB" if recent else "–"),
            ("E2E latency now", f"{self.lat_now:.0f} ms"),
            ("E2E latency mean (last 30)", f"{np.mean(lat):.0f} ms" if lat else "–"), None,
            ("PHY frames / s", f"{self.rates.get('PHY_FRAMES', 0):.1f}"), ("decoder frames / s", f"{self.rates.get('IMG_FRAMES', 0):.1f}"),
            ("frame buffer drops / s", f"{self.rates.get('FB_DROP', 0):.1f}"),
            ("UDP incomplete messages", f"{self.rx_thread.ra.dropped}")]))

    def _card2(self, st):
        if self.compact:
            self.card2.setText(card_html([
                ("EVM final", f"{self.evm_now:.1f} dB"), ("SNR (LTF)" if self.ltf_n else "SNR", f"{self.snr_now:.1f} dB"), None,
                ("sync / s", f"{self.rates.get('SYNC_CNT', 0):.1f}"),
                ("AGC", f"{st.get('AGC_GAIN_IDX', 0)} {'locked' if st.get('AGC_LOCK') else 'unlocked'}")], center=True))
            return
        self.card2.setText(card_html([
            ("EVM final (64-QAM)", f"{self.evm_now:.1f} dB"), ("SNR (2×LTF, %d frames)" % self.ltf_n if self.ltf_n else "SNR (decision directed)", f"{self.snr_now:.1f} dB"),
            ("SNR (decision directed)", f"{self.snr_dd:.1f} dB") if self.ltf_n else None, None,
            ("sync / s", f"{self.rates.get('SYNC_CNT', 0):.1f}"), ("PHY frames total", f"{st.get('PHY_FRAMES', 0):,}"),
            ("frames dropped total", f"{st.get('FB_DROP', 0):,}"), None,
            ("AGC", f"gain idx {st.get('AGC_GAIN_IDX', 0)}  {'locked' if st.get('AGC_LOCK') else 'unlocked'}"),
            ("telemetry", f"{self.fps('tel'):.1f} /s, window sym {(self.last_tel or {}).get('start_sym', 0)}–"
                          f"{(self.last_tel or {}).get('start_sym', 0) + 19}")]))

    # ---------------- |H| 3-D surface (as in telemetry_gui.py)
    def push_h(self, t, hdb):
        if self.h_ref is None:
            self.h_ref = float(np.median(hdb))
        self.h_hist.append((t, hdb - self.h_ref))
        self.h_hist = [h for h in self.h_hist if h[0] >= t - self.T_WIN]
        if len(self.h_hist) < 2:
            return
        ts = np.array([h[0] for h in self.h_hist]) - t
        y = ts / self.T_WIN * self.T_SPAN
        x = (USED - 32).astype(float)
        z = np.array([h[1] for h in self.h_hist]).T
        if args.lite:
            zlo = float(np.floor(min(-1.0, z.min()))); zhi = float(np.ceil(max(1.0, z.max())))
            if self.h3d is not None:
                v = self.h3d_view
                if v.width() > 50 and v.height() > 50:
                    self.h3d.request(min(v.width(), 1600), min(v.height(), 900), z.T, ts, zlo, zhi)
                if self.compact:
                    self.h_title.setText(f"|H| from LTF  ·  0 dB = {self.h_ref:.1f} dB  ·  GPU {v.ms:.0f} ms")
                else:
                    self.h_title.setText(f"Channel |H| from LTF  ·  last {self.T_WIN:.0f} s  ·  0 dB = {self.h_ref:.1f} dB  "
                                         f"·  Mali-400 {v.ms:.0f} ms")
                return
            self.wf_img.setImage(z.T, levels=(zlo, zhi), autoLevels=False)
            self.wf_img.setRect(QtCore.QRectF(-26.5, ts[0], 53, -ts[0]))
            self.h_title.setText(f"Channel |H| from LTF  ·  last {self.T_WIN:.0f} s  ·  {zlo:+.0f} … {zhi:+.0f} dB  (0 dB = {self.h_ref:.1f} dB)")
            return
        zlo = float(np.floor(min(-1.0, z.min()))); zhi = float(np.ceil(max(1.0, z.max())))
        cols = self.cmap.map(((z - zlo) / (zhi - zlo)).ravel(), mode="float")
        if self.surf is None:
            self.surf = gl.GLSurfacePlotItem(x=x, y=y, z=z * self.Z_SCALE, colors=cols, smooth=False,
                                             drawEdges=False, computeNormals=False, shader=None)
            self.gv.addItem(self.surf)
        else:
            self.surf.setData(x=x, y=y, z=z * self.Z_SCALE, colors=cols)
        if getattr(self, "_axes_rng", None) != (zlo, zhi):
            self._axes_rng = (zlo, zhi); self.draw_axes(zlo, zhi)

    def draw_axes(self, zlo, zhi):
        for it in self.labels:
            self.gv.removeItem(it)
        self.labels = []
        Z, XE, T = self.Z_SCALE, self.XE, self.T_SPAN
        zf = zlo * Z
        ink = QtGui.QColor(C_INK); axc = (ink.redF(), ink.greenF(), ink.blueF(), 1.0); lbc = (*TH["DATA"], 255)

        def line(pts, col=axc, w=2):
            it = gl.GLLinePlotItem(pos=np.array(pts, float), color=col, width=w, antialias=True, mode="lines")
            self.gv.addItem(it); self.labels.append(it)

        def txt(pos, s, col=(*QtGui.QColor(C_INK).getRgb()[:3], 255)):
            it = gl.GLTextItem(pos=pos, text=s, color=col); self.gv.addItem(it); self.labels.append(it)
        g = gl.GLGridItem(); g.setSize(2 * XE, T); g.setSpacing(13, 10); g.setColor((*TH["GRID"], 45))
        g.translate(0, -T / 2, zf); self.gv.addItem(g); self.labels.append(g)
        line([(-XE, -T, zf), (XE, -T, zf)])
        for sc in (-26, -13, 0, 13, 26):
            line([(sc, -T, zf), (sc, -T - 1.5, zf)], axc, 1); txt((sc, -T - 4, zf), str(sc))
        txt((0, -T - 9, zf), "subcarrier", lbc)
        line([(XE, -T, zf), (XE, 0, zf)])
        for sec in range(int(self.T_WIN) + 1):
            yy = -sec / self.T_WIN * T
            line([(XE, yy, zf), (XE + 1.5, yy, zf)], axc, 1); txt((XE + 3, yy, zf), f"-{sec} s" if sec else "0 s")
        txt((XE + 10, -T / 2, zf), "time", lbc)
        line([(XE, 0, zf), (XE, 0, zhi * Z)])
        step = 1 if zhi - zlo <= 4 else 2
        for db in np.arange(zlo, zhi + 0.1, step):
            line([(XE, 0, db * Z), (XE + 1.5, 0, db * Z)], axc, 1)
            if db > zlo:
                txt((XE + 2, 7, db * Z), f"{db:+.0f}")
        txt((XE + 2, 7, zhi * Z + 4), "|H| dB", lbc)
        self.h_title.setText(f"Channel |H| from LTF  ·  last {self.T_WIN:.0f} s  ·  0 dB = {self.h_ref:.1f} dB")

    def shutdown(self):
        if getattr(self, "remote", None) is not None:
            self.remote.running = False; self.remote.sock.close(); self.remote.wait(1000)
        self.rx_thread.running = False; self.rx_thread.wait(2000); self.rx_thread.proc.kill()
        if self.h3d is not None:
            self.h3d.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "ripple"):
            self.ripple.setGeometry(self.rect())

    def tap_target(self, name):
        """widget for a remote 'tap' name (on the attenuation bar of the page shown, or a tab)"""
        if name.startswith("tab:"):
            return None
        bar = self.att_bars[1] if self.tabs.currentIndex() == 1 else self.att_bars[0]
        if name.startswith("mode:"):
            return bar.mbtn.get(name[5:])
        if name.startswith("src:"):
            return self.att_bars[0].sbtn.get(name[4:])
        if name == "att+":
            return bar.plus
        if name == "att-":
            return bar.minus
        if name.startswith("att:"):
            return self.att_bars[0].pbtn.get(int(name[4:]))
        return None

    def on_remote(self, line, addr):
        f = line.split()
        ok = True
        try:
            if f[0] == "tap" and len(f) == 2:
                if f[1].startswith("tab:"):
                    i = int(f[1][4:])
                    self.ripple.tap(self.tabs.tabBar().mapTo(self, self.tabs.tabBar().tabRect(i).center()))
                    self.tabs.setCurrentIndex(i)
                else:
                    w = self.tap_target(f[1])
                    if w is None or not w.isVisible():
                        ok = False
                    else:
                        self.ripple.tap(w.mapTo(self, w.rect().center())); w.click()
            elif f[0] == "page":
                self.tabs.setCurrentIndex(int(f[1]))
            elif f[0] == "mode":
                self.set_mode(f[1])
            elif f[0] == "src":
                self.set_source(f[1])
            elif f[0] == "atten":
                self.set_atten(float(f[1]))
            elif f[0] == "flash":
                self.ripple.flash()
            elif f[0] != "status":
                ok = False
        except (ValueError, IndexError):
            ok = False
        st = (self.last_tel or {}).get("status", {})
        tel_age = time.time() - self.rate["tel"][-1] if self.rate["tel"] else 1e9   # stale telemetry = no PHY sync
        self.remote.reply(addr, dict(tel_age=round(min(tel_age, 999), 2),
            ok=ok, cmd=line, page=self.tabs.currentIndex(), mode=self.cur_mode(), mode_tx=self.mode_tx, src=self.src_tx,
            tx_link=time.time() - self.t_att < 3.0, att_target=self.att_extra, att_shown=round(self.att_disp, 2),
            att_tx=None if self.att_rep is None else round(self.att_rep - TX_ATT0, 2),
            lat=None if self.lat_now != self.lat_now else round(self.lat_now, 0),
            rx_fps=round(self.fps("rx"), 1), sync=round(self.rates.get("SYNC_CNT", 0), 1) if tel_age < 2.0 else 0.0,
            psnr=None if self.psnr_now != self.psnr_now else round(self.psnr_now, 2),
            evm=None if self.evm_now != self.evm_now else round(self.evm_now, 1),
            fer=round(1 - float(np.mean(self.sscc_hist)), 3) if self.sscc_hist else None, sscc_status=self.sscc_status,
            agc=st.get("AGC_GAIN_IDX") if tel_age < 2.0 else None))

    def closeEvent(self, e):
        self.shutdown()
        e.accept()   # SIGTERM is not delivered under eglfs

    def switch_theme(self, key):
        """style page: remember the preset and restart the GUI in place (same PID, so systemd keeps tracking it)"""
        if key == args.theme:
            return
        self.style_hint.setText(f"正在切换到「{THEMES[key]['LABEL']}」…"); self.style_hint.repaint()
        QtWidgets.QApplication.processEvents()
        try:
            with open(THEME_FILE, "w") as f:
                f.write(key + "\n")
        except OSError as e:
            print(f"theme not saved: {e}", file=sys.stderr)
        self.shutdown()
        self.rx_thread.proc.join(2)
        if self.h3d is not None:
            self.h3d.proc.wait(2)
        argv, skip = [], False
        for a in sys.argv[1:]:                    # drop --theme / --page (and their values), then add the new ones
            if skip:
                skip = False
            elif a in ("--theme", "--page"):
                skip = True
            elif not a.startswith(("--theme=", "--page=")):
                argv.append(a)
        sys.stdout.flush(); sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + argv + ["--theme", key, "--page", "2"])


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    g = Gui(); g.resize(1800, 1000)
    if args.lite and not args.bench and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        g.showFullScreen()                   # board monitor: whole screen, no window decorations
    else:
        g.show()
    if args.page:
        g.tabs.setCurrentIndex(int(args.page))
    if args.bench:
        lag = []; last = [time.perf_counter()]

        def tick():                          # event-loop responsiveness: a 20 ms timer that should fire every 20 ms
            now = time.perf_counter(); lag.append((now - last[0]) * 1e3 - 20); last[0] = now
        tk = QtCore.QTimer(); tk.timeout.connect(tick); tk.start(20)

        def cpu():
            return time.process_time()                   # user + system CPU of the whole process (all threads)
        b0 = {}

        def start():
            b0.update(t=time.time(), cpu=cpu(), bn=dict(g.bn), rx=g.rx_thread.n_rx, tel=g.rx_thread.n_tel); lag.clear()

        def stop():
            dt = time.time() - b0["t"]
            l = np.array(lag) if lag else np.zeros(1)
            print(f"BENCH page {g.tabs.currentIndex()} lite {args.lite}: {dt:.0f} s, GUI CPU {100 * (cpu() - b0['cpu']) / dt:.0f}% of one core, "
                  f"rx img in {(g.rx_thread.n_rx - b0['rx']) / dt:.1f}/s drawn {(g.bn['rx_drawn'] - b0['bn'].get('rx_drawn', 0)) / dt:.1f}/s, "
                  f"tel in {(g.rx_thread.n_tel - b0['tel']) / dt:.1f}/s drawn {(g.bn['tel_drawn'] - b0['bn'].get('tel_drawn', 0)) / dt:.1f}/s, "
                  f"loop lag mean {l.mean():.1f} p95 {np.percentile(l, 95):.1f} max {l.max():.0f} ms", flush=True)
            g.close(); app.quit()
        QtCore.QTimer.singleShot(5000, start)                       # skip start-up
        QtCore.QTimer.singleShot(int((5 + args.bench) * 1000), stop)
    if args.shot:
        out, secs = args.shot[0], float(args.shot[1])
        page = int(args.page) if args.page else 0
        g.tabs.setCurrentIndex(page)
        QtCore.QTimer.singleShot(int(secs * 1000), lambda: (app.primaryScreen().grabWindow(int(g.winId())).save(out), g.close(), app.quit()))
    sys.exit(app.exec())
