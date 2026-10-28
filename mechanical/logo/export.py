"""Print files for the stickers: vector PDF at the sticker size and a 600 dpi transparent PNG (Edge headless)."""
import os, subprocess, sys, tempfile
E = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
D = os.path.dirname(os.path.abspath(__file__)); PROF = tempfile.mkdtemp(prefix='edgeprof')
SIZES = {'glidelink_sticker_wide': (90, 24.36, '_90×24.4mm'), 'glidelink_sticker_round': (50, 50, '_Ø50mm'),
         'glidelink_icon': (40, 40, '_40×40mm')}   # mm, PDF name suffix
for name, (w, h, suffix) in SIZES.items():
    url = 'file:///' + os.path.join(D, '_tmp.html').replace(os.sep, '/')
    open(os.path.join(D, '_tmp.html'), 'w').write(f'<html><head><style>@page{{size:{w}mm {h}mm;margin:0}}html,body{{margin:0}}'
        f'img{{width:{w}mm;height:{h}mm;display:block}}</style></head><body><img src="{name}.svg"></body></html>')
    subprocess.run([E, '--headless', '--disable-gpu', '--no-pdf-header-footer', f'--user-data-dir={PROF}',
                    f'--print-to-pdf={os.path.join(D, name + suffix + ".pdf")}', url], check=True, capture_output=True)
    px, py = round(w / 25.4 * 600), round(h / 25.4 * 600)
    open(os.path.join(D, '_tmp.html'), 'w').write(f'<html><body style="margin:0;background:transparent"><img src="{name}.svg" '
        f'style="width:{px}px;height:{py}px;display:block"></body></html>')
    subprocess.run([E, '--headless', '--disable-gpu', f'--user-data-dir={PROF}', '--default-background-color=00000000', '--hide-scrollbars',
                    f'--screenshot={os.path.join(D, name + ".png")}', f'--window-size={px},{py}', url], check=True, capture_output=True)
os.remove(os.path.join(D, '_tmp.html'))
