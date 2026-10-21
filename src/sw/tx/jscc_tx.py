"""PYNQ driver for the OFDM_JSCC_PS_TX overlay (PYNQ-ZU, PYNQ 3.1.1).

    from jscc_tx import JsccTx
    tx = JsccTx()                       # loads jscc_tx.bit / jscc_tx.hwh next to this file
    tx.set_source("encoder")            # or "prbs" (PHY self test)
    tx.send_image(img)                  # (256, 256, 3) uint8 RGB -> encoder -> OFDM -> air
    tx.status()

Address map (HPM0_LPD): img_dma 0x8000_0000, registers 0x8002_0000.
"""
import os
import time
import numpy as np
from pynq import Overlay, MMIO, allocate

HERE = os.path.dirname(os.path.abspath(__file__))
REG_BASE = 0x8002_0000
IMG_BYTES = 256 * 256 * 3
R = dict(ID=0x00, VERSION=0x04, CTRL=0x08, SRC_ACTIVE=0x0C, IMG_BYTES=0x10, SYMS=0x14, SYM_FRAMES=0x18,
         TX_FRAMES=0x1C, FB_FRAMES=0x20, TLAST_ERR=0x24, STATUS=0x28, SPI_CMD=0x2C, SPI_STAT=0x30, SSCC_FRAMES=0x34)
SSCC_BYTES = 16360                               # SSCC packet per PHY frame (sscc_pkt.py; LDPC bitstream)
COUNTERS = ["IMG_BYTES", "SYMS", "SYM_FRAMES", "TX_FRAMES", "FB_FRAMES", "TLAST_ERR"]


