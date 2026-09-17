#!/usr/bin/env bash
# Run from any directory. Requires an already installed Docker or Podman.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
engine=${CONTAINER_ENGINE:-podman}
case "$engine" in
    podman) pull=--pull=never; build_security=(--security-opt label=disable) ;;
    docker) pull=--pull=false; build_security=() ;;
    *) echo 'CONTAINER_ENGINE must be podman or docker.' >&2; exit 2 ;;
esac
image=${1:-localhost/rhel9-nvidia-bootc:offline}
sha256sum --check --strict SHA256SUMS
(
    cd offline
    sha256sum --check --strict TRANSFER.SHA256SUMS
)
# Extract to a fresh build context so a previous build cannot leave stale files.
context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
mkdir "$context/offline"
tar -xzf offline/nvidia-offline.tar.gz -C "$context/offline"
"$engine" load -i offline/base-image.tar
actual=$("$engine" image inspect --format '{{.Id}}' "$(cat offline/base-image.txt)")
[[ ${actual#sha256:} == "$(cat offline/base-image-id.txt)" ]] || {
    echo 'Loaded base image ID differs from the bundle.' >&2; exit 1;
}
"$engine" build "$pull" --network=none "${build_security[@]}" \
    --build-arg "BASE_IMAGE=$(cat offline/base-image.txt)" \
    -f "$PWD/Containerfile" -t "$image" "$context"
echo "Built $image. Deploy it using your bootc installation/update process."
