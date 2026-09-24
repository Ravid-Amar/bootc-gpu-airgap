# Offline installation

**Transfer this entire folder**, including `offline/`. Nothing else from the
project is needed to build the NVIDIA OS image. `examples/` is optional and is
never installed by the default build.

To customize the saved RHEL base inside the isolated network before installing
NVIDIA, follow [CUSTOM_BASE_AIRGAP.md](CUSTOM_BASE_AIRGAP.md).
That custom-base CI flow may omit `offline/base-image.tar` when it uses a remote
custom image; `build-custom.sh` still verifies every other checksum entry.

```text
transfer/
  README.md       These instructions
  CUSTOM_BASE_AIRGAP.md  Additive customization workflow
  build.sh        Verify, load the base, and build without internet
  build-custom.sh Build NVIDIA on an additive custom base
  Containerfile   Minimal NVIDIA OS image
  SHA256SUMS      Transfer integrity checks
  offline/        Base image, NVIDIA RPM archive, and bundle checksums
  examples/       Optional examples only
```

This is a clean RHEL base plus NVIDIA driver, Container Toolkit, and their required
dependencies. It does not add an application or request extra RPMs. RHEL's existing
packages and the tools needed to compile the driver remain in the OS.
Allow several GB of free space for extraction and building, in addition to this
folder. The first build compiles the driver and can take several minutes.

## After the transfer

The offline builder must already have Docker or Podman installed. Use the same
engine user for all commands. From inside the copied `transfer/` directory:

```bash
# Docker:
CONTAINER_ENGINE=docker bash build.sh localhost/rhel9-nvidia-bootc:release-01

# Or Podman:
CONTAINER_ENGINE=podman bash build.sh localhost/rhel9-nvidia-bootc:release-01
```

Choose one command. If your engine requires root, run it through
`sudo env CONTAINER_ENGINE=podman bash build.sh localhost/rhel9-nvidia-bootc:release-01`
(or substitute `docker`). The script checks all files, loads the exact saved base,
and builds with networking and image pulls disabled. It stops on any failure.
For Podman, the build containers use `--security-opt label=disable` so SELinux
can read the temporarily mounted RPM bundle. This does not change the installed
image's SELinux settings.

If an interrupted older attempt reports `device or resource busy`, first make
sure no build is still running, then run `podman unmount --all` (or
`sudo podman unmount --all` when using rootful Podman) before retrying. The current
Containerfile mounts its temporary bundle below `/tmp`; older copies that use
`target=/mnt/nvidia-offline` can fail with `stat /mnt: no such file or directory`.

The result is a **container image**, not an installed machine or bootable ISO.
For a new machine, use your existing bootc provisioning process with this image.
If that process creates an ISO or disk, bring bootc-image-builder and its required
offline repositories separately. This folder does not contain those inputs.

For machines already running bootc, publish the image to a registry they can
reach. Replace `registry.internal/team/nvidia-bootc` with your actual image path:

```bash
docker tag localhost/rhel9-nvidia-bootc:release-01 registry.internal/team/nvidia-bootc:stable
docker login registry.internal
docker push registry.internal/team/nvidia-bootc:stable
```

With Podman, substitute `podman` for `docker`. On the **target bootc machine**,
configure registry credentials if needed, then select this image and reboot:

```bash
sudo bootc switch registry.internal/team/nvidia-bootc:stable
sudo systemctl reboot
```

After boot, verify the GPU:

```bash
nvidia-smi
nvidia-ctk cdi list
```

Secure Boot requires the target to trust the kernel module signing key. The
bundle does not enroll keys. GPU workload container images must be transferred
separately; the NVIDIA Container Toolkit is not the CUDA application toolkit.

## Next time you update the toolkit or driver

1. On the connected machine, edit `nvidia-offline/config.sh`. Change
   `TOOLKIT_VERSION` (including its RPM release), or `DRIVER_VERSION`.
   Keep `EXTRA_PACKAGES=()`.
2. From the project root, prepare a new release:
   `bash nvidia-offline/bundle.sh prepare nvidia-offline/config.sh nvidia-offline/dist/release-02`.
   Wait for `Verified bundle:`.
3. Move the old `transfer/` into an unused directory under `archive/`, then run
   `bash nvidia-offline/package-transfer.sh nvidia-offline/dist/release-02 transfer`.
4. Transfer the whole new folder. Run `build.sh` with a new image tag such as
   `localhost/rhel9-nvidia-bootc:release-02`, then tag and push that image to the
   same internal `:stable` location used above.
5. On each target already following that image, run `sudo bootc upgrade`, then
   `sudo systemctl reboot`. Check the GPU again after boot.

If you use a different registry image path/tag, use `bootc switch` to select it.
A base/kernel change also requires a newly prepared bundle. Keep the previous
release until the replacement has booted successfully. Updates happen through
new OS images; do not run this installer or DNF on the live bootc machine.
