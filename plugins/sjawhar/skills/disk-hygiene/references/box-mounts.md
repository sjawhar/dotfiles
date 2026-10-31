# Box bind-mount sources: `jj-user-repos` and `omp-run`

`<boxes>/<box>/jj-user-repos` and `<boxes>/<box>/omp-run` are the live sources of the box's `~/.config/jj/repos` and `~/.omp/run` bind mounts, where `<boxes>` is `/mnt/nvme/<user>/agentbox/boxes` on a machine with that disk and `~/boxes` on one without (`findmnt -T ~/.omp/run` names the one in use). They hold no repository but are not scratch: their loss breaks `jj`, or omp's daemon broker, for the whole box.

- Symptom: `jj` fails `Failed to determine the secure config for a repo` over `EROFS`. `findmnt -T <path>` or `/proc/self/mountinfo` shows `…/jj-user-repos//deleted`.
- Fix: make the directory again, `sudo umount` the stale target, then `sudo mount --bind` it back. The directory alone does not reattach.
