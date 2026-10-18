"""Merge measured join-FIFO peaks into src/network/fifo_sizes.json (elements, peak + margin).

  python sim/network/fifo_sizes.py <first> <last> inst=peak ...
inst is the FIFO instance suffix (ident of '<op out>.<port>'); keys are matched through the
generated module's json so the stored key is the readable '<op out>.<port>'.
"""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2] / 'src' / 'network'
sys.path.insert(0, str(root))
from gen_rtl_top import MARGIN, SIZES, ident  # noqa: E402

first, last, pairs = sys.argv[1], sys.argv[2], sys.argv[3:]
mod = f'blk_{ident(first)}' + (f'_{ident(last)}' if last != first else '')
gen = Path(os.environ.get('NET_GEN_DIR', root / 'gen'))            # run_top_sim.ps1 -GenDir
info = json.loads((gen / f'{mod}.json').read_text(encoding='utf-8'))
keys = {ident(f['key']): f['key'] for f in info['fifos']}
sizes = json.loads(SIZES.read_text(encoding='utf-8')) if SIZES.exists() else {}
for p in pairs:
    inst, peak = p.rsplit('=', 1)
    key = keys[inst]
    peak = int(peak)
    sizes[key] = MARGIN(peak)
    print(f'  {key:28s} peak {peak:6d} -> {sizes[key]:6d}')
SIZES.write_text(json.dumps(dict(sorted(sizes.items())), indent=1), encoding='utf-8', newline='\n')
