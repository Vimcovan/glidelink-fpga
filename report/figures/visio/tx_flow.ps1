# TX main program (tx_camera.py) and the JPEG quality search (sscc_pkt.JpegFit).
#   powershell -ExecutionPolicy Bypass -File tx_flow.ps1   -> tx_flow.vsdx + ../sw/tx_flow.png
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'visio_style.ps1')
$repo = Resolve-Path (Join-Path $here '..\..\..')
$ST.SIZE = 7

Open-Visio 180 160
function Yes($x, $y) { [void](Label $x $y 8 4.5 '是' 6.5 0 $ST.MUTE) }
function No($x, $y) { [void](Label $x $y 8 4.5 '否' 6.5 0 $ST.MUTE) }

# ---------------------------------------------------------------- (a) main program
[void](Label 2 1 110 6 '(a) TX 主程序 tx_camera.py' 9 0 $ST.INK $true)
$cx = 50
[void](FTerm $cx 9 7 'systemd 启动 jscc-tx.service')
[void](Line $cx 16 $cx 20)
$b1 = FProc $cx 20 10 ("加载比特流 JsccTx()，切到 PRBS 源`n统计 1 s 内发往 DAC 的帧数 r") 'INIT'
[void](Line $cx 30 $cx 33)
$d1 = FDec $cx 39 34 12 'r > 300 帧/s ?'
$xl = $b1.L - 6
[void](Path @($d1.L, 39, $xl, 39, $xl, 25, $b1.L, 25))
[void](Label ($xl + 1) 33 14 5 '否，重载' 6.5 0 $ST.MUTE)
[void](Line $cx 45 $cx 49); Yes ($cx + 1) 44.5
[void](FProc $cx 49 10 ("切回编码器，读 TX 衰减；启动线程：`n三个数据源、控制、衰减、预览"))
[void](Line $cx 59 $cx 63)
$b3 = FProc $cx 63 9 ("从当前数据源取序号更新的一帧`n图像 img（256×256×3）")
[void](Line $cx 72 $cx 75)
[void](FDec $cx 81 40 12 'GUI 要求切换模式？')
$xa = $cx + 36
[void](Path @(($cx + 20), 81, $xa, 81, $xa, 88)); Yes ($cx + 21) 76
$ba = FProc $xa 88 13 ("切到 SSCC 前等 60 ms`n写控制寄存器的模式位`nPL 在帧边界切换") 'ALT' 6.5
[void](Line $cx 87 $cx 98); No ($cx + 1) 87.5
[void](FDec $cx 104 30 12 'SSCC 模式？')
[void](Path @($xa, $ba.B, $xa, 104, ($cx + 15), 104))
$xs = $cx - 33
[void](Path @(($cx - 15), 104, $xs, 104, $xs, 113)); Yes ($cx - 29) 99
$bs = FProc $xs 113 13 ("JPEG 自适应压缩`n打包（16360 B）`n送图像 DMA") 'ALT' 6.5
[void](Line $cx 110 $cx 115); No ($cx + 1) 109.5
[void](FProc ($cx + 6) 115 11 ("send_image(img)`nMM2S 196608 B，受编码器`n反压，收完才返回") 'PROC' 6.5)
[void](Path @($xs, $bs.B, $xs, 131, $cx, 131))
[void](Line $cx 126 $cx 134)
$b6 = FProc $cx 134 9 ("预览：帧号与采集时间戳立即发，`n原图交给预览线程（只留最新一帧）") 'PROC' 6.5
$xr = [math]::Max($ba.R, $b6.R) + 5
[void](Path @($b6.R, 138.5, $xr, 138.5, $xr, 67.5, $b3.R, 67.5))
[void](Label ($xr - 11) 116 10 9 '下一帧' 6.5 2 $ST.MUTE)

# ---------------------------------------------------------------- (b) JPEG quality search
[void](Label 114 1 66 6 '(b) JpegFit：JPEG 质量自适应' 9 0 $ST.INK $true)
$cx = 146
[void](FTerm $cx 9 7 'q ← 上一帧的质量因子')
[void](Line $cx 16 $cx 20)
$e1 = FProc $cx 20 8 'buf = imencode(img, q)'
[void](Line $cx 28 $cx 31)
[void](FDec $cx 38 44 14 ("len(buf) ≤ 12260`n或 q ≤ 5 ?"))
[void](Path @(($cx + 22), 38, ($cx + 30), 38, ($cx + 30), 24, $e1.R, 24))
[void](Label ($cx + 14) 29 15 5 'q ← q − 5' 6.5 2 $ST.MUTE)
[void](Line $cx 45 $cx 50); Yes ($cx + 1) 45
[void](FDec $cx 57 44 14 ("q 未下调过`n且 q < 95 ?"))
[void](Line $cx 64 $cx 69); Yes ($cx + 1) 64
[void](FProc $cx 69 9 '试 q + 5：放得下就采用')
[void](FTerm $cx 86 8 '记下 q，返回 (JPEG, q)')
[void](Path @(($cx - 22), 57, ($cx - 29), 57, ($cx - 29), 82, $cx, 82))
[void](Label ($cx - 29) 52 8 4.5 '否' 6.5 0 $ST.MUTE)
[void](Line $cx 78 $cx 86)
[void](Label 116 100 62 30 ("包格式（16360 字节）：`n魔数 'SC'、帧号、JPEG 长度、`n质量因子、CRC32（12 字节），`nJPEG 数据，其余补零。`n`n每帧 q 最多变化一档，又能跟上`n画面复杂度的变化。") 7 0 $ST.INK)

Save-Figure (Join-Path $here 'tx_flow.vsdx') (Join-Path $repo 'report\figures\sw\tx_flow.png')
'saved'
