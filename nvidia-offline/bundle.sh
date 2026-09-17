#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/common.sh"
usage() {
    cat <<'EOF'
Usage:
  bash bundle.sh inspect CONFIG [BUILD_OPTIONS...]
  bash bundle.sh prepare CONFIG OUTPUT_DIR [BUILD_OPTIONS...]

See README.md for preparation and update instructions.
Runs in a disposable RHEL bootc downloader image, then verifies installation in
a fresh copy of the base with --network=none. OUTPUT_DIR must not already exist.
Set CONTAINER_ENGINE=podman or docker in CONFIG. The download image needs access
to RHEL package repos. Use DOWNLOAD_IMAGE for a separately registered image.
Additional build options (e.g. entitlement secret mounts) apply only online.
EOF
}
case ${1:---help} in
    --help|-h) usage; exit 0 ;;
    inspect|prepare) action=$1; shift ;;
    *) usage >&2; exit 2 ;;
esac
[[ $# -ge 1 && -f $1 ]] || die 'Provide the path to a trusted Bash configuration file.'
config=$(realpath -- "$1"); shift
EXTRA_PACKAGES=()
source "$config"
validate_config "$action"
if [[ $action == prepare ]]; then
    [[ $# -ge 1 ]] || die 'Provide a new output directory.'
    output=$(realpath -m -- "$1"); shift
    [[ ! -e $output ]] || die "Output already exists: $output. Choose a new directory for each bundle."
fi
engine=${CONTAINER_ENGINE:-podman}
command -v "$engine" >/dev/null || die "$engine is required on the connected builder."
pull_option=--pull=never
[[ $engine != docker ]] || pull_option=--pull=false
scratch=$(mktemp -d)
cid=''
cleanup() {
    [[ ! $cid ]] || "$engine" rm "$cid" >/dev/null
    rm -rf -- "$scratch"
}
trap cleanup EXIT
cp "$here/"{common.sh,setup.sh,download.sh,collect-keys.py,toolkit-repo.py,install.sh,Containerfile.download} "$scratch/"
cp "$config" "$scratch/config.sh"

# Resolve once and use an immutable local ID throughout preparation and testing.
if ! "$engine" image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    "$engine" pull "$BASE_IMAGE"
fi
base_id=$("$engine" image inspect --format '{{.Id}}' "$BASE_IMAGE")
base_id=${base_id#sha256:}
base_tag="localhost/nvidia-offline-base:${base_id:0:16}"
"$engine" tag "$BASE_IMAGE" "$base_tag"
download_image=${DOWNLOAD_IMAGE:-$base_tag}
if ! "$engine" image inspect "$download_image" >/dev/null 2>&1; then
    "$engine" pull "$download_image"
fi
# Compare the downloader's original RPM inventory with the clean offline base
# before downloading packages. Registration files may differ; RPMs must match.
"$engine" run --rm --entrypoint /usr/bin/rpm "$base_tag" -qa \
    --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' \
    | LC_ALL=C sort | sed '/^gpg-pubkey-/d' > "$scratch/base-packages.txt"
"$engine" build "$pull_option" --no-cache --build-arg "BASE_IMAGE=$download_image" \
    --build-arg "ACTION=$action" --iidfile "$scratch/downloader.id" \
    "$@" -f "$scratch/Containerfile.download" "$scratch"
[[ $action == prepare ]] || exit 0

mkdir -p "$output/nvidia-offline"
cid=$("$engine" create "$(cat "$scratch/downloader.id")" /bin/true)
"$engine" cp "$cid:/bundle/." "$output/nvidia-offline/"
"$engine" rm "$cid" >/dev/null
cid=''
printf '%s\n' "$base_tag" > "$output/base-image.txt"
printf '%s\n' "$base_id" > "$output/base-image-id.txt"
printf '%s\n' "$BASE_IMAGE" > "$output/source-image.txt"
cat > "$output/Containerfile.verify" <<'EOF'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY nvidia-offline /tmp/nvidia-offline
RUN NVIDIA_OFFLINE_IMAGE_BUILD=1 bash /tmp/nvidia-offline/install.sh --bootc && rm -rf /tmp/nvidia-offline
RUN bootc container lint
EOF
echo 'Verifying installation in a clean base image with networking disabled...'
"$engine" build "$pull_option" --network=none --no-cache \
    --build-arg "BASE_IMAGE=$base_tag" --iidfile "$output/verified-image-id.txt" \
    -f "$output/Containerfile.verify" "$output"

# Publish the transfer artifacts only after the clean offline build succeeds.
tar -C "$output" -czf "$output/nvidia-offline.tar.gz" nvidia-offline
if [[ ${SAVE_BASE_IMAGE:-1} == 1 ]]; then
    if [[ $engine == podman ]]; then
        "$engine" save --format docker-archive -o "$output/base-image.tar" "$base_tag"
    else
        "$engine" save -o "$output/base-image.tar" "$base_tag"
    fi
fi
(
    cd "$output"
    files=(nvidia-offline.tar.gz base-image.txt base-image-id.txt source-image.txt Containerfile.verify)
    [[ ! -f base-image.tar ]] || files+=(base-image.tar)
    sha256sum "${files[@]}" > TRANSFER.SHA256SUMS
)
echo "Verified bundle: $output/nvidia-offline.tar.gz"
echo "Offline base: $base_tag"