class JsccTx:
    def __init__(self, bitfile=os.path.join(HERE, "jscc_tx.bit"), download=True):
        self.ol = Overlay(bitfile, download=download)
        self.reg = MMIO(REG_BASE, 0x1_0000)
        self.dma = self.ol.img_dma
        self.buf = allocate(shape=(IMG_BYTES // 4,), dtype=np.uint32)
        self.busy = False                                 # a transfer has been started and not waited for
        idv = self.rd("ID")
        if idv != 0x4A535458:
            raise RuntimeError(f"unexpected register block ID {idv:08x}")

    def rd(self, name):
        return self.reg.read(R[name])

    def wr(self, name, value):
        self.reg.write(R[name], int(value) & 0xFFFF_FFFF)

    def status(self):
        s = {k: self.rd(k) for k in COUNTERS}
        s.update(SRC_SEL="prbs" if self.rd("CTRL") & 1 else "encoder",
                 SRC_ACTIVE="prbs" if self.rd("SRC_ACTIVE") & 1 else "encoder",
                 MMCM250_LOCKED=self.rd("STATUS") & 1)
        return s

    # ---------------------------------------------------------------- AD9361 SPI (bitstream VERSION >= 2)
    def has_spi(self):
        return self.rd("VERSION") >= 2

    def spi(self, addr, value=None, timeout=0.05):
        """one AD9361 register write (value given) or read; returns the read data. Only after rf_ready
        (LUT init + TX quad phase search done)."""
        st = self.rd("SPI_STAT")
        if not st >> 16 & 1:
            raise RuntimeError("AD9361 not ready (rf_ready = 0)")
        c0 = st >> 8 & 0xFF
        cmd = (addr & 0x3FF) | ((value or 0) & 0xFF) << 10 | (1 << 24 if value is not None else 1 << 25)
        self.wr("SPI_CMD", cmd)
        t0 = time.time()
        while (self.rd("SPI_STAT") >> 8 & 0xFF) == c0:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"AD9361 SPI 0x{addr:03x} did not finish")
        return self.rd("SPI_STAT") & 0xFF                   # read again: data and count cross clock domains bit by bit

    def tx_atten(self):
        """TX1 attenuation in dB (0x073 / 0x074[0], 0.25 dB steps)"""
        return ((self.spi(0x074) & 1) << 8 | self.spi(0x073)) / 4

    def set_tx_atten(self, db):
        """TX1 attenuation 0..89.75 dB, takes effect at once (same sequence as the config LUT / ADI driver:
        clear 'immediately update TPC atten', write the word, set it again). Returns the value set."""
        code = int(round(min(max(float(db), 0.0), 89.75) * 4))
        r7c, r74 = self.spi(0x07C), self.spi(0x074)
        self.spi(0x07C, r7c & ~0x40)
        self.spi(0x074, (r74 & ~1) | (code >> 8 & 1))
        self.spi(0x073, code & 0xFF)
        self.spi(0x07C, r7c | 0x40)
        return code / 4

    def set_carrier(self, mhz, log=print):
        """retune both AD9361 synthesizers to `mhz` and repeat the TX quadrature calibration phase search
        (the PL init and its search run at 915 MHz; see ad9361_lo.py). Returns the search result."""
        import ad9361_lo as L
        t0 = time.time()
        while not self.rd("SPI_STAT") >> 16 & 1:            # PL init + its phase search still running
            if time.time() - t0 > 15:
                raise RuntimeError("AD9361 not ready (rf_ready = 0) after 15 s")
            time.sleep(0.1)
        L.retune(self.spi, mhz * 1e6, log)
        return L.tx_quad_search(self.spi, log)

    def clear_counters(self):
        self.wr("CTRL", (self.rd("CTRL") & 5) | 2)

    def set_source(self, src):
        """'encoder' (images from send_image) or 'prbs' (PRBS 64-QAM PHY self test). Switches at a frame boundary."""
        self.wr("CTRL", (self.rd("CTRL") & 4) | (1 if src == "prbs" else 0))

    # ---------------------------------------------------------------- SSCC baseline (bitstream VERSION >= 3)
    def has_sscc(self):
        return self.rd("VERSION") >= 3

    def set_mode(self, mode):
        """'jscc': PS bytes -> DeepJSCC encoder (send_image); 'sscc': PS bytes -> sscc_tx (send_packet).
        Switch only between images / packets; the OFDM side changes at a frame boundary."""
        self.wr("CTRL", (self.rd("CTRL") & 1) | (4 if mode == "sscc" else 0))

    def send_packet(self, pkt, timeout=2.0, wait=True):
        """one SSCC packet (SSCC_BYTES) -> one PHY frame"""
        if len(pkt) != SSCC_BYTES:
            raise ValueError(f"packet must have {SSCC_BYTES} bytes, got {len(pkt)}")
        if self.busy and not self._wait(timeout):
            raise TimeoutError("previous transfer still running")
        if getattr(self, "pbuf", None) is None:
            self.pbuf = allocate(shape=(SSCC_BYTES // 4,), dtype=np.uint32)
        self.pbuf.view(np.uint8)[:] = np.frombuffer(pkt, np.uint8)
        self.pbuf.flush()
        self.dma.sendchannel.transfer(self.pbuf)
        self.busy = True
        if wait and not self._wait(timeout):
            raise TimeoutError(f"sscc_tx did not take the packet within {timeout} s (status {self.status()})")

    def send_image(self, img, timeout=2.0, wait=True):
        """Stream one (256, 256, 3) uint8 image (NHWC) into the encoder. The DMA is back-pressured by the encoder,
        so with wait=True this returns when the encoder has taken the whole image."""
        a = np.ascontiguousarray(img, dtype=np.uint8).reshape(-1)
        if a.size != IMG_BYTES:
            raise ValueError(f"image must have {IMG_BYTES} bytes, got {a.size}")
        if self.busy and not self._wait(timeout):
            raise TimeoutError("previous image still being sent")
        self.buf.view(np.uint8)[:] = a
        self.buf.flush()
        self.dma.sendchannel.transfer(self.buf)
        self.busy = True
        if wait and not self._wait(timeout):
            raise TimeoutError(f"encoder did not take the image within {timeout} s (status {self.status()})")

    def _wait(self, timeout):
        t0 = time.time()
        while not self.dma.sendchannel.idle:
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.0005)
        self.busy = False
        return True

    def stream(self, images, seconds=10.0):
        """Send images round-robin for the given time; returns the achieved frame rate."""
        n, t0 = 0, time.time()
        while time.time() - t0 < seconds:
            self.send_image(images[n % len(images)])
            n += 1
        return n / (time.time() - t0)


def load_image(path):
    """.npy (256, 256, 3) uint8, or a DeepJSCC input.mem (one hex byte per line, NHWC)."""
    if path.endswith(".npy"):
        return np.load(path).astype(np.uint8).reshape(256, 256, 3)
    v = np.array([int(x, 16) for x in open(path).read().split()], dtype=np.uint16)
    return (v & 0xFF).astype(np.uint8).reshape(256, 256, 3)


if __name__ == "__main__":
    tx = JsccTx()
    time.sleep(1.0)
    for k, v in tx.status().items():
        print(f"{k:15s} {v}")
