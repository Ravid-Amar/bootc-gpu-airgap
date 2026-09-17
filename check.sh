#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

python3 -m unittest discover -s nvidia-offline/tests -v
python3 -m unittest discover -s ci/tests -v

for script in check.sh ci/*.sh nvidia-offline/*.sh examples/*.sh; do
    bash -n "$script"
done
