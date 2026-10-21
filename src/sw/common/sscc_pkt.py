"""SSCC baseline packets (one per PHY frame, carried by sscc_tx / sscc_rx in the PL):

    0   2  magic b"SC"
    2   2  frame number (u16, little endian)
    4   2  JPEG length
    6   1  JPEG quality
    7   1  reserved
    8   4  CRC32 of bytes 0..7 + the JPEG
    12  .. JPEG (256 x 256 RGB centre crop, quality chosen per frame to fill the packet), zero padding

PAY_BYTES = 16360: a frame carries 101 LDPC codewords (802.11n, n = 1944, rate 2/3) = 130896 information bits
(16362 bytes; the last 16 bits are a zero tail) in 32768 64-QAM symbols: the same channel symbols per image as the
DeepJSCC link.
"""
import struct
import zlib
import numpy as np

PAY_BYTES = 16360
HDR = struct.Struct("<2sHHBBI")
MAX_JPEG = PAY_BYTES - HDR.size


def pack(jpeg, frame, quality):
    if len(jpeg) > MAX_JPEG:
        raise ValueError(f"JPEG {len(jpeg)} bytes > {MAX_JPEG}")
    h = HDR.pack(b"SC", frame & 0xFFFF, len(jpeg), quality, 0, 0)
    crc = zlib.crc32(h[:8] + jpeg)
    return h[:8] + struct.pack("<I", crc) + jpeg + bytes(MAX_JPEG - len(jpeg))


def unpack(pkt):
    """-> dict(ok, frame, quality, jpeg): ok = magic and CRC right; with errors the JPEG bytes are returned as
    received (length clamped), so a damaged image can still be shown"""
    magic, frame, n, q, _, crc = HDR.unpack_from(pkt)
    n = min(n, MAX_JPEG)
    jpeg = bytes(pkt[HDR.size:HDR.size + n])
    ok = magic == b"SC" and zlib.crc32(bytes(pkt[:8]) + jpeg) == crc
    return dict(ok=ok, frame=frame, quality=q, jpeg=jpeg, magic=magic == b"SC")


class JpegFit:
    """JPEG quality search per frame: the largest quality whose JPEG fits MAX_JPEG (starts from the last one)."""
    def __init__(self, q=80):
        self.q = q

    def encode(self, rgb):
        import cv2
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        q = self.q
        while True:
            ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
            if len(buf) <= MAX_JPEG or q <= 5:
                break
            q -= 5
        if q == self.q and q < 95:                    # it fitted at once: try one step up next time
            ok2, buf2 = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q + 5])
            if len(buf2) <= MAX_JPEG:
                q, buf = q + 5, buf2
        self.q = q
        return buf.tobytes(), q
