# Abacus review protocol: 20 September 2026

The latest retained visual review received HTTP 400 with `UserFeedbackError` and
the message “Request contains an invalid argument. Please check and try again.”
The provider did not name a field. This does not establish a billing failure,
provider outage, image-count limit or a particular schema defect.

A read-only inspection of the encrypted request and response verified that the
prepared and transmitted JSON bodies match. All 30 original JPEG inputs pass
the adapter's bounded full decode. The request uses `route-llm`, text output,
8,192 maximum output tokens and strict JSON Schema. The native schema has 37
nodes, depth four, complete required-property lists and closed objects. The
complete original validation schema is also bound to the request. The earlier
`uniqueItems` and enum compatibility changes did not resolve this newer error.
No provider request, observer settlement or replay occurred during inspection.

The official [image input reference](https://abacus.ai/help/developer-platform/route-llm/chat-completions/image-analysis)
documents multiple base64 images. The [chat reference](https://abacus.ai/help/developer-platform/route-llm/chat-completions)
documents both `json_schema` and `json_object` responses. These references do not
identify the cause of the earlier rejection. The complete 30-frame request with
its original rubric still requires a separately admitted compatibility check;
the failed request is not retried or treated as approved.

The [billing FAQ](https://abacus.ai/help/chatllm-ai-super-assistant/faqs/billing)
distinguishes unlimited ChatLLM UI models from RouteLLM API usage, which consumes
credits. On 20 September the owner reported 15,768 remaining credits and automatic
credit purchasing disabled. These are owner reports, not an API balance lookup;
unknown historical cash usage remains unknown.

A separate one-use diagnostic then sent a synthetic 32×32 red JPEG with JSON
Object output and a 128-token ceiling. It received HTTP 200 and the expected JSON
color result. The complete response was preserved privately. Observed token
usage was 46 input and 33 output tokens; no credit charge was captured by the
diagnostic. This confirms acceptance of that small request, not the original
30-frame request, an identified cause of its HTTP 400, or production quality.
Reading those saved bytes also exposed a parser mismatch: the successful response
reported `native_finish_reason: "stop"`, while the parser accepted only `"STOP"`.
Both observed terminal spellings are now accepted, with the canonical
`finish_reason: "stop"` still mandatory. Truncation, tool calls and other native
finish values remain rejected. The same response also includes a nonnegative
integer `reasoning_tokens` counter (19), which is now preserved alongside the
three observed native token counters. No arithmetic relationship or credit/USD
conversion is inferred. Unknown fields and malformed counters remain rejected.
The saved response contains only the synthetic
color result and protocol metadata; it is a regression fixture, not a new wire
observation or a full-video quality proof.

The explicit `prepare_json_object_router_request` builder now preserves the
entire original rubric, ordered JPEGs and validation schema in the bound request.
It uses JSON Object output with deterministic temperature and enforces the same
complete schema locally, including enum types, unique arrays, required fields
and bounds. Missing, duplicated, moved or changed schema contracts are rejected.
Default native-schema requests retain their existing bytes. There is no automatic
fallback or provider retry, and the builder grants no sending or QA authority.
The expired retained controller and its occupied requests remain closed. A new
current-authority admission path and real full-rubric/audio/final QA are still
required before production can resume.
