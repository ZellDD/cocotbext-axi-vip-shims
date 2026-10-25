# cocotbext-axi-vip-shims -- top level, so CMake can be run from the repo root:
#
#   cmake -B build
#   cmake --build build
#
# The libraries are defined in src/.
cmake_minimum_required(VERSION 3.16)
project(cocotbext_axi_vip_shims LANGUAGES C CXX)

add_subdirectory(src)
