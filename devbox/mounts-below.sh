# shellcheck shell=bash
# mounts-below.sh: is a filesystem mounted inside a tree a cleaner is about to remove? Sourced, never
# run, by devbox/session-scratch-reap, devbox/tmp-stale-reap and devbox/nvme-cache-cap, the
# cleaners here that remove whole trees.
#
# `rm -rf` removes everything it reaches, a filesystem mounted below the tree included: the files of
# another disk or tmpfs, or, through a bind mount, of another directory. Each of those cleaners
# removes with `rm -rf --one-file-system`, which skips a directory on another device, so a mount is
# never crossed even when this check misses it, and the removal then fails and is said. That flag
# compares devices only, so a bind mount of a directory on the same filesystem still passes it.
# /proc/self/mountinfo names every mount point in this process's namespace, bind mounts too, and a
# cleaner that finds one at or below an entry keeps the entry and says why. A mount in another
# namespace (a container's or a sandbox's) is not on this process's path, so this process's rm
# cannot reach it, and it is not listed.

# mount_below DIR: print the first mount point at or below DIR and return 0; return 1 when there
# is none, and 2 when the mount table cannot be read (a caller keeps the entry then). Field 5 of
# mountinfo is the mount point, with a space, tab, newline and backslash written as \040, \011,
# \012 and \134. MOUNTINFO replaces /proc/self/mountinfo for a test.
mount_below() {
    local table="${MOUNTINFO:-/proc/self/mountinfo}"
    [[ -r "$table" ]] || return 2
    DIR="${1%/}" awk '
        BEGIN { dir = ENVIRON["DIR"] }
        {
            point = $5
            gsub(/\\040/, " ", point); gsub(/\\011/, "\t", point); gsub(/\\012/, "\n", point)
            gsub(/\\134/, "\\", point)
            if (point == dir || index(point, dir "/") == 1) { print point; found = 1; exit }
        }
        END { exit !found }' "$table"
}
