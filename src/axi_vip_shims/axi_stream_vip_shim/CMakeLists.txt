# axi_stream_vip_shim -- libaxis_dpi.so (+ libaxis_dpi_test.so)
#
# svGetScope/svSetScope and the SV exports (axis_mst_push, ...) stay undefined
# here: the simulator provides them when it loads the library. Do not link
# with -Wl,--no-undefined.

set(AXIS_DPI_SRC ${CMAKE_CURRENT_SOURCE_DIR}/src/dpi_c/axis_dpi.cpp)

function(axis_dpi_lib target)
    add_library(${target} SHARED ${ARGN})
    target_compile_options(${target} PRIVATE -Wall -Wextra)
    set_target_properties(${target} PROPERTIES
        POSITION_INDEPENDENT_CODE ON
        LIBRARY_OUTPUT_DIRECTORY "${AXI_VIP_SHIMS_LIB_DIR}")
endfunction()

# Loaded by the simulator (-sv_lib) and by cocotb (AXIS_DPI_LIB).
axis_dpi_lib(axis_dpi ${AXIS_DPI_SRC})

# axis_dpi.cpp linked with the mock simulator/SV side, for python/test_c_side.py.
if(AXI_VIP_SHIMS_BUILD_TEST_LIBS)
    axis_dpi_lib(axis_dpi_test ${AXIS_DPI_SRC}
                 ${AXI_VIP_SHIMS_TEST_DIR}/axi_vip_shims/axi_stream_vip_shims/dpi_c/test_c_side_mock.c)
endif()
