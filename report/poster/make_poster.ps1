# Builds report/poster/poster_en.pptx (A0 portrait, one slide) through PowerPoint COM, plus a PNG preview.
#   powershell -ExecutionPolicy Bypass -File make_poster.ps1 [-Name poster_en]
# Needs Microsoft PowerPoint (Windows). All figures come from the repository.
param([string]$Name = 'poster_en')            # output: <Name>.pptx and <Name>_preview.png
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..')
$out  = Join-Path $here "$Name.pptx"
$png  = Join-Path $here "${Name}_preview.png"
$img = @{
  psnr   = Join-Path $repo 'data\measurements\psnr_snr\k0802_v3_psnr_snr.png'
  visual = Join-Path $repo 'data\measurements\psnr_snr\k0802_v3_visual_h.png'
  cdf    = Join-Path $repo 'data\measurements\latency\latency_3seg_cdf.png'
  hw     = Join-Path $here 'assets\hardware.jpg'
  gui    = Join-Path $here 'assets\gui_phy_page.png'
}

# ---------------------------------------------------------------- style
function RGB($hex) { $r = [Convert]::ToInt32($hex.Substring(0,2),16); $g = [Convert]::ToInt32($hex.Substring(2,2),16); $b = [Convert]::ToInt32($hex.Substring(4,2),16); return $r + 256*$g + 65536*$b }
$BLUE  = RGB '0B4F8A'   # primary (DeepJSCC-Q curve colour family)
$BLUE2 = RGB '2F6FA8'
$ORNG  = RGB 'D9531E'   # SSCC accent
$INK   = RGB '1F2933'
$MUTE  = RGB '52606D'
$CARD  = RGB 'EEF3F8'
$LINE  = RGB 'B8C7D6'
$WHITE = RGB 'FFFFFF'
$FONT  = 'Calibri'
$BODY  = 26; $SMALL = 21; $HEAD = 40

$W = 2384; $H = 3370                         # A0 portrait in points
$M = 72; $G = 48; $CW = [math]::Floor(($W - 2*$M - 2*$G) / 3)
$X1 = $M; $X2 = $M + $CW + $G; $X3 = $M + 2*($CW + $G)

# ---------------------------------------------------------------- PowerPoint
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Add(0)
$pres.PageSetup.SlideWidth = $W
$pres.PageSetup.SlideHeight = $H
$s = $pres.Slides.Add(1, 12)                 # ppLayoutBlank
$sh = $s.Shapes

