# Core dumps

A core dump is crash evidence before it is space. It can land in the crashed process's cwd.

- Identify it before you delete it: `file core`, `gdb -batch -ex bt core`. In a box, where `dmesg` and `journalctl` are unreadable, it can be the only crash evidence.
- A matching restart time does not prove which tool crashed; the backtrace does.
