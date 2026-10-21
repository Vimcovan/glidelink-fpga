# RX synchronisation (synchronizer_top: frame_detection + time_sync + cp_remover) with a file stimulus.
#   vivado -mode batch -source run.tcl -tclargs <tag>      reads stim_<tag>.txt, writes log_<tag>.txt
# The first run builds sim/phy/sync/prj/ (IP output products: CORDIC, DDS, FIR Compiler); later runs reuse it.
set tag [lindex $argv 0]
set S [file normalize [file dirname [info script]]]
set R [file normalize $S/../../../src/hw/rx]
set work $S/prj
if {![file exists $work/sync.xpr]} {
  create_project sync $work -part xczu5eg-sfvc784-2-e
  file copy -force $R/coe $work/coe
  set rtl {}
  foreach f {synchronizer_top frame_detection time_sync cp_remover axis_broadcast axis_combine axis_ram_srl complex_multiplier slide_win_sum} { lappend rtl $R/rtl/phy/ofdm_rx/$f.sv }
  foreach f {axis_fifo axis_forward_register fwft_fifo} { lappend rtl $R/rtl/phy/ofdm_tx/$f.sv }
  add_files -norecurse $rtl
  foreach n {cordic_block dds_freq_correct xcorr_cplx_block} {
    set d $work/ip_srcs/sources_1/ip/$n; file mkdir $d
    file copy -force $R/ip/$n/$n.xci $d/
    add_files -norecurse $d/$n.xci
  }
  generate_target all [get_ips]
  add_files -fileset sim_1 -norecurse $S/tb_sync.sv
  set_property top tb_sync [get_filesets sim_1]
  set_property -name {xsim.simulate.runtime} -value {all} -objects [get_filesets sim_1]
} else { open_project $work/sync.xpr }
set_property -name {xsim.simulate.xsim.more_options} -value "-testplusarg STIM=$S/stim_$tag.txt -testplusarg LOG=$S/log_$tag.txt" -objects [get_filesets sim_1]
launch_simulation
close_sim