function Box($x, $y, $w, $h, $fill, $round = $true, $name = '') {
  $b = $sh.AddShape($(if ($round) {5} else {1}), $x, $y, $w, $h)
  if ($round) { $b.Adjustments.Item(1) = [math]::Min(0.06, 18.0 / [math]::Min($w, $h)) }
  $b.Fill.ForeColor.RGB = $fill; $b.Line.Visible = 0; $b.Shadow.Visible = 0
  if ($name) { $b.Name = $name }
  return $b
}
function Txt($x, $y, $w, $h, $text, $size = $BODY, $color = $INK, $bold = $false, $align = 1, $name = '') {
  $t = $sh.AddTextbox(1, $x, $y, $w, $h)
  $t.TextFrame2.AutoSize = 0; $t.TextFrame.WordWrap = -1
  $t.TextFrame.MarginLeft = 0; $t.TextFrame.MarginRight = 0; $t.TextFrame.MarginTop = 0; $t.TextFrame.MarginBottom = 0
  $r = $t.TextFrame.TextRange; $r.Text = $text
  $r.Font.Name = $FONT; $r.Font.Size = $size; $r.Font.Color.RGB = $color; $r.Font.Bold = [int]$bold
  $r.ParagraphFormat.Alignment = $align
  if ($name) { $t.Name = $name }
  return $t
}
function Bullets($x, $y, $w, $h, $items, $size = $BODY) {
  $t = Txt $x $y $w $h ($items -join "`r") $size
  $r = $t.TextFrame.TextRange
  $r.ParagraphFormat.Bullet.Visible = -1; $r.ParagraphFormat.Bullet.Character = 8226
  $r.ParagraphFormat.Bullet.Font.Color.RGB = $BLUE
  $r.ParagraphFormat.SpaceAfter = [math]::Round($size * 0.45)
  $t.TextFrame.Ruler.Levels.Item(1).FirstMargin = 0; $t.TextFrame.Ruler.Levels.Item(1).LeftMargin = [math]::Round($size * 0.9)
  return $t
}
function Bold($t, $word) {                     # bold every occurrence of $word inside text box $t
  $r = $t.TextFrame.TextRange; $start = 1
  while ($true) { $f = $r.Find($word, $start - 1); if (-not $f) { break }; $f.Font.Bold = -1; $start = $f.Start + $f.Length; if ($start -gt $r.Length) { break } }
}
function Card($x, $y, $w, $h, $num, $title) {
  [void](Box $x $y $w $h $CARD $true "card $num")
  $c = $sh.AddShape(9, ($x + 28), ($y + 26), 56, 56)          # numbered circle
  $c.Fill.ForeColor.RGB = $BLUE; $c.Line.Visible = 0; $c.Shadow.Visible = 0; $c.Name = "badge $num"
  $cr = $c.TextFrame.TextRange; $cr.Text = "$num"; $cr.Font.Name = $FONT; $cr.Font.Size = 28; $cr.Font.Bold = -1; $cr.Font.Color.RGB = $WHITE
  $c.TextFrame.MarginLeft = 0; $c.TextFrame.MarginRight = 0
  [void](Txt ($x + 104) ($y + 26) ($w - 132) 60 $title $HEAD $BLUE $true 1 "title $num")
}
function Pic($path, $x, $y, $w) {
  $p = $sh.AddPicture($path, 0, -1, $x, $y)
  $p.LockAspectRatio = -1; $p.Width = [single]$w       # COM wants a Single (an Int32 fails)
  return $p
}
function Arrow($x1, $y1, $x2, $y2, $color = $MUTE) {
  $a = $sh.AddConnector(1, $x1, $y1, $x2, $y2)
  $a.Line.ForeColor.RGB = $color; $a.Line.Weight = 4; $a.Line.EndArrowheadStyle = 2
  return $a
}
function Node($x, $y, $w, $h, $l1, $l2, $fill = $WHITE, $edge = $BLUE) {
  $b = $sh.AddShape(5, $x, $y, $w, $h); $b.Adjustments.Item(1) = 0.12
  $b.Fill.ForeColor.RGB = $fill; $b.Line.ForeColor.RGB = $edge; $b.Line.Weight = 2.5; $b.Shadow.Visible = 0
  $tf = $b.TextFrame; $tf.MarginLeft = 6; $tf.MarginRight = 6; $tf.MarginTop = 2; $tf.MarginBottom = 2; $tf.WordWrap = -1
  $r = $tf.TextRange; $r.Text = "$l1`r$l2"; $r.Font.Name = $FONT; $r.Font.Color.RGB = $INK; $r.ParagraphFormat.Alignment = 2
  $r.Paragraphs(1).Font.Size = 23; $r.Paragraphs(1).Font.Bold = -1; $r.Paragraphs(2).Font.Size = 19; $r.Paragraphs(2).Font.Color.RGB = $MUTE
  return $b
}
function Table($x, $y, $w, $rows, $colw, $rowh = 52, $size = 22) {
  $nr = $rows.Count; $nc = $rows[0].Count
  $tb = $sh.AddTable($nr, $nc, $x, $y, $w, $nr * $rowh)
  $t = $tb.Table
  for ($j = 1; $j -le $nc; $j++) { $t.Columns.Item($j).Width = $colw[$j-1] }
  for ($i = 1; $i -le $nr; $i++) {
    for ($j = 1; $j -le $nc; $j++) {
      $cell = $t.Cell($i, $j).Shape
      $cell.Fill.ForeColor.RGB = $(if ($i -eq 1) { $BLUE } elseif ($i % 2 -eq 0) { $WHITE } else { RGB 'F7FAFC' })
      $tf = $cell.TextFrame; $tf.MarginLeft = 8; $tf.MarginRight = 8; $tf.MarginTop = 4; $tf.MarginBottom = 4; $tf.VerticalAnchor = 3
      $r = $tf.TextRange; $r.Text = [string]$rows[$i-1][$j-1]
      $r.Font.Name = $FONT; $r.Font.Size = $size; $r.Font.Color.RGB = $(if ($i -eq 1) { $WHITE } else { $INK }); $r.Font.Bold = [int]($i -eq 1 -or $j -eq 1)
      $r.ParagraphFormat.Alignment = $(if ($j -eq 1) { 1 } else { 2 })
    }
    $t.Rows.Item($i).Height = $rowh
  }
  return $tb
}

