#!/usr/bin/env bash
# Publish one self-contained folder from a successfully verified bundle.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $# == 2 ]] || { echo 'Usage: bash package-transfer.sh BUNDLE_DIR NEW_TRANSFER_DIR' >&2; exit 2; }
bundle=$(realpath -- "$1")
output=$(realpath -m -- "$2")
[[ ! -e $output ]] || { echo "Already exists: $output" >&2; exit 1; }
(
    cd "$bundle"
    sha256sum --check --strict TRANSFER.SHA256SUMS
)
[[ -f $bundle/base-image.tar ]] || { echo 'Prepare with SAVE_BASE_IMAGE=1.' >&2; exit 1; }
mkdir -p "$output/offline"
cp --reflink=auto "$bundle/"{nvidia-offline.tar.gz,base-image.tar,base-image.txt,base-image-id.txt,source-image.txt,Containerfile.verify,TRANSFER.SHA256SUMS} "$output/offline/"
cp "$here/Containerfile" "$output/Containerfile"
cp "$here/build.sh" "$output/build.sh"
cp "$here/build-custom.sh" "$output/build-custom.sh"
cp "$here/TRANSFER.md" "$output/README.md"
cp "$here/CUSTOM_BASE_AIRGAP.md" "$output/CUSTOM_BASE_AIRGAP.md"
cp -R "$here/../examples" "$output/examples"
(
    cd "$output"
    find . -type f ! -path ./SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
)
echo "Transfer this entire directory: $output"
