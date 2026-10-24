# LLM evaluation engineering

[← Field guides](README.md) · Prerequisite: [Measurement](measurement.md)

**Outcome:** compare model or prompt configurations using a reproducible dataset, calibrated scoring
and explicit failure accounting. This lesson covers Experimently's evaluation workflow, not current
provider availability or pricing. Check those separately before making a paid call.

## Offline evaluation and online experimentation answer different questions

An offline dataset asks how configurations perform on a known task distribution. An online randomized
experiment asks how deploying them changes outcomes for eligible users. A dataset can underrepresent
production traffic, and a judge can reward prose that does not improve customer outcomes. Use offline
evaluation to reject obvious regressions, then validate the business effect through an appropriately
designed online experiment.

At the reviewed snapshot, the [LLM endpoints][routes] support experiment configuration, completion,
evaluation, judging and results. The [quick start][quickstart] shows the supported requests. This
guide recommends a dataset manifest and replay discipline; it does not claim the platform provides a
full dataset registry, paired-test engine or automated promotion pipeline.

## Specify the experiment artifact

Keep a versioned, access-controlled manifest containing the dataset hash, case IDs, input schema,
expected labels or scoring rubric, slices, prompt template and system prompt, model identifier,
provider route, sampling settings, token limit and tool/retrieval configuration. Record the judge
configuration separately. Avoid storing customer text in ordinary source control.

Pin what can be pinned. A provider alias may change underneath an unchanged application config.
Capture request time and returned provider metadata where available. Setting temperature to zero
does not establish byte-for-byte reproducibility across service versions or hardware.

Build the dataset from the intended use cases: straightforward requests, ambiguous inputs, long
contexts, multilingual cases, refusals, unsupported tasks and failures. Keep a held-out set that is
not repeatedly used to tune prompts. Record slice sizes; an overall average can conceal a regression
in the small population that most needs the feature.

## Score the task, not the style

For classification, exact labels may be meaningful. For free text, define an explicit rubric with
examples of acceptable and unacceptable answers. Human reviewers should not see the model identity
when it could influence scoring. Calibrate reviewers on shared cases and measure disagreement.

An LLM judge is a fallible measurement instrument. Compare it with human labels on a calibration
subset, test order and verbosity bias, and include adversarial responses that tell the judge to
ignore the rubric. Treat response text as untrusted content, especially when it is inserted into a
judge prompt. Do not give the evaluation process production tools or credentials it does not need.

The implemented [judge path][analytics] can return **0.5 with fallback reasoning** after provider or
parse failures. Such a record is not evidence that quality was exactly average. Inspect reasoning
and execution logs before interpreting scores; report fallback counts separately. If the current
results response cannot filter them in the way your analysis requires, use a validated separate
analysis rather than silently treating infrastructure failures as quality observations.

## Keep the denominators visible

An illustrative replay begins with 100 cases per configuration. Configuration A produces 95 valid
responses with mean valid-response score 0.8; configuration B produces 100 with mean 0.78. Reporting
only 0.8 versus 0.78 hides A's five failures. Under a predeclared failure-as-zero rule, A's overall
mean is 0.76 and B's is 0.78. Another policy might classify safety refusals differently, but it must
be written before seeing which model benefits.

Track attempted cases, provider failures, timeouts, invalid outputs, valid scored cases and judge
fallbacks independently. Count latency and cost for retries as well as successful responses in your
operational accounting. Missing cost is not zero cost: [pricing logic][pricing] is a built-in estimate,
not a substitute for provider billing, and an unpriced model can be recorded as zero.

## Use the analysis that matches the observation unit

If each dataset case is sent to both configurations, the results are paired by case. Repeated outputs
from one case or one user are not automatically independent. Preserve these identifiers for a paired
or clustered analysis outside the platform when its built-in analysis does not match the design.

Read [LLM analytics][analytics] before interpreting the winner label. At this snapshot, winner selection
uses the largest available mean business metric, falling back to mean automatic score. It does not
by itself implement your cost/latency guardrails, prove practical significance or authorize deployment.
Likewise, mean latency does not establish a p95 latency objective.

Online, assignment is user-based and deterministic in the [experiment service][service]. A case-paired
offline replay needs an explicit harness and mapping; calling ordinary user assignment does not
guarantee every case is evaluated against both arms. Keep offline comparison separate from live routing.

## Exercise and worked answer

You see a tightly concentrated score of 0.5 for one model, a “winner” label for another, and $0 cost
for a new model identifier. What should happen before promotion?

**Answer:** inspect judge reasoning and failures for fallback scores; validate provider execution and
the pricing table; recompute the intended quality metric with the declared missing/failure policy.
Check dataset slices, human calibration, observation dependence, uncertainty and cost/latency limits.
Treat the winner label as a summary of the implemented means, not an approval. Re-run the supported
quick start only in a controlled environment with a budget and appropriate credentials.

**Review artifact:** dataset/configuration manifest, calibrated rubric, failure table, slice analysis
and a promotion decision stating which checks were automated and which were performed separately.

[routes]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/v1/endpoints/llm_experiments.py
[quickstart]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/llm-evaluation/quickstart.md
[analytics]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/llm_analytics_service.py
[pricing]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/llm_pricing.py
[service]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/llm_experiment_service.py
