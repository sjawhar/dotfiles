# A docker command that never returns

## `docker rm -f` or `docker inspect` hangs

`docker ps -a` still answering means the daemon is alive; a dead daemon fails both commands. A daemon that answers while `docker inspect <id>` hangs is stuck in the kernel unmounting that container's filesystem. Seccomp is not the cause: a dropped seccomp listener returns ENOSYS at once, while a stuck unmount waits.

Confirm it in `/proc` instead of guessing. The thread's `wchan` is world-readable; its `syscall` needs root. Expect `wchan=sync_inodes_sb` and `syscall=166` (`umount2`):

```bash
for t in /proc/$(pgrep -x dockerd)/task/*; do
  awk '$3=="D"{f=1} END{exit !f}' "$t/stat" &&
    echo "$t wchan=$(cat "$t/wchan") syscall=$(sudo -n cut -d' ' -f1 "$t/syscall")"
done
```

- Do not retry the removal: each try parks another daemon thread in the same wait.
- Do not restart the daemon: only this one container is stuck, and a restart stops every other session's containers.
- Do not trust "healthy" from `docker ps`. `grep -l <container-id> /proc/[0-9]*/cgroup` printing nothing means the container's init is already gone, whatever `docker ps` says.
- The hang clears when the unmount finishes, or not until a reboot. Send the user the container id and the thread line above, leave the container alone, and carry on with the rest of your task.
