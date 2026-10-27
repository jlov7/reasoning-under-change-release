# Security

Installed runtime commands accept bounded JSON and perform offline calculations.
They do not execute task commands, call models, contact services or load plugins.
File readers refuse non-regular descriptors before bounded reads, including FIFOs,
directories and devices; symlinks to regular files remain accepted. The descriptor
check avoids a path-check/open race. Input schemas reject unknown fields, duplicate keys, nonfinite numbers and invalid
graph/accounting definitions. Size/task/resource/search limits reduce accidental
resource use; they are not a sandbox or a real-time guarantee.

Saved work outputs use owner-private sibling staging and a no-replacement hard
link after a complete write/close. Existing files and symlinks are refused. The
filesystem must support hard links. These operations do not provide crash durability,
malicious-local-writer isolation or authentication. A forced termination or cleanup failure can leave
an owned staging file. Input/plan hashes check supplied-content consistency, not
identity, authority or proof of completion. Keep sensitive queues and plans private.

Installation and build/test tooling can contact package indexes. Runtime commands
have no network dependency or telemetry. Review any added build/runtime dependency
and maintain pinned CI actions, read-only workflow permissions and disabled saved
checkout credentials.

Report a suspected vulnerability using the repository's private security reporting
channel when available. Otherwise ask the maintainer for a private reporting route
without publishing exploit details or sensitive inputs. Use a minimal synthetic
reproducer. No security certification or production suitability is claimed.
