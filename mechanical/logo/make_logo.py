"""GlideLink logo: icon (swept blades = wing + radio waves, dot grid = constellation / pixels) + wordmark (Montserrat, outlined)."""
import os, subprocess
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
L = os.path.dirname(os.path.abspath(__file__))
NAVY1, NAVY2, CYAN, BLUE, WHITE = '#0B2545', '#134074', '#22C3D6', '#4F8CFF', '#FFFFFF'

def text_path(txt, font, size, x, y, track=0):
    f = TTFont(os.path.join(L, 'fonts', font)); gs = f.getGlyphSet(); cmap = f.getBestCmap(); upm = f['head'].unitsPerEm
    s = size / upm; out = []; pen_x = x
    for ch in txt:
        g = cmap[ord(ch)]; sp = SVGPathPen(gs)
        gs[g].draw(TransformPen(sp, (s, 0, 0, -s, pen_x, y)))
        out.append(sp.getCommands()); pen_x += gs[g].width * s + track
    return ' '.join(out), pen_x - track - x

def blade(sx, sy, ex, ey, c_out, c_in):
    return f'M{sx} {sy} Q{c_out[0]} {c_out[1]} {ex} {ey} Q{c_in[0]} {c_in[1]} {sx} {sy} Z'

def icon(dark_bg=True):
    fg = WHITE if dark_bg else NAVY1
    g = []
    # three swept blades: a wing taking off, and three radio wavefronts
    g.append(f'<path d="{blade(26,160, 178,32, (124,180), (100,136))}" fill="url(#gl)"/>')
    g.append(f'<path d="{blade(62,132, 168,56, (130,144), (112,112))}" fill="{CYAN}" opacity=".85"/>')
    g.append(f'<path d="{blade(96,108, 158,76, (138,114), (124,94))}" fill="{fg}" opacity=".9"/>')
    # 3x3 dot grid (64QAM points / pixels) behind the wing
    for i in range(3):
        for j in range(3):
            r = 7.0 - 1.8 * (i + j) / 2
            g.append(f'<circle cx="{34 + 21*i}" cy="{40 + 21*j}" r="{r:.2f}" fill="{fg}" opacity="{0.95 - 0.18*(i+j)/2:.2f}"/>')
    return '\n'.join(g)

DEFS = f'''<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{NAVY2}"/><stop offset="1" stop-color="{NAVY1}"/></linearGradient>
<linearGradient id="gl" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{CYAN}"/></linearGradient>
</defs>'''

def svg(w, h, body, extra=''):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w*4}" height="{h*4}">\n{DEFS}\n{body}\n</svg>\n'

# 1) square icon badge
sq = svg(200, 200, f'<rect width="200" height="200" rx="44" fill="url(#bg)"/>\n{icon()}')
# 2) horizontal sticker: badge + wordmark on a white rounded rectangle
gpath, gw = text_path('Glide', 'montserrat_latest_latin-700-normal.ttf', 92, 0, 0)
lpath, lw = text_path('Link', 'montserrat_latest_latin-700-normal.ttf', 92, 0, 0)
sub, sw = text_path('DEEPJSCC  OVER  OFDM  ·  FPGA', 'montserrat_latest_latin-600-normal.ttf', 22, 0, 0, track=3.2)
tx, base = 236, 128; W = tx + gw + lw + 48; H = 200
hz = svg(W, H, f'''<rect width="{W}" height="{H}" rx="44" fill="{WHITE}"/>
<rect width="200" height="200" rx="44" fill="url(#bg)"/>
{icon()}
<g transform="translate({tx} {base})"><path d="{gpath}" fill="{NAVY1}"/><path d="{lpath}" fill="url(#gl)" transform="translate({gw} 0)"/></g>
<g transform="translate({tx+3} {base+44})"><path d="{sub}" fill="#5B6B7F"/></g>''')
# 3) round sticker
R = 150
rd = svg(300, 300, f'''<circle cx="150" cy="150" r="150" fill="url(#bg)"/>
<g transform="translate(50 18)">{icon()}</g>
<g transform="translate({150-(gw+lw)*0.48/2} 238) scale(.48)"><path d="{gpath}" fill="{WHITE}"/><path d="{lpath}" fill="{CYAN}" transform="translate({gw} 0)"/></g>''')
for name, s in (('glidelink_icon', sq), ('glidelink_sticker_wide', hz), ('glidelink_sticker_round', rd)):
    open(os.path.join(L, name + '.svg'), 'w', encoding='utf-8').write(s)
print('ok', round(W), H, round(sw))
