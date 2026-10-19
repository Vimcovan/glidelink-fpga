"""Regenerate the network RTL from the quantized export and update src/hw.

    python src/network/regen_hw.py                       (deployed model: data/model_shallow, shallow decoder)
    python src/network/regen_hw.py --export data/model --files-txt   (first-stage model with the symmetric decoder)

1. gen_rtl_init.py: <export>/params + manifest.json -> <export>/rtl_init/<engine>/*.mem, engine.json
2. gen_rtl_top.py:  blk_enc_0_latent_idx (TX) and blk_rx_in_output (RX) -> src/network/gen/
3. the absolute $readmemh paths in the two tops are replaced by unique file names
   (<dir>__<subdir>__<file>.mem, what Vivado projects expect), and the referenced .mem files are copied to
   src/hw/{tx,rx}/rtl/network/mem/; the tops go to src/hw/{tx,rx}/rtl/network/rtl/.

All files are written with LF line ends, so an unchanged export leaves the work tree without changes: that is the check
that the committed RTL comes from the committed model (check with `git diff --stat`; on Windows `git status` may list
files whose content is unchanged).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXP = REPO / 'data' / 'model_shallow'
GEN = HERE / 'gen'
TOPS = {'tx': (('enc.0', 'latent_idx'), 'blk_enc_0_latent_idx'),
        'rx': (('rx_in', 'output'), 'blk_rx_in_output')}


def run(*args):
    print('>', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([sys.executable, *map(str, args)], check=True)


def local_name(path: Path) -> str:
    return '__'.join(path.relative_to(EXP).parts)


def install(side: str, top: str):
    text = (GEN / f'{top}.sv').read_text(encoding='ascii')
    dst = REPO / 'src' / 'hw' / side / 'rtl' / 'network'
    copied = set()
    for ref in sorted(set(re.findall(r'"([^"\r\n]+)"', text))):
        p = Path(ref)
        if not p.is_absolute():
            continue
        # weight ROM parameters are prefixes (.../wrom); the RTL appends .mem, _sN.mem, _g_cN.mem ...
        files = [p] if p.is_file() else sorted(x for x in p.parent.glob(p.name + '*') if x.is_file())
        if not files:
            raise FileNotFoundError(ref)
        for f in files:
            shutil.copyfile(f, dst / 'mem' / local_name(f))
            copied.add(local_name(f))
        text = text.replace(f'"{ref}"', f'"{local_name(p)}"')
    (dst / 'rtl' / f'{top}.sv').write_text(text, encoding='ascii', newline='\n')
    extra = sorted({f.name for f in (dst / 'mem').glob('*.mem')} - copied)
    print(f'{side}: {top}.sv, {len(copied)} .mem files' + (f'; not referenced: {extra}' if extra else ''))
    return copied, extra


def sync_files_txt(side: str, copied, extra, prune: bool):
    """files.txt [src]: the network .mem entries replaced by the installed set (right after the [src] header), the
    network library modules present in rtl/network/rtl added; with prune, unreferenced .mem files are deleted"""
    hw = REPO / 'src' / 'hw' / side
    if prune:
        for n in extra:
            (hw / 'rtl' / 'network' / 'mem' / n).unlink()
    lines = (hw / 'files.txt').read_text(encoding='utf-8').splitlines()
    lines = [l for l in lines if not l.startswith('rtl/network/mem/')]
    i = lines.index('[src]') + 1
    lines[i:i] = [f'rtl/network/mem/{n}' for n in sorted(copied)]
    have = set(lines)
    j = lines.index('[constr]')
    for f in sorted((hw / 'rtl' / 'network' / 'rtl').glob('*.sv')):
        e = f'rtl/network/rtl/{f.name}'
        if e not in have:
            lines.insert(j, e); j += 1
            print(f'{side}: files.txt + {e}')
    (hw / 'files.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


def main():
    global EXP, GEN
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', type=Path, default=EXP, help='export directory (default data/model_shallow)')
    ap.add_argument('--gen', type=Path, default=GEN, help='generated-top directory (default src/network/gen)')
    ap.add_argument('--files-txt', action='store_true', help='also update the network entries of src/hw/*/files.txt '
                    'and delete .mem files the new tops do not reference (another network)')
    a = ap.parse_args()
    EXP, GEN = a.export.resolve(), a.gen.resolve()
    run(HERE / 'gen_rtl_init.py', '--export', EXP)
    for side, ((first, last), top) in TOPS.items():
        run(HERE / 'gen_rtl_top.py', first, last, '--export', EXP, '--out', GEN)
        copied, extra = install(side, top)
        if a.files_txt:
            sync_files_txt(side, copied, extra, prune=True)
    print('done; `git diff --stat` lists every file that differs from the committed RTL')


if __name__ == '__main__':
    main()
