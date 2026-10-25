# Shortcuts around the CMake build in src/.
#
#   make          build the DPI-C libraries into lib/
#   make test     build, then run the simulator-free tests
#   make clean    empty build/, lib/ and test artifacts (keeping .gitkeep),
#                 remove Python caches

BUILD_DIR := build

.PHONY: all test clean

all:
	cmake -B $(BUILD_DIR)
	cmake --build $(BUILD_DIR)

test: all
	python3 -m pytest

clean:
	find $(BUILD_DIR) lib -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf {} +
	find . \( -path ./.venv -o -path ./.git \) -prune -o \
	       -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
	find test -path '*/artifacts/*' -prune ! -name .gitkeep -exec rm -rf {} +
