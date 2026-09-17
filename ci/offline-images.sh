#!/usr/bin/env bash
source "$(dirname -- "$0")/common.sh"

usage() {
    echo 'Usage: bash ci/offline-images.sh save NEW_DIRECTORY IMAGE [IMAGE ...]'
    echo '       bash ci/offline-images.sh load DIRECTORY'
    echo 'Uses local rootful Podman images only; never pulls or pushes.'
}
case ${1:---help} in
    --help|-h) usage; exit 0 ;;
    save|load) action=$1; shift ;;
    *) usage >&2; exit 2 ;;
esac
[[ $# -ge 1 ]] || { usage >&2; exit 2; }
directory=$(realpath -m -- "$1"); shift

case $action in
    save)
        [[ $# -ge 1 ]] || die 'Provide the local image tags to save.'
        [[ ! -e $directory ]] || die 'Use a new output directory.'
        init_podman
        for image in "$@"; do
            "${PODMAN[@]}" image exists "$image" || die "Image is not loaded locally: $image"
        done
        mkdir -p -- "$directory"
        # Redirect as the runner user so the archive does not become root-owned.
        "${PODMAN[@]}" save --format=docker-archive --multi-image-archive "$@" > "$directory/images.tar"
        printf '%s\n' "$@" > "$directory/images.txt"
        (cd "$directory" && sha256sum images.tar images.txt > SHA256SUMS)
        echo "Transfer $directory to the offline network, then run this script with load."
        ;;
    load)
        [[ $# -eq 0 ]] || { usage >&2; exit 2; }
        for file in images.tar images.txt SHA256SUMS; do
            [[ -s $directory/$file ]] || die "Missing or empty offline input: $directory/$file"
        done
        (cd "$directory" && sha256sum --check --strict SHA256SUMS)
        init_podman
        "${PODMAN[@]}" load -i "$directory/images.tar"
        echo 'Images loaded into rootful Podman. Publish application images to your internal registry before starting VMs.'
        ;;
esac
