# LTF fine timing (time_sync.sv), drawn in the style of the hand-drawn frame detection figure (sync_hw_fd.vsdx).
#   powershell -ExecutionPolicy Bypass -File ts_hw.ps1   -> sync_hw_ts.vsdx + ../hw/ts_hw.png
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'visio_style.ps1')
$repo = Resolve-Path (Join-Path $here '..\..\..')
$ROUND = '2 mm'

Open-Visio 180 92

function Mul($cx, $cy, $r = 4) {
  # complex multiplier: yellow circle with a cross
  $c = Circle $cx $cy $r ''
  $d = $r * 0.7071
  foreach ($p in @(@(($cx - $d), ($cy - $d), ($cx + $d), ($cy + $d)), @(($cx - $d), ($cy + $d), ($cx + $d), ($cy - $d)))) {
    $l = Line $p[0] $p[1] $p[2] $p[3] $false
    $l.CellsU('LineColor').FormulaU = 'RGB(127,96,0)'
  }
}
function Add($cx, $cy, $r = 4) {
  # adder: yellow circle with a plus
  $c = Circle $cx $cy $r ''
  foreach ($p in @(@(($cx - $r), $cy, ($cx + $r), $cy), @($cx, ($cy - $r), $cx, ($cy + $r)))) {
    $l = Line $p[0] $p[1] $p[2] $p[3] $false
    $l.CellsU('LineColor').FormulaU = 'RGB(127,96,0)'
  }
}
function Sup($s, $b, $e) {
  # superscript characters b..e-1 of the shape text (visCharacterPos = 4, superscript = 1)
  $c = $s.Characters; $c.Begin = $b; $c.End = $e
  [void]$c.GetType().InvokeMember('CharProps', [Reflection.BindingFlags]::SetProperty, $null, $c, @([int16]4, [int16]1))
}
function Demux($x, $w, $cy, $hNarrow, $hWide, $text) {
  $l = $hNarrow / 2; $r = $hWide / 2
  $s = Path @($x, ($cy - $l), ($x + $w), ($cy - $r), ($x + $w), ($cy + $r), $x, ($cy + $l), $x, ($cy - $l)) $false
  $s.CellsU('Geometry1.NoFill').FormulaU = 'FALSE'
  $s.CellsU('FillForegnd').FormulaU = 'RGB(219,238,243)'; $s.CellsU('FillPattern').FormulaU = '1'
  $s.CellsU('LineColor').FormulaU = 'RGB(32,88,103)'; $s.CellsU('ShdwPattern').FormulaU = '0'
  $s.Text = $text; Style-Text $s 6
}
function Tag($x, $y, $w, $text, $align = 1) { [void](Label $x $y $w 5 $text 7 $align $ST.MUTE) }

# ---------------------------------------------------------------- input and broadcast
[void](Label 0 32 8 5 'r(n)' 8 1)
[void](Line 0 38 8 38)
[void](Box 8 6 9 72 $ST.STORE "广`n播" $false 7.5)

# ---------------------------------------------------------------- LTF matched filter: three real FIRs + Gauss multiplication
$fy = @(12, 22, 32)
$ft = @('FIR（系数 c），输入 I', 'FIR（系数 d），输入 Q', 'FIR（系数 c+d），输入 I+Q')
for ($k = 0; $k -lt 3; $k++) {
  [void](Line 17 $fy[$k] 22 $fy[$k])
  [void](Box 22 ($fy[$k] - 3.5) 32 7 $ST.COMPUTE $ft[$k] $false 6.5)
  [void](Line 54 $fy[$k] 60 $fy[$k])
  Tag 54 ($fy[$k] - 5) 6 ('k' + [char](0x2081 + $k))
}
[void](Box 60 6 24 32 $ST.COMPUTE ("Gauss 复数乘法`nRe = k₁ − k₂`nIm = k₃ − k₁ − k₂") $false 6.5)

