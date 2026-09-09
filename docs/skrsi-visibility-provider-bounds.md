# SKRSI visibility provider bounds

SKRSI means **SK Recursive SELF Improvement**, where SELF is **Systematic
Evaluation, Learning, and Feedback**. The canonical project definition lives
in the [SKRSI repository](https://github.com/smilinTux/skrsi).

The dashboard authorizes the role and validates the requested visibility scope
before provider access. Provider execution then uses a fixed worker count, a
bounded admission queue, an explicit per-attempt timeout, and one bounded retry
for provider exceptions. A timeout is not retried because the original call may
still be running.

Timeout, overload, exhausted retry, stale truth, and malformed results fail
closed. The API returns only a typed error and redacted evidence hash. The same
terminal record is appended under `evidence/skrsi-visibility/terminal.jsonl`.
An optional notifier receives that redacted record and cannot convert the
failure into an authorization, actuation, or successful projection.

Timed-out work retains its capacity slot until the provider actually returns.
This prevents a hung provider from creating an unbounded thread or queue. A
later request is a clean replay and never receives cached stale truth.

Defaults are one second per attempt, two workers, two queued calls, and one
retry. Operators may lower these bounds through the application composition,
but cannot disable authorization, scope validation, freshness validation, or
projection validation.
