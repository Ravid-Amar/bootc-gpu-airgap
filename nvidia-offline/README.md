# Prepare or update the offline installation

Run these commands from the project root on the **internet-connected machine**.
To use an already prepared bundle, follow [transfer/README.md](../transfer/README.md).
For additive customization inside the isolated network, follow
[CUSTOM_BASE_AIRGAP.md](CUSTOM_BASE_AIRGAP.md).

You need Docker or Podman, access to the matching RHEL package repositories,
and enough space for base images, RPMs, and a driver build. No GPU is needed here.
Use the same engine and user throughout.

## 1. Choose versions

Edit `nvidia-offline/config.sh`. If it does not exist, copy
`nvidia-offline/config.example.sh` to that path first.

- `BASE_IMAGE`: clean, unregistered RHEL 9 bootc image (x86_64).
- `CONTAINER_ENGINE`: `docker` or `podman`.
- `DOWNLOAD_IMAGE`: optional registered image with exactly the same RPM inventory
  as the clean base. Only this image receives subscription credentials.
- `DRIVER_VERSION`: exact driver version.
- `TOOLKIT_VERSION`: exact Container Toolkit version **including RPM release**,
  for example `1.20.0-1` (a syntax example, not a latest-version claim).
- `DRIVER_MODE`: `open-dkms`, `proprietary-dkms`, or `precompiled`.
  Precompiled mode also needs an exact `KMOD_NEVRA` matching the base kernel.
- Keep `EXTRA_PACKAGES=()` for a minimal installation and `SAVE_BASE_IMAGE=1`.

List available versions before changing them:

```bash
bash nvidia-offline/bundle.sh inspect nvidia-offline/config.sh
```

Registry login alone does not grant RHEL RPM repository access. DKMS also needs
CodeReady Builder. Open modules require a supported Turing-or-newer GPU.
The configuration is trusted Bash code; only use files you control.

## 2. Prepare and verify

Choose a new output directory for each release:

```bash
bash nvidia-offline/bundle.sh prepare nvidia-offline/config.sh nvidia-offline/dist/release-02
```

Wait for `Verified bundle:`. This downloads RPMs, checks signatures and signed
toolkit metadata, then installs in a fresh base with `--network=none`. It checks
driver/toolkit versions, builds modules for the image kernel, rebuilds initramfs,
and runs `bootc container lint`. Failed builds are not published as transfer bundles.

## 3. Recreate the transfer folder

Keep the previous release for recovery, then package the new one:

```bash
mkdir -p archive
mv transfer archive/transfer-release-01
bash nvidia-offline/package-transfer.sh nvidia-offline/dist/release-02 transfer
```

Use an unused archive name each time. On the first preparation, skip `mv` if
`transfer/` does not exist. Copy **only the complete `transfer/` directory** to
the offline builder. Its README explains building and deployment.

## Next time: update the toolkit

Change only `TOOLKIT_VERSION` in `config.sh`, keep `EXTRA_PACKAGES=()`, and repeat
steps 2–3 with a new release directory. Transfer the complete replacement folder,
build a new OS image, publish it to your internal registry, then update the bootc
machines as described in the transfer README. Do not edit an old RPM archive or
run DNF on the live bootc OS.

For a driver update, change `DRIVER_VERSION` too. For a base/kernel update,
change `BASE_IMAGE` (preferably to a digest), or explicitly pull its newer tag
with your chosen engine first: preparation otherwise reuses a cached image.
Ensure `DOWNLOAD_IMAGE` matches the new base RPM inventory. Always regenerate
the bundle when the kernel changes.

## Scope and checks

The clean install adds NVIDIA plus required dependencies. DKMS compilers remain
in the OS; packages already supplied by RHEL are not stripped. No application,
nginx, pip, cloud-init, or extra runtime is requested. The optional VM pipeline
has a separate configuration and requires additional VM packages.

GPU operation must be checked after boot with `nvidia-smi` and `nvidia-ctk cdi list`.
Secure Boot requires trusted module signing and target key enrollment; the bundle
does not enroll keys. ISO/disk creation requires your separate bootc-image-builder
image and its offline inputs. The RPM bundle alone is not a bootable ISO.

`install.sh` supports matching mutable RHEL hosts with `--host`, but must not be
run on a live bootc host. Image builds must install immediately after the matching
`FROM`: the installer checks the original base inventory and kernel.

Local checks:

```bash
python3 -m unittest discover -s nvidia-offline/tests -v
```
