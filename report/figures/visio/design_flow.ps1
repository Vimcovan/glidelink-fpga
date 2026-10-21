# Design flow (report section 1.6): neural network and PHY / RF lanes built against one interface contract, then integration.
#   powershell -ExecutionPolicy Bypass -File design_flow.ps1   -> design_flow.vsdx + ../design_flow.png
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'visio_style.ps1')
$repo = Resolve-Path (Join-Path $here '..\..\..')
$ST.SIZE = 7

Open-Visio 206 84

$X0 = 28; $X1 = 198; $GAP = 4; $H = 17
function Lane($y, $fill, $steps) {
  # equal-width boxes from X0 to X1 joined by arrows; returns the right edge of the last box
  $n = $steps.Count; $w = ($X1 - $X0 - ($n - 1) * $GAP) / $n
  for ($i = 0; $i -lt $n; $i++) {
    $x = $X0 + $i * ($w + $GAP)
    [void](Box $x $y $w $H $fill $steps[$i] $false 6.5)
    if ($i -gt 0) { [void](Line ($x - $GAP) ($y + $H / 2) $x ($y + $H / 2)) }
  }
  return $w
}

# interface contract, shared by both lanes
[void](Box 2 8 20 44 $ST.GRAY ("接口约定`n（2.1.5节）`n`n每帧32,768个`n{Q, I}符号`n`nAXI-Stream`n12位I / Q") $false 6.5)
[void](Line 22 16.5 $X0 16.5)
[void](Line 22 43.5 $X0 43.5)

[void](Label $X0 2 80 5 '神经网络' 8 0 $ST.INK $true)
[void](Lane 8 $ST.COMPUTE @(
  "浮点模型训练`n期刊版DeepJSCC-Q`n10 dB AWGN",
  "W8A12整数量化`n整数参考模型`n量化损失≤0.07 dB",
  "子模块RTL编写与仿真`n卷积、行缓存、GDN`n逐模块逐比特比对",
  "并行度与存储规划`n每层取最小P`nBRAM36≤100块",
  "脚本顶层例化`n例化、传参、连线`n权重.mem文件",
  "整网仿真与综合`n与参考模型逐比特一致`n250 MHz时序收敛"))

[void](Label $X0 29 80 5 '物理层与射频' 8 0 $ST.INK $true)
[void](Lane 35 $ST.STORE @(
  "OFDM收发算法仿真`n帧检测、定时、SFO跟踪`n门限与环路参数",
  "RTL编写与仿真`n按算法模型逐模块实现`ntestbench验证",
  "AD9361接口与校准`nLVDS、SPI`nTX正交校准、FIR",
  "单板环回`nPRBS 64QAM测试源`n星座与EVM",
  "两板空口测试`nAGC、长帧SFO跟踪`n逐子载波SNR"))

# both lanes merge into system integration
[void](Path @(198, 16.5, 202, 16.5, 202, 56, 48, 56, 48, 64))
[void](Line 198 43.5 202 43.5 $false)
[void](Label $X0 58 60 5 '系统集成' 8 0 $ST.INK $true)
[void](Lane 64 $ST.GREEN @(
  "编解码器与PHY对接`n按接口约定直接连接`n编码 → 空口 → 解码",
  "PS软件与GUI`n取图送图、AGC看门狗`n触摸屏、手机控制页",
  "SSCC基线`nJPEG + LDPC`n同一PHY、同样符号数",
  "空口实测`nPSNR曲线、帧率`n时延、功耗"))

Save-Figure (Join-Path $here 'design_flow.vsdx') (Join-Path $repo 'report\figures\design_flow.png')
'saved'
