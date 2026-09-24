# Optional audience feedback

`/studio/analytics` displays a separate owner-authorized Analytics report. Existing upload credentials, channel connection IDs, jobs, production profiles, funding and publication controls stay intact when the owner adds this grant. The extra consent asks only for `yt-analytics.readonly` and `youtube.readonly`; it verifies the same selected channel and stores its encrypted refresh token bound to the current production credential and connection. The ordinary OAuth epoch, browser binding, PKCE and single-use state protections still apply. Wrong-channel or concurrent connection changes discard the grant.

The server checks every five minutes. Successful reports, quota failures and permission failures retain a six-hour per-channel interval. Temporary service failures and a newly enabled Analytics API are checked again after fifteen minutes; there are no immediate retries. The owner view explains a failed read and shows the earliest next check time. Without a grant there are no token exchanges or Analytics requests. Each refresh reads the authenticated channel's uploads playlist and verifies current ownership and public visibility with the Data API. At most the 50 newest uploads are inspected. Older videos remain eligible after reconnecting a channel, even when their original job has a different connection ID or is absent from Studio. This separate Analytics inventory never becomes publication or repair authority. A report reads only the verified public subset, followed by retention curves for at most two videos with at least 100 relevant views. Calls are read-only, have bounded timeouts and commit only if the credential, channel, membership and authorization epoch still match. No API failure revokes the upload connection. Every Studio GET uses cached observations only.

The requested period is 28 days ending three days ago. Google can return less recent data; this is not a promise of complete coverage through the requested date. Missing reports are unknown rather than zero. Shorts use engaged views for the minimum sample; ordinary views and replay-driven percentages are shown accurately, including percentages over 100. Partial/invalid rows reject the aggregate; an unavailable retention curve does not discard a valid aggregate. Reports older than 36 hours or with a failed latest refresh provide no editorial hints.

Next-series generation optionally receives two higher-retention Shorts examples, only when at least three Shorts each have 100 engaged views in the report. This is tentative, observational guidance, never a causal claim, revenue prediction, topic-repeat permission or quality approval. The existing authoritative planning context and daily reservation remain unchanged. Unavailable feedback does not block planning or trigger a paid replacement call.

Pacing observations also measure the strongest decline across a window spanning
5–10% of a video's running time. Looking only at adjacent 1% samples missed the
gradual early loss present in actual channel reports. This measurement retains
replay ratios above 1 and does not reinterpret them as unique viewers. A single
same-format video with at least 100 relevant views can supply a descriptive pacing
observation to the writer and next-series planner. Cross-video ranking still
requires three qualifying videos. Sparse curves, insufficient samples, other
formats and failed/stale reports do not provide this advice. The suggested earlier
evidence or payoff is a hypothesis for new scripts, not a cause or reach guarantee.

The distinction between appeal, engagement and satisfaction follows YouTube's
[performance guidance](https://support.google.com/youtube/answer/16559650?hl=en)
and [Shorts discovery guidance](https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en-GB).
Search interest is not YouTube distribution; no universal retention threshold
or guaranteed view count is inferred from these observations.

Official contracts checked September 21, 2026:
- https://developers.google.com/youtube/analytics/channel_reports (authorization, video reports, retention)
- https://developers.google.com/youtube/analytics/reference/reports/query (date coverage and query contract)

Google project Analytics API enablement is separate from the channel owner's consent. A disabled API must not be presented as an expired channel connection. Offline tests do not demonstrate populated real reports, quality-approved automatic publication, or audience improvement. Abacus entitlement renewal remains deferred by the owner.
