# Reading completed retained publications

`read_retained_publication_history` verifies the permanent admission manifest,
claim journal and anchor, child mapping, execution and dispatch intent, original
publication plan, every ordered upload/asset/release receipt, completed child,
upload registry and individual series-number assignment. It acknowledges the
watched read before returning the original completion receipt.

This historical read deliberately does not depend on the current deployment,
profile, OAuth epoch, shared series counter, budget state, or job-list index.
Those records can change after publication. The receipt records the public
visibility observed at completion; it is not a fresh YouTube visibility check.

The dedicated worker uses this read for a duplicate delivery or lost completion
acknowledgement. It returns the existing result without another upload, asset
request, public release, spending reservation or scheduler write. Missing,
changed, expiring or concurrently modified permanent evidence still rejects
the read. Publication itself retains the original strict current-state checks.

Historical completion does not authorize the next production. The returned
receipt continues to say `next_production_authorized=false`.

`retained_schedule_continuation` now performs the separate reconciliation for
the preserved Capital episode five. It requires that historical receipt, all
original source/retry/cap records, the unchanged failed schedule and profile,
individual episode-five assignment and global counter five. It also verifies
the current published-to account, OAuth consent epoch, registered credentials,
global idleness, absence of owner holds and current enforced funding capacity.
Missing or exhausted funding leaves the pause and next topic untouched.

One watched transaction adds a permanent continuation audit, removes the
verified failure pause, sets the next due time and migrates the old schedule
connection to the current delivery connection. It preserves the original
failed job, cursor, consumed topics, publication receipts and all accounting.
It does not queue a task or reserve money. The next ordinary scheduler tick
admits topic six using its usual limits and every actual paid request still
requires a fresh atomic reservation. An acknowledged historical audit makes
repeated explicit reconciliation read-only, including after later episodes.
Ambiguous writes never trigger a reconstruction or replay of old work.
