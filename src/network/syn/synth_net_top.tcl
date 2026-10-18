# Out-of-context synthesis of a generated network top (gen_rtl_top.py output) at 250 MHz.
#   vivado -mode batch -source synth_net_top.tcl -tclargs <generated top .sv> <out dir>
# The library modules come from src/hw/{rx,tx}/rtl/network/rtl (the deployed tops there are not read); the .mem
# files are referenced by the absolute paths written by gen_rtl_top.py.
set here [file normalize [file dirname [info script]]]
set repo [file normalize [file join $here .. .. ..]]
lassign $argv top_sv out
set top [file rootname [file tail $top_sv]]
file mkdir $out
set part xczu5eg-sfvc784-2-e

set lib {}
foreach side {rx tx} {
    foreach f [glob -nocomplain [file join $repo src hw $side rtl network rtl *.sv]] {
        set n [file tail $f]
        if {[string match blk_* $n]} continue
        if {[lsearch -exact [lmap x $lib {file tail $x}] $n] < 0} { lappend lib $f }
    }
}
create_project -in_memory -part $part
read_verilog -sv [concat $lib [list [file normalize $top_sv]]]
auto_detect_xpm
read_xdc [file join $here clk_250.xdc]
synth_design -top $top -part $part -mode out_of_context
report_utilization -file [file join $out $top.util.rpt]
report_utilization -hierarchical -hierarchical_depth 1 -file [file join $out $top.util_hier.rpt]
report_timing_summary -file [file join $out $top.timing.rpt]

set r36  [llength [get_cells -quiet -hier -filter {REF_NAME == RAMB36E2}]]
set r18  [llength [get_cells -quiet -hier -filter {REF_NAME == RAMB18E2}]]
set uram [llength [get_cells -quiet -hier -filter {REF_NAME == URAM288}]]
set dsp  [llength [get_cells -quiet -hier -filter {REF_NAME == DSP48E2}]]
set lut  [llength [get_cells -quiet -hier -filter {REF_NAME =~ LUT*}]]
set ff   [llength [get_cells -quiet -hier -filter {REF_NAME =~ FD*}]]
set wns  [get_property SLACK [lindex [get_timing_paths -quiet -max_paths 1 -setup] 0]]
puts "RESULT $top LUT $lut FF $ff BRAM36 [expr {$r36 + $r18 / 2.0}] (RAMB36 $r36 RAMB18 $r18) URAM $uram DSP $dsp WNS $wns"
