# Production spend control: foundation, not yet enforced in production

This change adds an offline-tested reservation primitive only. It does not
initialize production Redis, alter settings, call paid providers, intercept
HTTP, publish videos or claim a $500 all-in cap is already enforced.

## Financial scope

- Proposed combined-channel cash envelope: $500/month, not a spending target.
- Proposed variable API allowance: approximately $260/month. Fixed bills,
  infrastructure, asset/music allowances, taxes and contingency stay outside
  that API ledger but INSIDE the $500 envelope.
- A valid all-zero policy prohibits paid dispatch while free local editing
  and accepted-operation polling remain possible. Legitimate free quotas or
  prepaid credits require provider-specific quota/cash accounting; their expiry
  must NOT automatically enable a paid route. No repeated fake trial accounts.
- Included subscription credits and actual cash overage need separate
  reconciliation. This ledger counts reserved upper bounds, not invoices.
- Remaining-period initialization must account for existing/in-flight usage.
  It must never silently reset the whole current month to $0 already spent.
- Existing personal ChatGPT Pro/Google subscriptions and their extra credit
  purchases are separate; this application cannot cap purchases in those apps.

## Required integration before enabling

1. Load one operator-approved policy. Initialize the durable, no-eviction Redis
   ledger explicitly once; never bootstrap it when a provider request finds
   missing state. Calendar periods use UTC. Every replica uses a synchronized
   clock and the same ledger/policy; legacy expiring job writes cannot touch it.
2. Derive channel, format and root cost lineage from verified stored jobs.
   Repairs use the same root lineage, not a fresh queue task's allowance.
   Derived Shorts are charged to a verified long-form family with its `long`
   budget kind, not reclassified into a fresh budget. Independently budgeted
   originals have independent lineages. Caller-provided IDs cannot mint money.
3. Obtain a reviewed quote with hard bounds on input, output tokens, tool
   calls, duration, resolution and attempt count. Unknown models/prices block
   paid dispatch. `SpendQuote` holds this upper bound; it does not calculate
   prices or validate a provider's request shape by itself.
4. Persist one submission/request identity in the existing durable provider
   intent before reservation. Call reserve immediately before ONE POST.
   Only an acknowledged new reservation permits that POST. Duplicate keys,
   lost Redis replies or uncertain provider outcomes never permit blind replay.
   Read/poll a previously accepted provider operation without another create.
5. Cover ALL paid paths: OpenAI planning/research/next-series/visual critic,
   Gemini JSON/critic/audio review/image/video, ElevenLabs TTS/design/effects,
   Runway and fal creates, future MiniMax/HeyGen and every fallback/SDK retry.
   Every worker thread must preserve the verified spending context. No paid
   route may fall through to an unguarded client if its context/price is absent.
6. Keep the existing request-count guard and artifact checkpoints. A budget
   failure allows free polling, existing-asset editing and other independently
   eligible work. It never approves rejected media or resets paid history.
7. Expose safe budget status in Studio and reconcile provider usage. Do not
   release reserved money on a timeout/error unless a later audited process
   proves non-billing; the foundation intentionally does not implement refunds.
8. Test first with stub transports, then one <=$5 reference acceptance clip.
   This tests integration/style, not a statistically proven 83.3% usable-clip
   yield or monthly throughput. An eight-minute long + three derived Shorts
   and post-publication continuation are later separate acceptance gates.

No new paid package or trial-account cycling is required by this module.
