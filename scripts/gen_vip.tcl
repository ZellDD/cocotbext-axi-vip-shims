# gen_vip.tcl -- generate the AXI4-Stream VIP cores that
# test/axi_vip_shims/axi_stream_vip_shims/hdl/tb_top.sv instantiates.
#
#   vivado -mode batch -source gen_vip.tcl -tclargs <out_dir>
#
# Creates <out_dir>/axi4stream_vip_mst/sim and <out_dir>/axi4stream_vip_slv/sim
# (the dirs AXIS_VIP_IP_DIRS points at). The interface must match tb_top.sv,
# the shim parameters and IFACE in test_cases/test_axis_vip.py:
#   TDATA 4 bytes, TKEEP, TSTRB, TLAST, TID/TDEST/TUSER 4 bits

set out_dir [file normalize [lindex $argv 0]]
if {$out_dir eq ""} { error "usage: -tclargs <out_dir>" }

file delete -force $out_dir
file mkdir $out_dir
# No -part: Vivado picks a default from the installed devices, and the VIP has
# no device primitives, so any part generates the same simulation sources.
create_project -in_memory
puts "gen_vip: part [get_property PART [current_project]], output $out_dir"

set iface {
    CONFIG.TDATA_NUM_BYTES 4
    CONFIG.HAS_TREADY      1
    CONFIG.HAS_TKEEP       1
    CONFIG.HAS_TSTRB       1
    CONFIG.HAS_TLAST       1
    CONFIG.TID_WIDTH       4
    CONFIG.TDEST_WIDTH     4
    CONFIG.TUSER_WIDTH     4
}

foreach {name mode} {axi4stream_vip_mst MASTER axi4stream_vip_slv SLAVE} {
    create_ip -name axi4stream_vip -vendor xilinx.com -library ip \
              -module_name $name -dir $out_dir
    set_property -dict [concat [list CONFIG.INTERFACE_MODE $mode] $iface] \
                 [get_ips $name]
    generate_target simulation [get_ips $name]
}

puts "gen_vip: done"