# ---------------------------------------------------------------- header
$title = Txt $M 64 ($W - 2*$M) 110 'Real-Time Over-the-Air Image Transmission with DeepJSCC-Q on FPGA' 70 $BLUE $true 1 'poster title'
[void](Txt $M 166 ($W - 2*$M) 100 ('Separate source and channel coding (JPEG + FEC) fails abruptly once the SNR drops below its code threshold; DeepJSCC-Q maps pixels straight to 64-QAM symbols and degrades gracefully. The CNN codec, an 802.11a-like OFDM PHY and the RF front-end control all run on two Zynq UltraScale+ boards.') 30 $MUTE $false 1 'subtitle')

$stats = @(
  @('30 fps',     ('256' + [char]0x00D7 + '256 RGB images over a radio link')),
  @('71.5 ms',    'PL latency: encode 38.2 + air 2.9 + decode 30.4 ms'),
  @('+3.8 dB',    'PSNR over JPEG + convolutional code, with no cliff'),
  @('2.33 GMAC',  'per frame, 114 convolutions, W8A12 fixed point'),
  @('3.1 / 3.7 W','TX / RX measured power (PL + PS + DDR rails)')
)
$sw = ($W - 2*$M - 4*36) / 5
for ($k = 0; $k -lt 5; $k++) {
  $x = $M + $k * ($sw + 36)
  [void](Box $x 290 $sw 230 $BLUE $true "stat $k")
  [void](Txt ($x + 30) 306 ($sw - 60) 100 $stats[$k][0] 74 $WHITE $true 1 "stat value $k")
  [void](Txt ($x + 30) 414 ($sw - 60) 100 $stats[$k][1] 25 (RGB 'D6E4F2') $false 1 "stat label $k")
}

$Y0 = 570
# ---------------------------------------------------------------- column 1
# 1 hardware
Card $X1 $Y0 $CW 560 1 'Hardware'
$p = Pic $img.hw ($X1 + 36) ($Y0 + 104) ($CW - 72)
[void](Txt ($X1 + 36) ($Y0 + 104 + $p.Height + 12) ($CW - 72) 60 ('Left: RX (PYNQ-ZU + AD9361 + touch screen). Right: TX (PYNQ-ZU + AD9361 + USB camera). ' +
  'Antennas in front.') $SMALL $MUTE)

# 2 system
$yB = $Y0 + 590
Card $X1 $yB $CW 670 2 'System'
$nw = 300; $nh = 84; $gap = 28; $xa = $X1 + 36; $xb = $X1 + $CW - 36 - $nw; $yy = $yB + 120
[void](Txt $xa ($yB + 96) $nw 30 'TX board' 22 $MUTE $true 2); [void](Txt $xb ($yB + 96) $nw 30 'RX board' 22 $MUTE $true 2)
$yy = $yB + 134
$tx = @(@('Camera / video', ('PS: crop to 256' + [char]0x00D7 + '256')), @('DeepJSCC-Q encoder', 'PL, W8A12, 250 MHz'),
        @('OFDM transmitter', 'PN flip, IFFT, frame buffer'), @('AD9361', '20 MSPS'))
$rx = @(@('Touch-screen GUI', 'PS: display, control'), @('DeepJSCC-Q decoder', 'soft I/Q in, no hard decision'),
        @('OFDM receiver', ('sync, 2' + [char]0x00D7 + 'LTF CE, SFO / CPE')), @('AD9361', 'fast AGC'))
for ($k = 0; $k -lt 4; $k++) {
  $y = $yy + $k * ($nh + $gap)
  $f = $(if ($k -eq 1) { RGB 'DCE8F4' } else { $WHITE })
  [void](Node $xa $y $nw $nh $tx[$k][0] $tx[$k][1] $f); [void](Node $xb $y $nw $nh $rx[$k][0] $rx[$k][1] $f)
  if ($k -lt 3) { [void](Arrow ($xa + $nw/2) ($y + $nh) ($xa + $nw/2) ($y + $nh + $gap)); [void](Arrow ($xb + $nw/2) ($y + $nh + $gap) ($xb + $nw/2) ($y + $nh)) }
}
$ya = $yy + 3 * ($nh + $gap) + $nh/2
[void](Arrow ($xa + $nw) $ya $xb $ya $ORNG)
[void](Txt ($xa + $nw) ($ya - 40) ($xb - $xa - $nw) 30 'air' 22 $ORNG $true 2)
[void](Txt $xa ($yy + 4*($nh+$gap) - 6) ($CW - 72) 70 ('One frame = one image = 32,768 64-QAM symbols = 683 OFDM symbols (2.75 ms on air). ' +
  'The coded image crosses the air link only; Ethernet carries control and the TX preview used for PSNR.') $SMALL $MUTE)

