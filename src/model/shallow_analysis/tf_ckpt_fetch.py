"""Read single tensors of a TensorFlow checkpoint (tensor bundle) on the Hugging Face hub without TensorFlow and without
downloading the whole data shard: the .index file (a LevelDB table of BundleEntryProto) is parsed here, the tensor bytes
are fetched with HTTP range requests.

    python tf_ckpt_fetch.py <hub url of the checkpoint prefix, ending in ckpt-N> [--list] [--grep synthesis] --out x.npz
"""
import argparse
import re
import struct
import urllib.request

import numpy as np

DTYPES = {1: np.float32, 2: np.float64, 3: np.int32, 9: np.int64, 19: np.float16}


def varint(b, i):
    v = s = 0
    while True:
        c = b[i]; i += 1
        v |= (c & 0x7F) << s; s += 7
        if not c & 0x80:
            return v, i


def block_entries(blk):
    """key/value pairs of one LevelDB block (contents without the 5-byte trailer)"""
    nres = struct.unpack('<I', blk[-4:])[0]
    end = len(blk) - 4 - 4 * nres
    i, key, out = 0, b'', []
    while i < end:
        shared, i = varint(blk, i); unshared, i = varint(blk, i); vlen, i = varint(blk, i)
        key = key[:shared] + blk[i:i + unshared]; i += unshared
        out.append((key, blk[i:i + vlen])); i += vlen
    return out


def read_index(data):
    footer = data[-48:]
    i = 0
    _, i = varint(footer, i); _, i = varint(footer, i)          # metaindex handle
    off, i = varint(footer, i); size, i = varint(footer, i)     # index block handle
    entries = []
    for _, h in block_entries(data[off:off + size]):
        o, j = varint(h, 0); s, _ = varint(h, j)
        assert data[o + s] == 0, 'compressed block'                # tensor bundles are written uncompressed
        entries += block_entries(data[o:o + s])
    return entries


def parse_entry(v):
    """BundleEntryProto -> dict(dtype, shape, shard, offset, size)"""
    i, e = 0, {'shape': [], 'shard': 0, 'offset': 0}
    while i < len(v):
        tag, i = varint(v, i); f, w = tag >> 3, tag & 7
        if w == 0:
            x, i = varint(v, i)
            e[{1: 'dtype', 3: 'shard', 4: 'offset', 5: 'size'}.get(f, f'f{f}')] = x
        elif w == 2:
            n, i = varint(v, i); sub = v[i:i + n]; i += n
            if f == 2:                                              # TensorShapeProto: repeated dim {size}
                j = 0
                while j < len(sub):
                    t, j = varint(sub, j); m, j = varint(sub, j); d = sub[j:j + m]; j += m
                    if t >> 3 == 2:
                        k = 0
                        while k < len(d):
                            t2, k = varint(d, k)
                            if t2 >> 3 == 1:
                                sz, k = varint(d, k); e['shape'].append(sz)
                            else:
                                _, k = varint(d, k)
        elif w == 5:
            i += 4
        elif w == 1:
            i += 8
    return e


def get(url, rng=None):
    req = urllib.request.Request(url, headers={'Range': f'bytes={rng[0]}-{rng[1]}'} if rng else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('prefix', help='https://huggingface.co/<repo>/resolve/main/.../ckpt-N')
    ap.add_argument('--grep', default='synthesis')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    idx = get(a.prefix + '.index')
    tensors = {}
    for k, v in read_index(idx):
        if not k:
            continue                                                # header entry
        e = parse_entry(v)
        if 'size' in e:
            tensors[k.decode()] = e
    sel = {k: e for k, e in tensors.items() if re.search(a.grep, k) and 'OPTIMIZER' not in k}
    for k, e in sel.items():
        print(k, e['shape'], np.dtype(DTYPES.get(e['dtype'], np.uint8)).name, e['size'])
    if a.list or not a.out:
        return
    out = {}
    for k, e in sel.items():
        raw = get(a.prefix + f'.data-{e["shard"]:05d}-of-00001', (e['offset'], e['offset'] + e['size'] - 1))
        out[re.sub(r'[^0-9A-Za-z]+', '_', k)] = np.frombuffer(raw, DTYPES[e['dtype']]).reshape(e['shape'])
    np.savez(a.out, **out)
    print('saved', a.out, sum(v.nbytes for v in out.values()), 'bytes')


if __name__ == '__main__':
    main()
