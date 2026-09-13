# Reading a captured STORY response

A complete provider response can be retained even when the live observer rejects
its protocol shape. The text adapter accepts the optional choice metadata
`native_finish_reason` only when it is the exact string `STOP`; the ordinary
`finish_reason` must still be `stop`. Missing metadata preserves the existing
result and evidence contract.

`read_retained_transport_story_evidence` interprets authenticated saved bytes
without sending another request. It checks the closed completion selection,
historical records, original reservation, permanent capture intent and anchor,
private encrypted object, and packet boundaries. Prepared and outgoing bytes
keep separate hashes; their canonical JSON must match the original wrapped
request identity.

The reader reconstructs the full STORY request from the original manifests,
metadata, decoded voice and source options. Only an exact request match may
reach the shared pure response parser and the existing complete story semantic
validator. A watched read acknowledgement must succeed before returning a
closed component-evidence object. It does not replay an HTTPX observer or change
the original unknown journal slot.

The capture contains selected header summaries, not the complete original
header list. Its evidence therefore does not assert a replay of the live header
checks or a historical settlement acknowledgement. Schema matching and model
booleans alone are insufficient for semantic acceptance.

Component evidence grants no final-edit approval, claim, retry, publication or
series resumption. Any remaining VISUAL, ASR and prosody requests require a
separate explicit continuation that preserves the existing occupied requests;
this reader cannot commission one or authorize another STORY request.