# 3 network
$yC = $yB + 680
Card $X1 $yC $CW 545 3 'DeepJSCC-Q network'
$enc = 'RG','RB','RG','ATT','RB','RG','RB','RG','ATT'
$dec = 'ATT','RB','RU','RB','RU','ATT','RB','RU','RB','RU'
function Chain($labels, $x0, $y0, $wtot, $fill) {
  $n = $labels.Count; $gp = 18; $bw = ($wtot - ($n - 1) * $gp) / $n; $out = @()
  for ($k = 0; $k -lt $n; $k++) {
    $b = $sh.AddShape(5, ($x0 + $k * ($bw + $gp)), $y0, $bw, 52); $b.Adjustments.Item(1) = 0.18
    $b.Fill.ForeColor.RGB = $fill; $b.Line.Visible = 0; $b.Shadow.Visible = 0
    $r = $b.TextFrame.TextRange; $r.Text = $labels[$k]; $r.Font.Name = $FONT; $r.Font.Size = 18; $r.Font.Bold = -1; $r.Font.Color.RGB = $WHITE
    $b.TextFrame.MarginLeft = 0; $b.TextFrame.MarginRight = 0
    if ($k -gt 0) {                                     # short arrow from the previous block
      $a = $sh.AddConnector(1, 0, 0, 10, 10)
      $a.ConnectorFormat.BeginConnect($out[$k - 1], 4); $a.ConnectorFormat.EndConnect($b, 2)
      $a.Line.ForeColor.RGB = $MUTE; $a.Line.Weight = 2.5; $a.Line.EndArrowheadStyle = 2
      $a.Line.EndArrowheadLength = 1; $a.Line.EndArrowheadWidth = 1
    }
    $out += $b
  }
  return ,$out
}
$xn = $X1 + 36; $wn = $CW - 72
[void](Txt $xn ($yC + 100) $wn 30 'Encoder: 256x256x3 image -> 64x64x16 latent (55 conv)' 21 $MUTE $true)
$eb = Chain $enc $xn ($yC + 134) $wn $BLUE
$db = Chain $dec $xn ($yC + 254) $wn $BLUE2
$el = $sh.AddConnector(2, 0, 0, 10, 10)                 # encoder output -> channel -> decoder input
$el.ConnectorFormat.BeginConnect($eb[$eb.Count - 1], 3); $el.ConnectorFormat.EndConnect($db[0], 1)
$el.Line.ForeColor.RGB = $ORNG; $el.Line.Weight = 3; $el.Line.EndArrowheadStyle = 2
$el.Line.EndArrowheadLength = 2; $el.Line.EndArrowheadWidth = 2
$pl = $sh.AddShape(5, ($xn + ($wn - 470) / 2), ($yC + 202), 470, 36); $pl.Adjustments.Item(1) = 0.5
$pl.Fill.ForeColor.RGB = $WHITE; $pl.Line.ForeColor.RGB = $ORNG; $pl.Line.Weight = 2; $pl.Shadow.Visible = 0
$pl.TextFrame.MarginTop = 0; $pl.TextFrame.MarginBottom = 0
$r = $pl.TextFrame.TextRange; $r.Text = '32,768 64-QAM symbols (0.5 symbol / pixel)'
$r.Font.Name = $FONT; $r.Font.Size = 20; $r.Font.Bold = -1; $r.Font.Color.RGB = $ORNG
[void](Txt $xn ($yC + 314) $wn 30 'Decoder: soft I/Q -> image (59 conv)' 21 $MUTE $true)
[void](Txt $xn ($yC + 346) $wn 30 'RG/RB residual (+GDN), ATT attention, RU PixelShuffle (+IGDN)' 18 $MUTE)
$b = Bullets $xn ($yC + 386) $wn 220 @(
  ('C = 32, 16 latent channels, 4' + [char]0x00D7 + ' down-sampling: same 32,768 symbols as [2] with 1/51 of its MACs; trained at 10 dB AWGN with a constellation-entropy (KL) penalty.'),
  'W8A12 post-training quantization: 0.05 dB below float (DIV2K); RTL bit-exact against the integer model.') 23

