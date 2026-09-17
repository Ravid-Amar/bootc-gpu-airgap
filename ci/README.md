# Build and deploy with GitLab

The pipeline builds an NVIDIA bootc image, turns it into a golden VM disk,
and publishes a reusable OpenShift Virtualization template. Set
`DEPLOY_VM=true` when you also want it to create a VM.

**No internet on the runner or VMs:** use the [offline setup](OFFLINE.md) with your
internal image registry and S3 RPM storage. Both HTTP reads and private buckets
with credentials are supported.

## 1. Prepare the runner once

Use a Linux x86_64 GitLab **Shell executor** tagged `bootc-shell` with:

- Bash, Python 3.9+, tar/coreutils, grep, Podman, `qemu-img`, and `kubectl`.
- Noninteractive `sudo -n podman` access for the runner account.
- Your tested bootc-image-builder image loaded into rootful Podman.
- Access to your internal registry, S3 RPM storage (or RPM mirror), and target cluster.
- AWS CLI when downloading the bundle or private RPM repositories from S3.

The cluster needs OpenShift Virtualization with CDI, an existing namespace,
and a working storage class. GPU VMs also need a configured PCI passthrough
resource. See [runner and cluster details](REFERENCE.md#2-prepare-the-shell-runner-and-cluster)
for storage, SELinux, permissions, and standalone KubeVirt setup.

## 2. Prepare the VM package bundle

The older NVIDIA transfer bundle lacks `cloud-init`. On the connected preparation
machine, review [nvidia-vm.config.sh](nvidia-vm.config.sh), including its registered
download image, then run from the repository root:

```bash
bash nvidia-offline/bundle.sh inspect ci/nvidia-vm.config.sh
bash nvidia-offline/bundle.sh prepare ci/nvidia-vm.config.sh nvidia-offline/dist/gpu-vm
```

Use a new output directory. Copy its contents to `/srv/bootc/offline/` on each
runner that can execute the base build, or upload it to S3 and set `OFFLINE_S3_URI`
and `S3_ENDPOINT_URL`. Keep the archives and checksum files together.

## 3. Set your images and environment

Edit these files as needed:

| File | What to put there |
| --- | --- |
| [base/rootfs/](base/rootfs/) | Internal CA certificates, RPM mirror configuration, and OS settings, using their target filesystem paths. |
| [base/Containerfile](base/Containerfile) | Additional OS customization after the NVIDIA installer. |
| [golden/baseline.container](golden/baseline.container) | The container baked into every VM as a Quadlet definition. |
| [vm/workload.container](vm/workload.container) | The per-VM workload and its config mount. |

Add required RPMs to `EXTRA_PACKAGES` in the bundle configuration before preparing
the bundle. Mirror the baseline and workload container images into your internal
registry; the VM pulls them at boot.

## 4. Set GitLab variables and run

Under **Settings → CI/CD → Variables**, set:

| Variable | Value |
| --- | --- |
| `BASELINE_IMAGE` | Your internal baseline container image. |
| `BIB_IMAGE` | The bootc-image-builder image already loaded on the runner. |
| `KUBECONFIG_FILE` | A **File** variable containing cluster credentials. |
| `VM_NAMESPACE` | Your existing namespace; default `golden-images`. |
| `GPU_RESOURCE_NAME` | Your permitted PCI passthrough GPU resource, if using a GPU. |
| `RPM_REPO_BASEURL` **or** `RPM_REPO_S3_URI` | HTTP-readable RPM repository prefix, or private S3 prefix; see [both options](OFFLINE.md#3-store-rpm-content-in-s3). |

The pipeline uses GitLab's registry and job credentials by default. For another
internal registry, also set `REGISTRY_HOST`, `IMAGE_PREFIX`, `REGISTRY_USER`, and
`REGISTRY_PASSWORD`. Mask and protect credentials.

Commit `.gitlab-ci.yml`, `ci/`, and `nvidia-offline/` to your GitLab project, then
use **Run pipeline**. Keep large bundle archives outside Git.

The jobs run in this order:

1. `build_nvidia_base`: install the offline packages and OS customization.
2. `build_golden_containerdisk`: bake the baseline Quadlet, build qcow2, and push the disk image.
3. `deploy_vm_template`: publish a reusable template pinned to that disk's digest.

## 5. Also create a VM

Create a registry pull Secret in the target namespace using a pull-only auth file:

```bash
kubectl -n golden-images create secret generic containerdisk-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson=/secure/path/containerdisk-auth.json
```

Replace the namespace and auth file path with yours. Then set these additional
GitLab variables:

| Variable | Value |
| --- | --- |
| `DEPLOY_VM` | `true` |
| `VM_NAME` | A new, unique VM name. |
| `DISK_PULL_SECRET` | The Secret above; default `containerdisk-pull`. |
| `WORKLOAD_IMAGE` | Your internal workload image. |
| `WORKLOAD_AUTH_FILE` | **File** variable: long-lived, pull-only auth JSON covering both guest container images. |
| `WORKLOAD_CONFIG_FILE` | **File** variable: your application's configuration. |
| `VM_SSH_KEY_FILE` | **File** variable: one SSH public key. |

Default VM size is 4 cores, 16 GiB RAM, and a 40 GiB persistent root disk.
Adjust `VM_CPU_CORES`, `VM_MEMORY`, `VM_DISK_SIZE`, and `VM_STORAGE_CLASS` as needed.
The root disk must fit the generated image. For a private guest registry CA, set
`WORKLOAD_CA_FILE` to a **File** variable containing its PEM certificate.

The script checks VM inputs before publishing and refuses to overwrite an
existing VM or cloud-init Secret. If a failed attempt left a Secret behind,
set `CLOUD_INIT_SECRET` to a new unique name for the retry.

After boot, check inside the guest:

```bash
sudo cloud-init status --wait --long
sudo systemctl status baseline.service workload.service --no-pager
sudo podman ps
# When a GPU is assigned:
nvidia-smi
nvidia-ctk cdi list
```

See the [full reference](REFERENCE.md) for all variables, creating more VMs without
rebuilding, and troubleshooting.

## Local verification

```bash
python3 -m unittest discover -s ci/tests -v
python3 -m unittest discover -s nvidia-offline/tests -v
for script in ci/*.sh; do bash -n "$script" || exit; done
```

Local tests cover rendering and job orchestration with simulated external tools,
plus real Quadlet generation when available. A full image build and VM boot still
need your fresh bundle, runner, registry, mirrors, and cluster configuration.
