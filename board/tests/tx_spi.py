"""One AD9361 register access on the TX board while jscc-tx runs (PS SPI, bitstream VERSION >= 2):
    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 tx_spi.py 0x03E [0x08]'"""
import sys
from jscc_tx import JsccTx
t = JsccTx(download=False)
a = int(sys.argv[1], 0)
if len(sys.argv) > 2:
    t.spi(a, int(sys.argv[2], 0))
print(f"0x{a:03X} = 0x{t.spi(a):02X}")
