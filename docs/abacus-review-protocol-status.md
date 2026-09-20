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
documents both `json_schema` and `json_object` responses. A future separately
budgeted compatibility diagnostic could test JSON Object output with the full
original schema still enforced locally. That is a hypothesis to test, not an
implemented fallback or verified remedy; the current request is not retried.

The [billing FAQ](https://abacus.ai/help/chatllm-ai-super-assistant/faqs/billing)
distinguishes unlimited ChatLLM UI models from RouteLLM API usage, which consumes
credits. Account credit balance and automatic purchasing still require account
evidence. No unknown historical cash usage is treated as zero.
