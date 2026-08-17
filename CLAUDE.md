# CLAUDE.md

Agent instructions for this repository live in [`AGENTS.md`](AGENTS.md). Read that file before
making any change; it is the same content other tools load, kept in one place so the two cannot
drift apart.

Two rules are repeated here because skipping them is what most often goes wrong:

1. **Write the failing test first and read the failure** before writing implementation code.
2. **Verify with command output**, not with reasoning about what the code should do.

The design decisions behind the current architecture are recorded in
[`docs/superpowers/specs/`](docs/superpowers/specs/), and the implementation plans they produced
are in [`docs/superpowers/plans/`](docs/superpowers/plans/). Both were written before the code
and are kept current when a decision is superseded.
