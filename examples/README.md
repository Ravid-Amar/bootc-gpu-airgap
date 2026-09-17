# Optional examples

Nothing here is used by the default installation.

- `python-app/`: a sample Python script, systemd service, and Containerfile.
- `docker_load_tag_push.sh`: a manual registry publishing helper. Log in first,
  then pass the archive path, source image name, and destination image name.

To try the Python example, prepare a separate bundle with
`EXTRA_PACKAGES=(python3)`. Extract that bundle into `offline/` in your build
context, then build with `-f examples/python-app/Containerfile` and the saved
`BASE_IMAGE` build argument. The context must contain both `offline/` and
`examples/`. This is an optional custom image, separate from the minimal build.
