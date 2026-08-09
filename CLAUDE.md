# Detour Project Instructions

`DETOUR_SPEC.md` is the authoritative product and engineering specification. Read it before modifying the repository.

Follow the same locked decisions and working rules in `AGENTS.md`. Do not expand scope or swap frameworks/models without a verified technical blocker and an explicit explanation.

When asked to implement a phase:
- inspect relevant `_reference/` files first;
- implement only that phase and prerequisites;
- keep changes minimal and production-minded;
- run the relevant tests/smoke checks;
- report what works, what failed, and the next recommended action.

Never expose or persist hidden chain-of-thought. Agent tracing must contain only observable actions, safe summaries, timings, statuses, and tool/model metadata.