# 4 PHY and RF
$yD = $yC + 575
Card $X1 $yD $CW ($H - 230 - $yD) 4 'OFDM PHY and RF front end'
$b = Bullets ($X1 + 36) ($yD + 104) ($CW - 72) 300 @(
  '64-pt FFT, 48 data + 4 pilots; PN sign flip: 99 % PAPR 10.1-10.7 -> 9.4-9.8 dB.',
  'Second-order SFO / CPE loop, no steady-state error under a 40 ppm ramp: frame-end EVM within 1.7 dB of the start (683 symbols).',
  'AD9361: SPI handshake fixed, TX quadrature-calibration phase search in PL, FIR redesigned: loopback EVM -22 -> -32 dB.',
  ('Fast AGC locked at -14 dBFS with a PL-counter watchdog; PHY alone: 0 errors in 7.15' + [char]0x00D7 + '10^8 bits.')) 23
$gw = 490
$p = Pic $img.gui ($X1 + ($CW - $gw) / 2) ($yD + 432) $gw
$p.Line.Visible = -1; $p.Line.ForeColor.RGB = $LINE; $p.Line.Weight = 1.5
[void](Txt ($X1 + 36) ($yD + 432 + $p.Height + 6) ($CW - 72) 30 'Live RX GUI: constellations, |H| vs time (Mali-400 GPU), per-subcarrier SNR, spectrum' 18 $MUTE $false 2)

# ---------------------------------------------------------------- column 2: engine
Card $X2 $Y0 $CW 1060 5 'Layer-streaming CNN engine'
$b = Bullets ($X2 + 36) ($Y0 + 110) ($CW - 72) 360 @(
  'One engine per layer, chained by AXI-Stream with back-pressure: only line buffers, no frame storage.',
  'Output-channel parallelism P chosen per layer: the smallest P that meets 30 fps at 250 MHz.',
  'Branches that read the same input share one window reader; weights in LUT, BRAM or packed BRAM + gearbox; large activation buffers in URAM.') 23
$pw = 140; $pg = ($CW - 72 - 4*$pw) / 3; $py = $Y0 + 400
$pipe = @(@('Line buffer', 'K or K+1 rows'), @('Window', ('K' + [char]0x00D7 + 'K' + [char]0x00D7 + 'Cin')), @('P MAC lanes', 'weights x P'), @('Requantize', 'bias, act., shift'))
for ($k = 0; $k -lt 4; $k++) {
  $px = $X2 + 36 + $k * ($pw + $pg)
  [void](Node $px $py $pw 96 $pipe[$k][0] $pipe[$k][1] $(if ($k -eq 2) { RGB 'DCE8F4' } else { $WHITE }))
  if ($k -lt 3) { [void](Arrow ($px + $pw) ($py + 48) ($px + $pw + $pg) ($py + 48)) }
}
[void](Txt ($X2 + 36) ($py + 104) ($CW - 72) 30 'per layer; groups of P output channels re-read the window' 19 $MUTE $false 2)
[void](Txt ($X2 + 36) ($Y0 + 570) ($CW - 72) 34 'Network sized for on-chip storage (256x256 input)' 24 $BLUE $true)
[void](Table ($X2 + 36) ($Y0 + 610) ($CW - 72) @(
  @('', 'DeepJSCC-Q [2]', 'This work'),
  @('Channels / down-sampling', '256 / 16x', '32 / 4x'),
  @('Conv. MAC enc. / dec.', '47.7 / 70.5 G', '0.91 / 1.42 G'),
  @('Parameters', '34.4 M', '387 k'),
  @('Kodak PSNR, 10 dB', '34.9 dB', '33.3 dB')) @(283, 180, 180) 44 21)
[void](Txt ($X2 + 36) ($Y0 + 842) ($CW - 72) 34 'Full TX / RX designs (xczu5eg, routed)' 24 $BLUE $true)
[void](Table ($X2 + 36) ($Y0 + 882) ($CW - 72) @(
  @('', 'LUT', 'BRAM36', 'URAM', 'DSP', 'WNS'),
  @('TX', '52.7k', '104.5', '10', '243', '+0.26'),
  @('RX', '70.8k', '123.5', '25', '394', '+0.23')) @(83, 120, 125, 100, 100, 115) 50 21)

