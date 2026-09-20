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

Historical completion does not authorize the next production. Transition from
the retained failed episode into the funded scheduler still needs its own
delivery and current-account reconciliation; the returned receipt continues
to say `next_production_authorized=false`.
