"""Poster product photo from the hand-finished cut-out (RGBA): tone, white background (no shadow, the cut-out has none)."""
import sys
from PIL import Image, ImageFilter, ImageEnhance
cut = Image.open(sys.argv[1]).convert('RGBA')
x0, y0, x1, y1 = cut.getbbox()
m = 30
cut = cut.crop((max(0, x0 - m), max(0, y0 - m), x1 + m, y1 + m))
rgb, a = cut.convert('RGB'), cut.getchannel('A')
rgb = ImageEnhance.Brightness(rgb).enhance(1.08)
rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
rgb = ImageEnhance.Color(rgb).enhance(1.12)
canvas = Image.new('RGB', cut.size, 'white')
canvas.paste(rgb, (0, 0), a)
canvas.save(sys.argv[2], quality=93)
print(canvas.size)
