#!/usr/bin/env bash
set -euo pipefail
# Never trace credentials, including when the caller used bash -x.
set +x
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
PROJECT_ROOT=$PWD
mkdir -p build
die() { echo "ERROR: $*" >&2; exit 1; }
require() { local name; for name in "$@"; do [[ -n ${!name:-} ]] || die "Set $name (see ci/README.md)."; done; }

init_podman() {
    PODMAN=(podman)
    case ${PODMAN_USE_SUDO:-1} in
        1) PODMAN=(sudo -n podman) ;;
        0) [[ $EUID == 0 ]] || die 'Image builds require rootful Podman: use PODMAN_USE_SUDO=1 or a root runner.' ;;
        *) die 'PODMAN_USE_SUDO must be 0 or 1.' ;;
    esac
    [[ $(uname -m) == x86_64 ]] || die 'The prepared bundle requires Linux x86_64.'
    [[ $("${PODMAN[@]}" info --format '{{.Host.Security.Rootless}}') == false ]] || die 'Rootful Podman is required.'
}

registry_login() {
    REGISTRY_HOST=${REGISTRY_HOST:-${CI_REGISTRY:-}}
    IMAGE_PREFIX=${IMAGE_PREFIX:-${CI_REGISTRY_IMAGE:-}}
    REGISTRY_USER=${REGISTRY_USER:-${CI_REGISTRY_USER:-}}
    REGISTRY_PASSWORD=${REGISTRY_PASSWORD:-${CI_REGISTRY_PASSWORD:-}}
    require REGISTRY_HOST IMAGE_PREFIX REGISTRY_USER REGISTRY_PASSWORD
    [[ $IMAGE_PREFIX == "$REGISTRY_HOST/"* ]] || die 'IMAGE_PREFIX must be under REGISTRY_HOST.'
    [[ $IMAGE_PREFIX != *[[:space:]]* ]] || die 'IMAGE_PREFIX cannot contain whitespace.'
    AUTH_DIR=$(mktemp -d)
    AUTH_FILE=$AUTH_DIR/auth.json
    trap 'rm -f -- "$AUTH_FILE"; rmdir -- "$AUTH_DIR"' EXIT
    # Pre-create as the runner user so cleanup does not require root.
    install -m 600 /dev/null "$AUTH_FILE"
    printf '%s' "$REGISTRY_PASSWORD" | "${PODMAN[@]}" login \
        --authfile "$AUTH_FILE" --username "$REGISTRY_USER" --password-stdin "$REGISTRY_HOST"
    BUILD_TAG=${CI_COMMIT_SHORT_SHA:-local}-${CI_PIPELINE_ID:-0}-${CI_JOB_ID:-0}
}

push_ref() {
    local image=$1 digest_file=$2 digest
    install -m 600 /dev/null "$digest_file"
    "${PODMAN[@]}" push --authfile "$AUTH_FILE" --digestfile "$digest_file" "$image" >&2 || return $?
    digest=$(cat "$digest_file")
    [[ $digest =~ ^sha256:[a-f0-9]{64}$ ]] || die 'Registry returned an invalid image digest.'
    printf '%s@%s' "${image%:*}" "$digest"
}
