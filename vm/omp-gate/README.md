# omp gate VM

Provisioning for the gate VM that oh-my-pi lanes build and test Rust on, because
the agent boxes have no cargo. `toolchain/` builds `/data/toolchain` on a fresh
machine from nothing; `toolchain/README.md` and `toolchain/MANIFEST.md` say what
it installs and how it was measured.

Run it on a new VM with:

```
scp -r toolchain/ <vm>:/tmp/tc/
ssh <vm> 'cd /tmp/tc && ./provision.sh'
```

## Why this is here and not in the fork

`/data` on the gate VM is a RAID 0 over local NVMe with no fstab entry, so a
reboot returns the machine with the toolchain absent and this tree is what
rebuilds it. It lived under a fork checkout's `.scratch/`, which is swept and
untracked by design — one `rm -rf` from gone, taking the only copy of the
provisioning with it.

## What is deliberately not here

The machine's own wiring stays out of version control, on this box only:

- its SSH private key, public key and `known_hosts`
- `ssh_config`, which names a public IP and absolute paths on one machine
- the cloud provider's `create.json`: subscription id, resource group, MAC,
  public and private addresses

Those are per-machine state and one of them is a credential. A new machine gets
new ones; only the provisioning is portable, and only the provisioning is here.
