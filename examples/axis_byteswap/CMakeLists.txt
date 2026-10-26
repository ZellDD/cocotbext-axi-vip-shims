# axis_byteswap example -- builds libaxis_dpi.so from the shims checkout into
# this project's build/lib/:
#
#   cmake -B build
#   cmake --build build
#
# (test_byteswap_runner.py does both for you.) In your own project, point
# AXI_VIP_SHIMS_DIR at wherever the shims live, e.g. a git submodule:
#
#   cmake -B build -DAXI_VIP_SHIMS_DIR=third_party/cocotbext-axi-vip-shims
cmake_minimum_required(VERSION 3.16)
project(axis_byteswap_example LANGUAGES CXX)

set(AXI_VIP_SHIMS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.."
    CACHE PATH "cocotbext-axi-vip-shims checkout")
get_filename_component(AXI_VIP_SHIMS_DIR "${AXI_VIP_SHIMS_DIR}" ABSOLUTE
                       BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")

# Set as cache entries before add_subdirectory, so the shims' own defaults
# (their lib/, plus the mock-simulator test library) don't apply.
set(AXI_VIP_SHIMS_LIB_DIR "${CMAKE_BINARY_DIR}/lib"
    CACHE PATH "Where libaxis_dpi.so is written")
set(AXI_VIP_SHIMS_BUILD_TEST_LIBS OFF CACHE BOOL "")

add_subdirectory("${AXI_VIP_SHIMS_DIR}/src" axi_vip_shims)
