# Kie voice connection

The signed-in owner can save their funded Kie account key at
`/studio/providers/kie`. It uses the existing Studio cookie, same-origin POST,
bounded single-field password form and response privacy headers. The immutable
credential record is encrypted with `APP_ENCRYPTION_KEY`, a Kie-specific domain
and its exact storage namespace. It is available to every server replica without
copying secrets through chat, git, logs or the user's computer.

Saving a key does not activate production or make a paid request. The next
commissioning checks are authenticated balance access, supported narrator IDs,
actual audio/timing output and independent speech quality. No existing
ElevenLabs allocation, replay receipt, historical period or channel is changed.

Verified public Kie contracts on 2026-09-24: account balance is read through
`GET https://api.kie.ai/api/v1/chat/credit`; generation uses
`POST /api/v1/jobs/createTask`; accepted work is observed through
`GET /api/v1/jobs/recordInfo?taskId=...`. Turbo 2.5 lists six Kie credits per
1,000 characters; Multilingual v2 lists twelve. One Kie credit lists $0.005.
The price comparison is not a provider invoice or evidence of measured quality.
