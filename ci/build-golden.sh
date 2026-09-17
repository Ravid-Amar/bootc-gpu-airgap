#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
require NVIDIA_BASE_REF BIB_IMAGE BASELINE_IMAGE
if [[ -n ${RPM_REPO_S3_URI:-} && ${BOOTC_RPM_REPOS_READY:-0} != 1 ]]; then
    exec python3 ci/rpm-repos.py run -- bash ci/build-golden.sh
fi
command -v qemu-img >/dev/null || die 'Install qemu-img on the shell runner for disk validation.'
config=$(realpath -- "${BIB_CONFIG:-ci/image-builder/config.toml}")
[[ -f $config && -r $config ]] || die 'BIB_CONFIG must point to a readable disk build configuration file.'
context=$(mktemp -d "$PROJECT_ROOT/build/golden-context.XXXXXX")
cp ci/golden/Containerfile "$context/Containerfile"
python3 ci/render.py baseline --output "$context/baseline.container"
mkdir "$context/rpm-repos"
if [[ -n ${RPM_REPO_BASEURL:-} ]]; then
    python3 ci/rpm-repos.py render --output "$context/rpm-repos/bootc.repo"
fi
init_podman
"${PODMAN[@]}" image exists "$BIB_IMAGE" || die 'Load bootc-image-builder into rootful Podman on this runner first.'
storage=$("${PODMAN[@]}" info --format '{{.Store.GraphRoot}}')
[[ $storage == /var/lib/containers/storage ]] || die 'This BIB invocation requires the standard /var/lib/containers/storage rootful graph root.'
registry_login
"${PODMAN[@]}" pull --authfile "$AUTH_FILE" "$NVIDIA_BASE_REF"
image=$IMAGE_PREFIX/golden-bootc:$BUILD_TAG
"${PODMAN[@]}" build --pull=never --network=none --build-arg "BASE_IMAGE=$NVIDIA_BASE_REF" \
    -f "$context/Containerfile" -t "$image" "$context"
golden_ref=$(push_ref "$image" "$PROJECT_ROOT/build/golden.digest")

# bootc-image-builder uses the local rootful store for the source OS image.
# Its RPM content may require internal mirrors configured in the OS image.
output=$(mktemp -d "$PROJECT_ROOT/build/bib-output.XXXXXX")
"${PODMAN[@]}" run --rm --privileged --pull=never \
    --network="${BIB_NETWORK:-host}" --security-opt label=type:unconfined_t \
    -v "$config:/config.toml:ro" \
    -v "$output:/output" \
    -v "$storage:/var/lib/containers/storage" \
    "$BIB_IMAGE" --local --type qcow2 --rootfs xfs --chown "$(id -u):$(id -g)" "$image"
disk=$output/qcow2/disk.qcow2
[[ -s $disk ]] || die "BIB did not produce $disk; inspect its output and installed version."
qemu-img check "$disk"
qemu-img info --output=json "$disk" | python3 -c '
import json, sys
d=json.load(sys.stdin)
assert d["format"] == "qcow2", "Expected qcow2"
assert not d.get("backing-filename"), "Disk must not require an external backing file"
print("Disk virtual size:", d["virtual-size"], "bytes")
'
disk_context=$(mktemp -d "$PROJECT_ROOT/build/disk-context.XXXXXX")
cp ci/containerdisk/Containerfile "$disk_context/Containerfile"
mv "$disk" "$disk_context/disk.qcow2"
(cd "$disk_context" && sha256sum disk.qcow2) > build/qcow2.sha256
disk_image=$IMAGE_PREFIX/containerdisk:$BUILD_TAG
"${PODMAN[@]}" build --pull=never --network=none -f "$disk_context/Containerfile" \
    -t "$disk_image" "$disk_context"
disk_ref=$(push_ref "$disk_image" "$PROJECT_ROOT/build/containerdisk.digest")
printf 'GOLDEN_BOOTC_REF=%s\nCONTAINERDISK_REF=%s\n' "$golden_ref" "$disk_ref" > build/golden.env
