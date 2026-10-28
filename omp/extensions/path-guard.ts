// Keep a scheduler-launched step agent's writes, and optionally its reads, inside the directories
// its scheduler names.
//
// This is a guard against mistakes and drift, not a security sandbox. It reads the paths a tool
// call names and refuses the call when one is outside the list; an agent that means to get past it
// can (a script run from bash, git, a path built at run time), and it does not try to stop that.
//
// SCHED_ALLOW_WRITE is a colon-separated list of directories. When it is set, a `write`, `edit` or
// `ast_edit` call whose target is outside every listed directory is refused, and so is a `bash`
// command that writes outside them through a redirection (`>`, `>>`, `>|`, `&>`, `>&file`, `<>`),
// `tee`, `cp` (its destination), `mv` (its sources and destination), `rm`, `rmdir`, `touch`,
// `mkdir` or `sed -i`. Every `eval` call is refused too: its code's writes cannot be read from the
// call, and in the guard's first live run a haiku step agent used eval to write outside its list.
// SCHED_ALLOW_READ is a second list, for context control: when it is set, a `read`, `grep`, `glob`
// or `ast_grep` call outside it is refused, and so is a `bash` input redirection (`<`) or
// `cat`/`head`/`tail` operand, or a `cp` source, outside it. A session may
// always read what it may write, so the read list is SCHED_ALLOW_READ plus SCHED_ALLOW_WRITE.
// With neither variable set (or both empty), the extension registers no handler, so a session
// without them runs exactly as before.
//
// List entries should be absolute; `~` expands, and a relative entry resolves against the process's
// directory at load. A target is inside a directory when its path, as written or with its symlinks
// resolved, is the directory or under it. Internal URLs (`local://`, `xd://`, `skill://`, …), web
// URLs and the /dev/null-style devices always pass. The refusal names the targets and the allowed
// directories, and nothing in the refused call runs.
//
// The lists are read once, when the extension loads. Edit targets come from omp's own edit parser,
// through running-script-guard.ts's `targetsOf`; the bash lexer and the checks are in
// path-guard-core.ts. It fails open — the call runs and one debug line goes to omp's log — on any
// exception in the check.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { createPathGuard } from "./path-guard-core";
import { targetsOf } from "./running-script-guard";

export default function pathGuard(pi: ExtensionAPI): void {
	createPathGuard(process.env, targetsOf)(pi);
}
