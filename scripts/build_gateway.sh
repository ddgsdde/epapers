#!/bin/sh
set -eu
EPAPERS_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export IDF_TOOLS_PATH="$EPAPERS_ROOT/.local/idf-tools"
export IDF_PATH="$EPAPERS_ROOT/.local/esp-idf"
export ESP_PYTHON="$EPAPERS_ROOT/.venv/bin/python"
export PATH="$EPAPERS_ROOT/.venv/bin:$PATH"
export IDF_PATH_FORCE=1
. "$IDF_PATH/export.sh" >/dev/null
cd "$EPAPERS_ROOT/firmware/gateway"
idf.py -DIDF_TARGET=esp32s3 build