# |X(n)|^2 = X(n) X*(n)
[void](Line 84 22 96 22)
Tag 84 17 8 'X(n)'
[void](Dot 89 22)
[void](Path @(89, 22, 89, 31, 100, 31, 100, 26))
[void](Label 96 26.5 4 4 '*' 8 0)
Mul 100 22
# two-point metric |X(n-1)|^2 + |X(n)|^2 (the LTF peak splits over two samples at a half-sample offset)
[void](Line 104 22 112 22)
Tag 103 17 10 '|X(n)|²'
[void](Dot 108 22)
[void](Line 108 22 108 27.5)
$z = Box 104 27.5 8 6 $ST.STORE 'Z-1' $false 7; Sup $z 1 3
[void](Path @(112, 30.5, 116, 30.5, 116, 26))
Add 116 22
[void](Line 120 22 124 22)

# ---------------------------------------------------------------- energy E = sum over 64 samples of |r(n)|^2
[void](Line 17 50 26 50)
[void](Dot 21 50)
[void](Path @(21, 50, 21, 59, 30, 59, 30, 54))
[void](Label 31 54.5 4 4 '*' 8 0)
Mul 30 50
[void](Line 34 50 41 50)
[void](Box 41 45.5 19 9 $ST.STORE ("寄存 4 级`n（对齐）") $false 6.5)
# 64-point sliding sum: S(n) = S(n-1) + x(n) - x(n-64)
[void](Box 62 44 36 22 $null '' $true 7)
[void](Label 62 61 36 4.5 '64 点滑动求和' 7 1)
[void](Line 60 50 76 50)
[void](Dot 68 50)
[void](Line 68 50 68 54.5)
$z = Box 64 54.5 8 6 $ST.STORE 'Z-64' $false 7; Sup $z 1 4
[void](Path @(72, 57.5, 74.5, 57.5, 77.2, 52.8))
[void](Label 71.5 51.5 3 3 '−' 7 1)
Add 80 50
[void](Line 84 50 110 50 $false)
[void](Dot 92 50)
[void](Line 92 50 92 54.5)
$z = Box 88 54.5 8 6 $ST.STORE 'Z-1' $false 7; Sup $z 1 3
[void](Path @(88, 57.5, 85.5, 57.5, 82.8, 52.8))
[void](Path @(110, 50, 110, 38, 124, 38))
Tag 99 45 8 'E'

# ---------------------------------------------------------------- data FIFO
[void](Line 17 72 60 72)
[void](Box 60 67.5 30 9 $ST.STORE ("FIFO 128`n（等待定时判决）") $false 6.5)
[void](Line 90 72 137 72)

# ---------------------------------------------------------------- decision and output
[void](Box 124 12 36 30 $ST.COMPUTE ("判决`n|X(n−1)|² + |X(n)|² > E/4 + E/8`nE > 2500`n状态机：空闲 / 放行`n放行 2×64 + 683×80 个采样") $false 6.5)
Demux 137 10 72 10 18 'DEMUX'
[void](Line 142 42 142 65)
[void](Line 147 67.5 160 67.5); Tag 147 62.5 13 '放行'
[void](Label 160 64.5 20 6 '去 CP → FFT' 7 0)
[void](Line 147 76.5 160 76.5); Tag 147 71.5 13 '空闲'
[void](Box 160 72.5 12 8 $ST.STORE '丢弃' $false 7)

[void](Label 2 84 178 6 ("* 表示取共轭；FIR 系数为 LTF 匹配滤波器循环移位 9 个抽头，使 FFT 窗口落在 CP 内 8 个采样处；三个 FIR 共用一个 FIR Compiler（3 组系数）。") 6.5 0 $ST.MUTE)

Save-Figure (Join-Path $here 'sync_hw_ts.vsdx') (Join-Path $repo 'report\figures\hw\ts_hw.png')
'saved'




