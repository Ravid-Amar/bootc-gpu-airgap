#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"
if [[ -n ${OFFLINE_S3_URI:-} ]]; then
    require S3_ENDPOINT_URL
    [[ $OFFLINE_S3_URI == s3://* ]] || die 'OFFLINE_S3_URI must be an s3://bucket/prefix URI.'
    command -v aws >/dev/null || die 'Install AWS CLI on the runner to read the offline bundle from S3.'
    # A fresh directory prevents files from a previous snapshot being reused.
    OFFLINE_DIR=$(mktemp -d "$PROJECT_ROOT/build/offline-inputs.XXXXXX")
    aws --endpoint-url "$S3_ENDPOINT_URL" s3 sync "$OFFLINE_S3_URI" "$OFFLINE_DIR" \
        --exclude 'nvidia-offline/*' --only-show-errors
else
    OFFLINE_DIR=${OFFLINE_DIR:-/srv/bootc/offline}
fi
OFFLINE_DIR=$(realpath -- "$OFFLINE_DIR")
for file in TRANSFER.SHA256SUMS nvidia-offline.tar.gz base-image.tar base-image.txt base-image-id.txt; do
    [[ -f $OFFLINE_DIR/$file ]] || die "Missing offline input: $OFFLINE_DIR/$file"
done
(cd "$OFFLINE_DIR" && sha256sum --check --strict TRANSFER.SHA256SUMS)
context=$(mktemp -d "$PROJECT_ROOT/build/base-context.XXXXXX")
tar -xzf "$OFFLINE_DIR/nvidia-offline.tar.gz" -C "$context"
bash "$context/nvidia-offline/install.sh" --verify-only
for package in cloud-init qemu-guest-agent; do
    if ! grep -Eq "^$package-[0-9]+:" "$context/nvidia-offline/packages.txt" "$context/nvidia-offline/base-packages.txt"; then
        die "This bundle lacks $package. Prepare a new bundle with ci/nvidia-vm.config.sh before running this pipeline."
    fi
done
init_podman
registry_login
"${PODMAN[@]}" load -i "$OFFLINE_DIR/base-image.tar"
base=$(cat "$OFFLINE_DIR/base-image.txt")
expected=$(cat "$OFFLINE_DIR/base-image-id.txt")
actual=$("${PODMAN[@]}" image inspect --format '{{.Id}}' "$base")
[[ ${actual#sha256:} == "${expected#sha256:}" ]] || die 'Loaded base ID differs from the verified bundle.'
cp -a ci/base "$context/base"
image=$IMAGE_PREFIX/nvidia-base:$BUILD_TAG
"${PODMAN[@]}" build --pull=never --network=none \
    --build-arg "BASE_IMAGE=$base" -f "$context/base/Containerfile" -t "$image" "$context"
ref=$(push_ref "$image" "$PROJECT_ROOT/build/base.digest")
printf 'NVIDIA_BASE_REF=%s\n' "$ref" > build/base.env
