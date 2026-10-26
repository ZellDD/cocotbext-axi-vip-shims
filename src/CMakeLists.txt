# axi_vip_shims -- DPI-C libraries for the cocotb <-> Xilinx VIP shims
#
#   cmake -B build      (from the repo root)
#   cmake --build build
#
# Libraries land in lib/ (AXI_VIP_SHIMS_LIB_DIR). Point the simulator's
# -sv_lib and cocotb's AXIS_DPI_LIB at the same file there.
cmake_minimum_required(VERSION 3.16)
project(axi_vip_shims LANGUAGES C CXX)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(AXI_VIP_SHIMS_LIB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../lib"
    CACHE PATH "Where the DPI-C shared libraries are written")
set(AXI_VIP_SHIMS_TEST_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../test")

option(AXI_VIP_SHIMS_BUILD_TEST_LIBS
       "Also build the simulator-free test libraries (DPI code + mock simulator)" ON)
option(AXI_VIP_SHIMS_TEST_COVERAGE
       "Build the test libraries with --coverage (gcov), and libaxis_dpi_cov.so for the simulation tests" ON)

add_subdirectory(axi_vip_shims/axi_stream_vip_shim)
