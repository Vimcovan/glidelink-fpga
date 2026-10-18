# Generate, simulate and (optionally) size the join FIFOs of a block range.
#   powershell -ExecutionPolicy Bypass -File sim\run_top_sim.ps1 -First enc.3 [-Last enc.3] [-Measure]
# -Measure: generate with large join FIFOs, simulate, record the peak occupancy of every
#           join FIFO into src/network/fifo_sizes.json (peak + margin), then regenerate sized.
# -Export / -GenDir: another export (default data\model) and generated-top directory (default src\network\gen),
#           relative to the repository, e.g. -Export data\model_shallow -GenDir src\network\gen_shallow.
param([string]$First = 'enc.3', [string]$Last = '', [switch]$Measure,
      [string]$Image = 'div2k_val_00', [int]$ValidPct = 90, [int]$ReadyPct = 80, [int]$Frames = 1,
      [string]$WorkDir = '', [string]$Export = 'data\model', [string]$GenDir = 'src\network\gen')
$ErrorActionPreference = 'Stop'
$viv  = $(if ($env:XILINX_VIVADO) { Join-Path $env:XILINX_VIVADO 'bin' } else { 'D:\AMDDesignTools\2025.2\Vivado\bin' })
$py   = $(if ($env:PYTHON) { $env:PYTHON } else { 'python' })
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# network library RTL: src/hw/{tx,rx}/rtl/network/rtl (each side holds the modules it uses)
function Rtl([string]$n) { foreach ($s in 'tx', 'rx') { $p = "$repo\src\hw\$s\rtl\network\rtl\$n.sv"; if (Test-Path $p) { return $p } }; throw "$n.sv not found" }
$export = (Join-Path $repo $Export) -replace '\\', '/'
$gen = Join-Path $repo $GenDir
$env:NET_GEN_DIR = $gen                                    # read by fifo_sizes.py
if ($Last -eq '') { $Last = $First }
$work = if ($WorkDir) { $WorkDir } else { Join-Path $PSScriptRoot ('work_top_' + ($First -replace '[.]', '_')) }
New-Item -ItemType Directory -Force $work | Out-Null

function Run-Sim([string]$tag) {
    $genArgs = @("$repo\src\network\gen_rtl_top.py", $First, $Last, '--export', $export, '--out', $gen)
    if ($tag -eq 'measure') { $genArgs += '--measure' }
    $msg = & $py @genArgs
    if ($LASTEXITCODE -ne 0) { throw 'gen_rtl_top failed' }
    $mod = ($msg -split ':')[0]
    $info = Get-Content "$gen\$mod.json" -Raw | ConvertFrom-Json
    Push-Location $work
    try {
        $src = @('sdp_ram', 'rom', 'rom_banked', 'axis_line_buffer', 'axis_pixel_buffer', 'mul_serial', 'weight_stream',
                 'conv_engine', 'axis_fork', 'axis_tag', 'axis_split', 'centre_tap', 'axis_fifo_packed', 'gdn_unit',
                 'axis_gdn', 'axis_add', 'axis_sigmoid', 'axis_gate', 'axis_pixel_shuffle', 'qam_tx', 'rx_frame', 'axis_pwl') | ForEach-Object { Rtl $_ }
        $vl = @('-sv') + ($src | ForEach-Object { '"' + $_ + '"' }) +
              @(('"' + "$gen\$mod.sv" + '"'), ('"' + "$PSScriptRoot\tb_top.sv" + '"'), '-d', "BLK=$mod")
        Set-Content -Path 'args_xvlog.txt' -Value ($vl -join ' ') -Encoding ascii
        & "$viv\xvlog.bat" -f args_xvlog.txt | Out-File out_xvlog.txt
        if ($LASTEXITCODE -ne 0) { Get-Content out_xvlog.txt | Select-String 'ERROR'; throw 'xvlog failed' }
        $inFile = if ([int]$info.IN_W -eq 24) { "$export/golden/$Image/rx_iq24.mem" } else { "$export/golden/$Image/$($info.in).mem" }
        $outFile = if ([int]$info.OUT_W -eq 24) { "$export/golden/$Image/tx_iq24.mem" } else { "$export/golden/$Image/$($info.out).mem" }
        $g = @("H=$($info.H)", "W=$($info.W)", "CIN=$($info.CIN)", "HO=$($info.HO)", "WO=$($info.WO)",
               "COUT=$($info.COUT)", "OUT_W=$($info.OUT_W)", "IN_W=$($info.IN_W)", "IN_ELEMS=$($info.IN_ELEMS)", "OUT_ELEMS=$($info.OUT_ELEMS)",
               "IN_FILE=$inFile", "OUT_FILE=$outFile",
               "VALID_PCT=$ValidPct", "READY_PCT=$ReadyPct", "NFRAMES=$Frames")
        $snap = "top_$mod"
        $opts = @('tb_top', '-s', $snap, '-timescale', '1ns/1ps', '-debug', 'off', '-L', 'xpm')
        foreach ($x in $g) { $opts += @('-generic_top', ('"' + $x + '"')) }
        Set-Content -Path "args_$snap.txt" -Value ($opts -join ' ') -Encoding ascii
        & "$viv\xelab.bat" -f "args_$snap.txt" | Out-File "out_xelab_$snap.txt"
        if ($LASTEXITCODE -ne 0) { Get-Content "out_xelab_$snap.txt" | Select-String 'ERROR' | Select-Object -First 10; throw 'xelab failed' }
        $t = Measure-Command { & "$viv\xsim.bat" $snap -R | Out-File "out_xsim_$snap.$tag.txt" }
        $simExit = $LASTEXITCODE
        $simText = Get-Content "out_xsim_$snap.$tag.txt" -Raw
        if ($simExit -ne 0 -or $simText -notmatch '(?m)^PASS\s' -or
            $simText -match '(?im)FAIL|TIMEOUT|MISMATCH|\bERROR\b|\bFATAL\b') {
            throw "Simulation failed or did not finish: $work\out_xsim_$snap.$tag.txt (exit $simExit)"
        }
        $res = Select-String -Path "out_xsim_$snap.$tag.txt" -Pattern 'PASS|FAIL|TIMEOUT|MISMATCH|ERROR|Error|Fatal|frame . done' |
               Select-Object -First 12 | ForEach-Object { $_.Line }
        Write-Output ("[{0} {1}] {2}   (sim {3:N0} s)" -f $mod, $tag, ($res -join ' | '), $t.TotalSeconds)
        return "$work\out_xsim_$snap.$tag.txt"
    } finally { Pop-Location }
}

if ($Measure) {
    $log = Run-Sim 'measure'
    $log = $log[-1]
    $peaks = Select-String -Path $log -Pattern 'FIFO_PEAK \S*u_fifo_([^.\s]+)\S* (\d+) of' | ForEach-Object {
        @{ inst = $_.Matches[0].Groups[1].Value; peak = [int]$_.Matches[0].Groups[2].Value } }
    & $py "$PSScriptRoot\fifo_sizes.py" $First $Last ($peaks | ForEach-Object { "$($_.inst)=$($_.peak)" })
    if ($LASTEXITCODE -ne 0) { throw 'fifo_sizes failed' }
}
Run-Sim 'sized' | Select-Object -First 1
