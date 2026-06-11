#!/usr/bin/env bash

# Copyright (c) 2022-2025, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

# Exit on error.
set -e

# Get repo directory.
export ISAACLAB_PATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Find python to run CLI.
if [ -n "$VIRTUAL_ENV" ]; then
    python_exe="$VIRTUAL_ENV/bin/python"
elif [ -f "$ISAACLAB_PATH/env_isaaclab/bin/python" ]; then
    python_exe="$ISAACLAB_PATH/env_isaaclab/bin/python"
elif [ -n "$CONDA_PREFIX" ]; then
    python_exe="$CONDA_PREFIX/bin/python"
elif [ -f "$ISAACLAB_PATH/_isaac_sim/python.sh" ]; then
    python_exe="$ISAACLAB_PATH/_isaac_sim/python.sh"
else
    # Fallback to system python
    python_exe="python3"
fi

# Add source/isaaclab to PYTHONPATH so we can import isaaclab.cli.
export PYTHONPATH="$ISAACLAB_PATH/source/isaaclab:$PYTHONPATH"

# If a local Isaac Sim binary is present and we are not executing through
# python.sh, source its env setup so that PYTHONPATH/PATH/EXP_PATH are correct
# without depending on a conda activate.d hook.
if [ -d "$ISAACLAB_PATH/_isaac_sim" ]; then
    if [ "$python_exe" = "$ISAACLAB_PATH/_isaac_sim/python.sh" ]; then
        :
    elif [ -f "$ISAACLAB_PATH/_isaac_sim/setup_python_env.sh" ]; then
        export CARB_APP_PATH="$ISAACLAB_PATH/_isaac_sim/kit"
        export ISAAC_PATH="$ISAACLAB_PATH/_isaac_sim"
        export EXP_PATH="$ISAACLAB_PATH/_isaac_sim/apps"
        # shellcheck disable=SC1091
        . "$ISAACLAB_PATH/_isaac_sim/setup_python_env.sh" >/dev/null 2>&1 || true
    elif [ -f "$ISAACLAB_PATH/_isaac_sim/setup_conda_env.sh" ]; then
        # shellcheck disable=SC1091
        . "$ISAACLAB_PATH/_isaac_sim/setup_conda_env.sh" >/dev/null 2>&1 || true
    else
        echo "[WARNING] _isaac_sim is present but no supported Isaac Sim env setup script was found; Isaac Sim env vars not exported." >&2
        echo "[WARNING] Re-extract the Isaac Sim binary zip if you intend to use the bundled binary." >&2
    fi
fi

export ISAACLAB_PYTHON_EXE="$python_exe"

# Execute CLI.
exec "$python_exe" -c "from isaaclab.cli import cli; cli()" "$@"
