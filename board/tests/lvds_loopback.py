"""AD9361 data-port loopback on the RX board: polarity check of the TX LVDS data pairs (FPGA -> AD9361) of the
daughter card, without the analog path.

With 0x3F5 bit 0 (Data Port Loop Test Enable) the AD9361 returns the samples it receives on its TX data port on its
RX data port. The RX bitstream keeps dac_valid = 1 with zero samples (ad9361_top.v), so ad9361_lvds_mode sends them
through its normal path, which inverts sample bits 11 and 5 (TX pair D5, RTL_TX_SWAP in src/sw/tx/jscc_tx.py): a card
without swapped pairs reads back 0x820 in I and Q. Each further TX pair with swapped P/N toggles two more bits, k + 6
and k (the pair carries the upper half of the sample in one DATA_CLK phase and the lower half in the other). The
script prints the card's swapped-pair mask, the value for lvds_tx_swap.txt on the TX board when this card is used
there. RX pairs are not covered (an inverted RX pair would already break reception). Measured 2026-10-26: two
FMCOMMS3 cards 0x00 each.

Needs jscc-rx and jscc-gui stopped (uses the capture DMA and the SPI port):
    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 lvds_loopback.py'
"""
import time

import numpy as np

from jscc_rx import JsccRx

RTL_TX_SWAP = 0x20                 # TX pairs the RTL inverts (= src/sw/tx/jscc_tx.py RTL_TX_SWAP)


def raw_words(rx, n):
    x = rx.capture_adc(n, trig="now", timeout=2.0)
    i = x.real.astype(np.int64) & 0xFFF
    q = x.imag.astype(np.int64) & 0xFFF
    return i, q


def report(tag, i, q):
    print(f"{tag}: {len(i)} samples")
    for name, w in (("I", i), ("Q", q)):
        ones = [(w >> b & 1).mean() for b in range(12)]
        vals, cnt = np.unique(w, return_counts=True)
        top = ", ".join(f"0x{v:03X} ({c / len(w):.1%})" for v, c in sorted(zip(vals, cnt), key=lambda t: -t[1])[:4])
        print(f"  {name}: fraction of ones per bit 11..0: " + " ".join(f"{ones[b]:.2f}" for b in range(11, -1, -1)))
        print(f"     most frequent words: {top}")


rx = JsccRx(download=False)
reg0 = rx.rf.rd(0x3F5)
print(f"0x3F5 = 0x{reg0:02X}, 0x3D/0x3E (LVDS invert) = 0x{rx.rf.rd(0x03D):02X} / 0x{rx.rf.rd(0x03E):02X}")
i, q = raw_words(rx, 4096)
report("loopback off (antenna input)", i, q)
try:
    rx.rf.wr(0x3F5, reg0 | 0x01)
    time.sleep(0.05)
    print(f"0x3F5 -> 0x{rx.rf.rd(0x3F5):02X}")
    i, q = raw_words(rx, 65536)
    report("loopback on (TX port driven with 0)", i, q)
    bad = [b for b in range(12) if (i >> b & 1).mean() > 0.5 or (q >> b & 1).mean() > 0.5]
    seen = sum(1 << k for k in range(6) if k in bad and k + 6 in bad)          # pairs read back inverted
    odd = [b for b in bad if not ((b + 6) in bad or (b - 6) in bad)]
    swap = seen ^ RTL_TX_SWAP                                                  # minus the RTL's own inversion
    if odd:
        print(f"RESULT: unexpected set bits {odd} (not in pairs k, k + 6): check the setup")
    print(f"RESULT: read back 0x{seen << 6 | seen:03X} -> TX pairs swapped on this card 0x{swap:02X}; "
          f"lvds_tx_swap.txt on the TX board for this card: 0x{swap:02X}")
finally:
    rx.rf.wr(0x3F5, reg0)
    print(f"restored 0x3F5 = 0x{rx.rf.rd(0x3F5):02X}")
