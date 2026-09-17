#!/usr/bin/env bash
# Optional: load an image archive and publish one explicit image to your registry.
set -euo pipefail
[[ $# == 3 ]] || {
    echo "Usage: bash $0 ARCHIVE SOURCE_IMAGE DESTINATION_IMAGE" >&2
    exit 2
}
[[ -f $1 ]] || { echo "Archive not found: $1" >&2; exit 1; }
# Log in to the destination registry with docker login before running this.
docker load -i "$1"
docker image inspect "$2" >/dev/null
docker tag "$2" "$3"
docker push "$3"
