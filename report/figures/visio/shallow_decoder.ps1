# Shallow decoder structure (report/notes/shallow_decoder_design.md).
#   powershell -ExecutionPolicy Bypass -File shallow_decoder.ps1   -> shallow_decoder.vsdx + ../../notes/figures/shallow_decoder/dec_structure.png
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'visio_style.ps1')
$repo = Resolve-Path (Join-Path $here '..\..\..')
$ST.SIZE = 7

Open-Visio 200 93

function Note($x, $y, $w, $t) { [void](Label $x $y $w 4 $t 6 1 $ST.MUTE) }

# ---- row 1: 64x64 latent resolution
[void](Label 2 3 120 5 '64×64（潜变量分辨率）' 7.5 0 $ST.INK $true)
[void](Box 2 17 22 16 $ST.GRAY ("接收符号`n64×64×16") $false 7)
[void](Line 24 25 30 25)
[void](Box 30 17 22 16 $ST.COMPUTE ("线性3×3`n16→16") $false 7)
Note 30 34 22 '9.4 M'
[void](Line 52 25 58 25)
[void](Box 58 11 40 28 $ST.COMPUTE ("残差块×4`n3×3 → PWL → 3×3 → PWL`n+ 恒等支路`n16通道") $false 7)
Note 58 40 40 '75.5 M'
[void](Line 98 25 104 25)
[void](Box 104 17 30 16 $ST.COMPUTE ("亚像素3×3`n16→192（主96 | 旁96）") $false 7)
Note 104 34 20 '113 M'

# ---- row 2: 128x128 and 256x256 (wire from the merged engine at y 46, boxes below)
[void](Path @(128, 33, 128, 46, 8, 46, 8, 77, 14, 77))
[void](Dot 8 60)
[void](Line 8 60 14 60)
[void](Label 12 48 40 4 '128×128' 7.5 0 $ST.INK $true)
[void](Box 14 54 28 12 $ST.STORE ("PixelShuffle×2`n128×128×24") $false 7)
[void](Box 14 71 28 12 $ST.STORE ("PixelShuffle×2`n128×128×24") $false 7)
[void](Label 14 84 28 4 '线性旁路' 6 1 $ST.MUTE)
[void](Line 42 60 48 60)
[void](Box 48 54 18 12 $ST.GREEN 'IGDN' $false 7)
Note 48 67 18 '9.4 M'
[void](Path @(66, 60, 76, 60, 76, 65.5))
[void](Path @(42, 77, 76, 77, 76, 72.5))
[void](Circle 76 69 3.5 '+' 9)
[void](Line 79.5 69 86 69)
[void](Box 86 63 28 12 $ST.COMPUTE ("亚像素3×3`n24→12") $false 7)
Note 86 76 28 '42.5 M'
[void](Label 120 48 60 4 '256×256' 7.5 0 $ST.INK $true)
[void](Line 114 69 120 69)
[void](Box 120 63 26 12 $ST.STORE ("PixelShuffle×2`n256×256×3") $false 7)
[void](Line 146 69 151 69)
[void](Box 151 63 18 12 $ST.COMPUTE 'sigmoid' $false 7)
[void](Line 169 69 174 69)
[void](Box 174 63 24 12 $ST.GRAY ("RGB图像`n256×256×3") $false 7)
[void](Label 120 85 78 6 '框下数字：每帧乘累加量（共0.250 GMAC）' 6 2 $ST.MUTE)

$out = Join-Path $repo 'report\notes\figures\shallow_decoder\dec_structure.png'
Save-Figure (Join-Path $here 'shallow_decoder.vsdx') $out
'saved'
