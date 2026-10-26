"""AD9361 data-port loopback on the RX board: polarity check of the TX LVDS data pairs (FPGA -> AD9361) of the
daughter card, without the analog path.

With 0x3F5 bit 0 (Data Port Loop Test Enable) the AD9361 returns the samples it receives on its TX data port on its
RX data port. The RX bitstream drives the TX port with zero samples (dac_valid = 1, ad9361_top.v); ad9361_lvds_mode
XORs them with the TX_SWAP_PAIRS mask (pair D5, sample bits 11 and 5) when switch SW0 is on (STATUS bit 1). A pair
with swapped P/N on the card toggles two more bits, k + 6 and k (the pair carries the upper half of the sample in one
DATA_CLK phase and the lower half in the other). The script removes the switch's own inversion and prints the card's
swapped-pair mask and the SW0 position for it: on for a card with pair D5 swapped (the third-party card), off for a
card without swapped pairs (FMCOMMS3, both cards measured 2026-10-26). RX pairs are not covered (an inverted RX pair
would already break reception).

Needs jscc-rx and jscc-gui stopped (uses the capture DMA and the SPI port):
    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 lvds_loopback.py'
"""
import time

import numpy as np

from jscc_rx import JsccRx

TX_SWAP_PAIRS = 0x20               # pairs inverted with SW0 on (ad9361_lvds_mode.v TX_SWAP_PAIRS)


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
    sw = rx.rd("STATUS") >> 1 & 1
    swap = seen ^ (TX_SWAP_PAIRS if sw else 0)                               # minus the switch's own inversion
    if odd:
        print(f"RESULT: unexpected set bits {odd} (not in pairs k, k + 6): check the setup")
    advice = ("SW0 off" if swap == 0 else "SW0 on" if swap == TX_SWAP_PAIRS else
              f"neither SW0 position fits (TX_SWAP_PAIRS = 0x{TX_SWAP_PAIRS:02X})")
    print(f"RESULT: SW0 {'on' if sw else 'off'}, read back 0x{seen << 6 | seen:03X} -> TX pairs swapped on this card "
          f"0x{swap:02X}: {advice} for this card")
finally:
    rx.rf.wr(0x3F5, reg0)
    print(f"restored 0x3F5 = 0x{rx.rf.rd(0x3F5):02X}")