# ---------------------------------------------------------------- column 3: latency, comparison
Card $X3 $Y0 $CW 480 6 'Latency and throughput'
$p = Pic $img.cdf ($X3 + 40) ($Y0 + 104) ($CW - 80)
$b = Bullets ($X3 + 36) ($Y0 + 104 + $p.Height + 20) ($CW - 72) 150 @(
  'Medians from PL frame counters on both boards (1701 frames).',
  '30.0 fps sustained: 18,000 frames received in 600 s.') 22

$yK = $Y0 + 510
Card $X3 $yK $CW 550 7 'vs. FPGA DeepJSCC [3]'
[void](Table ($X3 + 36) ($yK + 108) ($CW - 72) @(
  @('', '[3] GLOBECOM 25', 'This work'),
  @('Conv. work / frame', '0.22 GMAC', '2.33 GMAC (10.5x)'),
  @('DSP enc. / dec.', '1597 / 874', '233 / 312'),
  @('BRAM enc. / dec.', '1039 / 1012', '100 / 100'),
  @('Latency enc. / dec.', '39.6 / 52.9 ms *', '38.2 / 30.4 ms'),
  @('PHY', 'external 5G', 'own OFDM in PL')) @(215, 213, 215) 50 21)
[void](Txt ($X3 + 36) ($yK + 420) ($CW - 72) 90 ('Network cores; different devices and precision (INT8 vs W8A12): absolute counts, not a controlled comparison. ' +
  '* median including PS processing; ours: PL only.') 18 $MUTE)

# ---------------------------------------------------------------- span: PSNR-SNR + visual comparison
$yR = $Y0 + 1090
$wv = 2*$CW + $G
Card $X2 $yR $wv ($H - 230 - $yR) 8 'Graceful degradation, no cliff (measured over the air)'
$pp = Pic $img.psnr ($X2 + 40) ($yR + 104) 600
$b = Bullets ($X2 + 680) ($yR + 120) ($wv - 720) ($pp.Height - 20) @(
  'DeepJSCC-Q: 32.0 dB at 32 dB SNR and still 26.5 dB at 5.5 dB - the image quality follows the channel.',
  'SSCC (JPEG + K=7 convolutional code + 64-QAM) on the same PHY with the same 32,768 symbols per image: 28.2 dB, lost within 3 dB (SNR 20 -> 17 dB).',
  'With the same channel-symbol budget, DeepJSCC-Q is 3.8 dB better even at high SNR.',
  'SNR is data-aided: received constellation against the known reference of a still image (DIV2K 0802), attenuator swept from 32 dB down.',
  'Below: decoded images from the same sweep - rows original, DeepJSCC-Q, SSCC; PSNR / SSIM under each image; an undecodable SSCC frame is scored as mid-gray.') 24
$p = Pic $img.visual ($X2 + 40) ($yR + 104 + $pp.Height + 24) ($wv - 80)

# ---------------------------------------------------------------- footer
$yF = $H - 196
[void](Txt $M $yF ($W - 2*$M) 60 ('Developed with AI coding agents: Claude Code (PHY, RF, PYNQ software, measurements) and Codex (training, quantization, RTL generator); ' +
  'the human set the goals and constraints, made the key technical calls and did all hardware handling; the two models cross-reviewed the report. ' +
  'Hardware: 2' + [char]0x00D7 + ' PYNQ-ZU (xczu5eg) + 2' + [char]0x00D7 + ' AD-FMCOMMS3, Vivado 2025.2. Open source (MIT).') 20 $INK)
[void](Txt $M ($yF + 96) ($W - 2*$M) 80 ('[1] E. Bourtsoulatze, D. B. Kurka, D. Gunduz, IEEE TCCN, 2019.   ' +
  '[2] T.-Y. Tung, D. B. Kurka, M. Jankowski, D. Gunduz, "DeepJSCC-Q," IEEE JSAIT, 2022.   ' +
  '[3] T. Isobe et al., "FPGA-based deep joint source-channel coding for real-time 5G image transmission," IEEE GLOBECOM, 2025.') 18 $MUTE)

# ---------------------------------------------------------------- save + preview
if (Test-Path $out) { Remove-Item $out }
$pres.SaveAs($out)
$s.Export($png, 'PNG', 2384, 3370)
$pres.Close()
$app.Quit()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
"saved $out"

