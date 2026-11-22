# Mounts, agent box storage and box bind-mount sources

Read this before you move data onto a mount, count on data on a mount to survive a machine stop, choose where temp output goes in an agent box, or delete anything in a box's own tree.

## Which mounts survive a machine stop

A mount's name does not say, and `/mnt/nvme` means opposite things on the two machines. Read the device before you move data onto a mount or treat anything on it as safe. Name the machine with the mount.

- sami-devbox: `/mnt/nvme` and `/mnt/durable` are persistent disks and survive; `/mnt/scratch` is raid0 over local SSD and is empty after a stop.
- sami-rocqperf: `/mnt/durable` is a managed disk and survives; `/mnt/nvme` is raid0 over local NVMe and is empty after a stop.
- On an unfamiliar machine, read the devices under the mount: `lsblk -s -no NAME,MODEL,TYPE "$(findmnt -no SOURCE <mount>)"`, or `sudo nvme list`.

  | Cloud | Disk | Shows as |
  | --- | --- | --- |
  | Google Cloud | persistent disk | model `nvme_card-pd` |
  | Google Cloud | local SSD | model `nvme_card<N>` |
  | Azure | managed disk | `MSFT NVMe Accelerator`, serial `00000` |
  | Azure | local disk | `Microsoft NVMe Direct Disk` |

  - On Azure, an empty `/dev/disk/azure/data/by-lun` does not mean there is no data disk, because an NVMe data disk is not listed there.
  - The surest check on Azure: a managed disk shares a controller with the namespace `/dev/disk/azure/os` points at. That OS disk survives a stop.

## Inside an agent box

- `/mnt/scratch` in a box is the container overlay, not the local SSD. The SSD reaches a box only as its own filesystem: `/tmp` and `$HOME` live in the box's sparse disk image.
- Put a large sort, extract or build in box `/tmp`. When it fills, grow the image online with `agentbox grow <box|session-id> <size>`; never move `TMPDIR` to a `--dir` path, which is a disk every session on the host shares.
- Check a mount from inside the namespace that writes to it (`findmnt -T <path>`, `stat -f <path>`), never from the host.
- From a box, `systemd-run --user` starts its command on the host, through the relayed session bus (`agentbox/README.md`), where the box's own paths (`/tmp`, `$HOME`) are other files. Never run a delete or `apply` that way; run a long one as a named bash service, or with `setsid <cmd> </dev/null >log 2>&1 &`.

## Box bind-mount sources

`<boxes>/<box>/jj-user-repos` and `<boxes>/<box>/omp-run` are the live sources of the box's `~/.config/jj/repos` and `~/.omp/run` bind mounts. `<boxes>` is `/mnt/nvme/<user>/agentbox/boxes` where that disk exists, else `~/boxes` (`findmnt -T ~/.omp/run` names the one in use). They hold no repository but are not scratch: deleting one breaks `jj`, or omp's daemon broker, for the whole box.

- Symptom: `jj` fails `Failed to determine the secure config for a repo` over `EROFS`, and `findmnt -T <path>` or `/proc/self/mountinfo` shows `…/jj-user-repos//deleted`.
- Fix: make the directory again, `sudo umount` the stale target, then `sudo mount --bind` it back. The directory alone does not reattach.
- Never delete by a glob in a box's tree: name each entry you delete, or delete a directory you made.
