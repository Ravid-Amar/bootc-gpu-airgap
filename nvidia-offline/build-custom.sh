#!/usr/bin/env bash
# Install this transfer bundle onto a locally available additive custom base.
set -euo pipefail

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  sudo env CUSTOM_BASE_IMAGE=IMAGE OUTPUT_IMAGE=NEW_IMAGE \
    CONTAINER_ENGINE=podman bash build-custom.sh

Required environment variables:
  CUSTOM_BASE_IMAGE  Existing local custom RHEL bootc image (input)
  OUTPUT_IMAGE       New image tag to create (output)

Optional environment variables:
  CONTAINER_ENGINE   podman (default) or docker
  PULL_CUSTOM_BASE   1 to pull CUSTOM_BASE_IMAGE first; 0 (default) for local only
  BUILD_CONTEXT      New absolute temporary path; automatically created if unset
  TMPDIR             Parent for an automatic BUILD_CONTEXT (default: /var/tmp)
EOF
}

step() {
    printf '\n[%s/7] %s\n' "$1" "$2"
}

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$here"

case ${1:-} in
    --help|-h) usage; exit 0 ;;
    '') ;;
    *) usage >&2; die 'This script takes configuration through environment variables, not arguments.' ;;
esac

custom_image=${CUSTOM_BASE_IMAGE:-}
output_image=${OUTPUT_IMAGE:-}
engine=${CONTAINER_ENGINE:-podman}
pull_custom=${PULL_CUSTOM_BASE:-0}

[[ $custom_image ]] || die 'Set CUSTOM_BASE_IMAGE to the existing custom base image.'
[[ $output_image ]] || die 'Set OUTPUT_IMAGE to the new NVIDIA image tag.'
[[ $custom_image != "$output_image" ]] || die 'CUSTOM_BASE_IMAGE and OUTPUT_IMAGE must be different.'
[[ $custom_image =~ ^[[:alnum:]][[:alnum:]._/:@-]*$ ]] || die 'CUSTOM_BASE_IMAGE is not a valid image reference.'
[[ $output_image =~ ^[[:alnum:]][[:alnum:]._/:-]*$ && $output_image != *@* ]] ||
    die 'OUTPUT_IMAGE must be a tag, not a digest, and contain no shell syntax.'
[[ $pull_custom =~ ^[01]$ ]] || die 'PULL_CUSTOM_BASE must be 0 or 1.'

case $engine in
    podman)
        pull=--pull=never
        build_security=(--security-opt label=disable)
        ;;
    docker)
        pull=--pull=false
        build_security=()
        ;;
    *) die 'CONTAINER_ENGINE must be podman or docker.' ;;
esac

for command in "$engine" sha256sum tar comm find sort xargs mktemp realpath sed wc; do
    command -v "$command" >/dev/null || die "Required command is missing: $command"
done

[[ -f Containerfile && -f SHA256SUMS ]] ||
    die 'Run the script from a complete transfer directory.'
[[ -f offline/nvidia-offline.tar.gz && -f offline/TRANSFER.SHA256SUMS ]] ||
    die 'The transfer directory is missing its offline bundle.'

