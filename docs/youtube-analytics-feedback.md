# Optional audience feedback

`/studio/analytics` displays a separate owner-authorized Analytics report. Existing upload credentials, channel connection IDs, jobs, production profiles, funding and publication controls stay intact when the owner adds this grant. The extra consent asks only for `yt-analytics.readonly` and `youtube.readonly`; it verifies the same selected channel and stores its encrypted refresh token bound to the current production credential and connection. The ordinary OAuth epoch, browser binding, PKCE and single-use state protections still apply. Wrong-channel or concurrent connection changes discard the grant.

The server checks hourly, with a six-hour per-channel refresh interval and no immediate retries. Without a grant there are no token exchanges or Analytics requests. Sampling requires recent Data API evidence that each stored, successfully uploaded video still belongs to the connected channel and is public/available. A report reads at most 50 videos, followed by retention curves for at most two videos with at least 100 relevant views. Calls are read-only, have bounded timeouts and commit only if the credential, channel, membership and authorization epoch still match. No API failure revokes the upload connection. Every Studio GET uses cached observations only.

The requested period is 28 days ending three days ago. Google can return less recent data; this is not a promise of complete coverage through the requested date. Missing reports are unknown rather than zero. Shorts use engaged views for the minimum sample; ordinary views and replay-driven percentages are shown accurately, including percentages over 100. Partial/invalid rows reject the aggregate; an unavailable retention curve does not discard a valid aggregate. Reports older than 36 hours or with a failed latest refresh provide no editorial hints.

Next-series generation optionally receives two higher-retention Shorts examples, only when at least three Shorts each have 100 engaged views in the report. This is tentative, observational guidance, never a causal claim, revenue prediction, topic-repeat permission or quality approval. The existing authoritative planning context and daily reservation remain unchanged. Unavailable feedback does not block planning or trigger a paid replacement call.

Official contracts checked September 21, 2026:
- https://developers.google.com/youtube/analytics/channel_reports (authorization, video reports, retention)
- https://developers.google.com/youtube/analytics/reference/reports/query (date coverage and query contract)

Not demonstrated by offline tests: the owner's extra consent, Google project Analytics API enablement, populated real reports, quality-approved automatic publication, or audience improvement. Abacus entitlement renewal remains deferred by the owner.
