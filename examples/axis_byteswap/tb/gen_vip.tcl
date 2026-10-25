# gen_vip.tcl -- generate the two AXI4-Stream VIP cores tb_byteswap.sv uses.
#
#   vivado -mode batch -source gen_vip.tcl -tclargs <out_dir> [part]
#
# The runner calls this on first use (into build/ip). The interface must match
# the shim parameters in tb_byteswap.sv and IFACE in test_byteswap.py:
#   TDATA 4 bytes, TKEEP, TLAST, TUSER 1 bit (no TSTRB, TID, TDEST)
#
# The component names are this project's own, so the shim macros
# (AXIS_MST_VIP_PKG, ...) are overridden in test_byteswap_runner.py.

set out_dir [file normalize [lindex $argv 0]]
if {$out_dir eq ""} { error "usage: -tclargs <out_dir> \[part\]" }

# The VIP has no device primitives, so any installed part works.
set part [lindex $argv 1]
if {$part eq ""} {
    set part [lindex [concat [get_parts -quiet xc7a35tcpg236-1] [get_parts]] 0]
}

file delete -force $out_dir
file mkdir $out_dir
create_project -in_memory -part $part

set iface {
    CONFIG.TDATA_NUM_BYTES 4
    CONFIG.HAS_TREADY      1
    CONFIG.HAS_TKEEP       1
    CONFIG.HAS_TSTRB       0
    CONFIG.HAS_TLAST       1
    CONFIG.TID_WIDTH       0
    CONFIG.TDEST_WIDTH     0
    CONFIG.TUSER_WIDTH     1
}

foreach {name mode} {ex_src_vip MASTER ex_sink_vip SLAVE} {
    create_ip -name axi4stream_vip -vendor xilinx.com -library ip \
              -module_name $name -dir $out_dir
    set_property -dict [concat [list CONFIG.INTERFACE_MODE $mode] $iface] \
                 [get_ips $name]
    generate_target simulation [get_ips $name]
}