build_context=${BUILD_CONTEXT:-}
if [[ $build_context ]]; then
    [[ $build_context == /* ]] || die 'BUILD_CONTEXT must be an absolute path.'
    build_context=$(realpath -m -- "$build_context")
    case $build_context in
        /|/tmp|/var|/var/tmp) die 'BUILD_CONTEXT is too broad; choose a new dedicated directory.' ;;
    esac
    [[ ! -e $build_context ]] || die "BUILD_CONTEXT already exists: $build_context"
    mkdir -p -- "$build_context"
else
    temp_root=${TMPDIR:-/var/tmp}
    [[ -d $temp_root ]] || die "Temporary directory does not exist: $temp_root"
    build_context=$(mktemp -d "$temp_root/nvidia-custom-build.XXXXXX")
fi

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [[ -n ${build_context:-} && -d $build_context ]]; then
        rm -rf -- "$build_context"
    fi
    if (( status == 0 )); then
        echo "Cleaned temporary build context: $build_context"
    else
        echo "Build failed; cleaned temporary build context: $build_context" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

step 1 'Checking the original transfer files'
sha256sum --check --strict SHA256SUMS
(
    cd offline
    sha256sum --check --strict TRANSFER.SHA256SUMS
)

step 2 'Checking image names and local availability'
if [[ $pull_custom == 1 ]]; then
    echo "Pulling custom base: $custom_image"
    "$engine" pull "$custom_image"
fi
"$engine" image inspect "$custom_image" >/dev/null 2>&1 ||
    die "Custom base is not in the $engine image store: $custom_image (set PULL_CUSTOM_BASE=1 to pull it)"
if "$engine" image inspect "$output_image" >/dev/null 2>&1; then
    die "OUTPUT_IMAGE already exists; choose a new tag: $output_image"
fi
echo "Custom base: $custom_image"
echo "New image:   $output_image"
echo "Context:     $build_context"

step 3 'Extracting the NVIDIA bundle into the temporary context'
mkdir -p "$build_context/offline"
tar -xzf offline/nvidia-offline.tar.gz -C "$build_context/offline"
bundle=$build_context/offline/nvidia-offline
[[ -f $bundle/base-packages.txt && -f $bundle/SHA256SUMS ]] ||
    die 'The extracted NVIDIA bundle is incomplete.'
(
    cd "$bundle"
    sha256sum --check --strict SHA256SUMS
)
[[ -f $bundle/settings.sh ]] || die 'The extracted NVIDIA bundle has no version settings.'
# The file is part of the verified bundle and contains shell-escaped assignments only.
source "$bundle/settings.sh"
[[ ${DRIVER_VERSION:-} && ${TOOLKIT_VERSION:-} ]] ||
    die 'The verified bundle does not define driver and toolkit versions.'

step 4 'Comparing the custom base with the original RPM inventory'
"$engine" run --rm --network=none --pull=never \
    --entrypoint /usr/bin/rpm "$custom_image" \
    -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' \
    | LC_ALL=C sort | sed '/^gpg-pubkey-/d' > "$build_context/custom-packages.txt"

comm -23 "$bundle/base-packages.txt" "$build_context/custom-packages.txt" \
    > "$build_context/removed-or-changed-packages.txt"
comm -13 "$bundle/base-packages.txt" "$build_context/custom-packages.txt" \
    > "$build_context/added-packages.txt"

if [[ -s $build_context/removed-or-changed-packages.txt ]]; then
    echo 'The custom base removed, upgraded, or downgraded these original packages:' >&2
    sed -n '1,40p' "$build_context/removed-or-changed-packages.txt" >&2
    lines=$(wc -l < "$build_context/removed-or-changed-packages.txt")
    (( lines <= 40 )) || echo "... and $((lines - 40)) more" >&2
    die 'Only additive RPM changes are allowed. Restore the original packages and retry.'
fi

added=$(wc -l < "$build_context/added-packages.txt")
echo "RPM inventory accepted: all original packages remain; $added package entries were added."

step 5 'Adapting and checksumming the disposable inventory'
cp "$build_context/custom-packages.txt" "$bundle/base-packages.txt"
(
    cd "$bundle"
    find . -type f ! -name SHA256SUMS -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 sha256sum > SHA256SUMS
)

step 6 'Building NVIDIA onto the custom base without pulls or networking'
"$engine" build "$pull" --network=none "${build_security[@]}" \
    --build-arg "BASE_IMAGE=$custom_image" \
    --label "nvidia.drivers_version=$DRIVER_VERSION" \
    --label "nvidia.containers_toolkit_version=$TOOLKIT_VERSION" \
    -f "$here/Containerfile" -t "$output_image" "$build_context"

step 7 'Confirming that the output image was created'
"$engine" image inspect "$output_image" >/dev/null
echo "Built successfully: $output_image"
echo "Image label: nvidia.drivers_version=$DRIVER_VERSION"
echo "Image label: nvidia.containers_toolkit_version=$TOOLKIT_VERSION"
echo 'After deploying and booting it on the GPU machine, run:'
echo '  nvidia-smi'
echo '  nvidia-ctk cdi list'
