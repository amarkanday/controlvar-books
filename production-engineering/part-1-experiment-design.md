# Part I — Experiment Design and Decisions

### Hours 1–3

[Contents](./README.md) · [Part 2 →](./part-2-reliable-systems.md)

This Part follows the decision loop: define what counts, run an experiment through a shipping
decision, then extend the same discipline to model and prompt evaluation. The worked data is
illustrative; the source links identify the reviewed implementation.

> **Verified against** getexperimently/experimently at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
> (source review, 2026-10-23). Recommended procedures and illustrative examples are identified
> in the lessons; live-stack exercises and paid provider calls were not run for this edition.

---

## Hour 1 — Measurement and experiment design

**Outcome:** write a metric contract that another engineer can implement without guessing what counts
as a user, an exposure or a conversion. This guide assumes basic familiarity with assignments and events.

### What & why

#### Start with the decision

“Make checkout better” is not a testable hypothesis. An illustrative decision is: “Ship a clearer
checkout summary if it improves the probability that an eligible shopper completes a purchase, while
keeping checkout errors below our agreed threshold.” Write the population, treatment, primary metric,
minimum useful effect, guardrails and decision method before collecting experiment traffic.

Choose the randomization unit deliberately. If one shopper can see both variants through different
device identifiers, the experiment is partly comparing devices, not stable shopper experiences.
If treatment affects an entire account, assigning account members independently can contaminate
control. An account-level experiment needs account-level identifiers and an analysis appropriate to
that unit; merely renaming a user ID does not make repeated observations independent.

### How Experimently does it

#### Define a metric contract

| Field | Illustrative checkout contract |
|---|---|
| Population | Shoppers eligible to see the checkout summary |
| Assignment unit | Stable opaque shopper identifier |
| Primary event | `checkout_completed` |
| Outcome | At least one matching conversion for an assigned shopper |
| Denominator | Assigned shoppers in the variant, not raw page views |
| Business horizon | Seven days after assignment, documented in the analysis plan |
| Exclusions | Synthetic and internal traffic excluded using pre-treatment eligibility |
| Guardrails | Checkout errors and latency, with explicit units and data sources |
| Decision | Predeclared method, confidence level, correction and minimum useful effect |

The business horizon above is a **recommended analysis requirement**, not a claim that the default
results endpoint enforces a configurable seven-day attribution window. Inspect its event selection
and use a separately validated analysis if the required window is not implemented. A label in a plan
does not change which rows the server counts.

#### Trace the implemented numerator and denominator

Read [event matching][h1-matching] alongside the [assignment model][h1-assignments]. Binary conversion
counts distinct converting users, not events. Matching uses the metric's event name and excludes
exposure event types. A contributing user must be assigned to the same experiment and variant as
the event. The unique assignment index covers experiment and user.

Assignment, exposure and conversion are different facts. Assignment records the allocation;
exposure records that an experience was encountered. Rendering a control fallback for an ineligible
user does not necessarily create an assignment. Do not replace the assignment denominator with
exposure counts without explicitly defining a different estimand and checking selection bias.

Illustrative reconciliation: control has 100 assignments and 20 matching purchase events from 10
assigned shoppers. Treatment has 100 assignments and 18 events from 12 assigned shoppers. Binary
rates are **10% and 12%**, a two percentage point difference, or 20% relative lift. Counting event
rows would incorrectly suggest control performed better. These counts alone do not establish a
shipping decision or adequate sample size.

#### Validate instrumentation before interpreting lift

Run a small synthetic cohort with known expected outcomes. Reconcile assigned users, matching
events, distinct converting users and experiment/variant associations. Test missing assignment,
repeated conversion, exposure-only activity, an unknown key and a user outside targeting.

Use an A/A rehearsal to find asymmetric routing and instrumentation. An A/A test does not prove
future tests are unbiased; it is one opportunity to observe the full collection path. Inspect the
[sample-ratio check][h1-srm] before looking for a winner. An unexpected allocation imbalance can come
from eligibility, identity churn, assignment errors or unequal collection loss. Investigate it rather
than repairing the allocation by discarding inconvenient users after seeing outcomes.

Decide bot and internal-traffic exclusions before treatment. A filter based on behavior treatment
can change may condition on the outcome and bias the result. Record exclusion counts and apply the
same policy to both arms.

#### Time, power and repeated looks

Distinguish event time from server receipt time. For [CUPED][h1-cuped], history must happen in each
user's pre-assignment window and must also have reached the server before assignment. Backdating
an event submitted afterwards does not turn it into a valid pre-treatment covariate.

Use [power analysis][h1-power] to plan a useful detectable effect and traffic requirement. The platform's
minimum sample threshold is not evidence that your particular effect has adequate power. Cover
relevant weekly cycles and conversion delays. Choose fixed-horizon or sequential inference before
launch; repeatedly checking ordinary fixed-horizon p-values and stopping at the first small one
does not preserve the nominal error rate. Review the sequential route's current limitations before
using it. Account for multiple treatments and metrics rather than reporting the best uncorrected result.

### Try It Yourself

You have 80 assignments in each arm. Control has 16 events from eight assigned users. Treatment has
12 events from ten assigned users, plus four events from unassigned users. What is the binary lift?
What else do you need before shipping?

**Answer:** control is 8/80 = 10%; treatment is 10/80 = 12.5%. The absolute difference is 2.5
percentage points and relative lift is 25%. Unassigned users do not enter this conversion numerator.
You still need eligibility and SRM checks, the planned sample/runtime, uncertainty and multiplicity
handling, guardrail results and the agreed decision rule. Event totals are not the primary metric.

### Key takeaways

A metric contract defines the unit, event and denominator before traffic arrives. Reconcile
converting users with stored assignments, and keep the planned attribution and stopping rules explicit.

**Review artifact:** save the metric contract, synthetic cohort reconciliation and analysis plan with
the experiment. Another engineer should be able to explain every denominator before launch.

---

## Hour 2 — A complete experiment: from hypothesis to removal

**Outcome:** produce a defensible decision and an operational handoff, including the cases where you
do not ship. This worked checkout experiment is illustrative; none of its traffic or outcomes are
claimed as production results.

### What & why

#### 1. Write the plan before creating traffic

The hypothesis is that a clearer checkout summary increases purchase completion. Control is the
current summary; treatment changes only that summary. Use a stable shopper ID and an eligibility
rule evaluated before treatment. Choose `checkout_completed` as the primary binary event. Specify
the smallest useful effect, conversion maturation period, method, confidence level, multiplicity
policy and minimum planned runtime. Guardrails cover checkout errors and latency.

Use the [power guide][h2-power] with a measured baseline and expected eligible traffic. Do not choose a
sample size by copying this lesson. Forecast runtime after eligibility and traffic allocation, not
from total website visits. Record who can stop the experiment for operational harm.

### How Experimently does it

#### 2. Rehearse the public integration path

Start the disposable stack using [onboarding](../guides/onboarding.md). Use [ShopLab][h2-shoplab] to
observe assignment, event collection and results through a real SDK integration. It is a learning
fixture; create a separate experiment for your own exercise rather than altering the demo's expected
configuration. Use synthetic shoppers and a nonproduction API key.

Create a draft using the [experiment guide][h2-experiments]. Configure the actual primary event name,
variant payloads and planned traffic. Trace the application call that requests an assignment and
renders the corresponding summary. Ensure assignment happens before a conversion is submitted.
Keep the fallback safe when no assignment is available.

For a known cohort, record assigned user IDs, variant IDs, exposures and conversions. Include one
repeated conversion, one unassigned user and one ineligible user. Reconcile the expected converting
users against results. Check the implementation's attribution behavior against the business plan;
do not assume an unimplemented attribution window exists because the plan mentions one.

#### 3. Validate readiness and start

Run an A/A or equivalent instrumentation rehearsal before interpreting a treatment difference.
Confirm eligibility, assignment balance, event associations and the data sources behind guardrails.
Review [dashboard versus API availability][h2-dashboard] so the operator knows where each action lives.
For example, creating and controlling rollout schedules may require API calls even though the
dashboard displays their stages.

Start the real experiment only after the owner signs off on the plan and instrumentation. Save its
configuration and start time. Restrict mid-experiment changes; changing targeting, identities or
metrics can change what comparison the accumulated data represents.

#### 4. Monitor integrity and harm separately from success

Check collection loss, assignment balance, schema errors and guardrails. Inspect SRM before reading
the recommendation. A safety stop protects users; it is not evidence that treatment loses under the
planned statistical test. Conversely, a favorable p-value does not excuse operational harm.

If guardrail errors spike, pause exposure using the supported experiment controls and, if the
integration uses a separately configured flag, operate that flag's kill switch. Experiment pause and
flag rollback are different controls. Do not assume completing or pausing an experiment automatically
changes every application's rendered experience. Verify the client behavior and the audit evidence.

#### 5. Make one of three decisions

| Illustrative observation | Decision and reasoning |
|---|---|
| Effect clears the practical threshold, uncertainty and correction support the decision, guardrails pass | Eligible to ship under the predeclared policy |
| Interval includes useful benefit and harm at the planned stopping point | Inconclusive; retain control and record what a future study would need |
| Guardrail breach or invalid assignment/collection | Stop or pause for safety/integrity; do not label the broken comparison a valid winner |

“Not significant” does not establish equivalence. Do not extend the run repeatedly until the answer
changes unless the chosen sequential design supports that monitoring rule. Segment differences found
after examining many cuts are exploratory until validated under an appropriate analysis plan.

Record the app and engine versions, experiment configuration, analysis time, counts, effect units,
uncertainty, correction, analysis notices and guardrail observations. A screenshot alone often omits
the settings needed to reproduce a decision. An API recommendation is input to the decision, not an
instruction to change production automatically.

#### 6. Ship with a separate rollout plan

If the decision permits shipping, implement the chosen experience behind a flag with a named owner,
safe default and removal date. Use [rollouts][h2-rollouts] and [safety monitoring][h2-safety] deliberately.
Confirm that client error and evaluation reports actually reach the configured monitor; a threshold
without its source data is not protection. Rehearse rollback in the disposable environment.

Roll out in stages, check guardrails and keep an operator available for the risk window. Safety
checks are periodic, not instantaneous. Verify server and SDK-held state after a rollback. A flag
serving the selected treatment is a separate configuration from the experiment's historical result.

#### 7. Finish the experiment and remove the branch

Complete the experiment using the supported lifecycle operation after deciding what clients will do.
Keep its decision record and configuration for auditability. Remove the losing application branch,
stop obsolete event production where appropriate, and follow [flag retirement](part-4-safe-change.md#hour-10--feature-flag-lifecycle-create-operate-and-retire).
Historical analysis may still require retained events and configuration; application cleanup does not
authorize deleting that history.

### Try It Yourself

At the planned stopping point, the observed lift is positive but its interval spans both a useful
benefit and material harm. A product deadline is tomorrow. What goes in the decision record?

**Answer:** “Inconclusive under the planned analysis; retain control.” Include the uncertainty and
guardrail evidence, the business cost of waiting, and options for a better-powered follow-up. If the
business chooses to ship for independent reasons, state that decision separately; do not rewrite the
experiment as proof of benefit. Name a rollout owner and rollback policy for any release.

### Key takeaways

An experiment can end in a ship, no-ship or inconclusive decision. Integrity, operational safety
and statistical benefit need separate evidence, followed by a rollout and cleanup handoff.

**Review artifact:** one experiment plan, one instrumentation reconciliation, a decision record and
a rollout/removal handoff. Each should name its owner and the evidence needed to pass its next stage.

---

## Hour 3 — LLM evaluation engineering

**Outcome:** compare model or prompt configurations using a reproducible dataset, calibrated scoring
and explicit failure accounting. This lesson covers Experimently's evaluation workflow, not current
provider availability or pricing. Check those separately before making a paid call.

### What & why

#### Offline evaluation and online experimentation answer different questions

An offline dataset asks how configurations perform on a known task distribution. An online randomized
experiment asks how deploying them changes outcomes for eligible users. A dataset can underrepresent
production traffic, and a judge can reward prose that does not improve customer outcomes. Use offline
evaluation to reject obvious regressions, then validate the business effect through an appropriately
designed online experiment.

At the reviewed snapshot, the [LLM endpoints][h3-routes] support experiment configuration, completion,
evaluation, judging and results. The [quick start][h3-quickstart] shows the supported requests. This
guide recommends a dataset manifest and replay discipline; it does not claim the platform provides a
full dataset registry, paired-test engine or automated promotion pipeline.

### How Experimently does it

#### Specify the experiment artifact

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

#### Score the task, not the style

For classification, exact labels may be meaningful. For free text, define an explicit rubric with
examples of acceptable and unacceptable answers. Human reviewers should not see the model identity
when it could influence scoring. Calibrate reviewers on shared cases and measure disagreement.

An LLM judge is a fallible measurement instrument. Compare it with human labels on a calibration
subset, test order and verbosity bias, and include adversarial responses that tell the judge to
ignore the rubric. Treat response text as untrusted content, especially when it is inserted into a
judge prompt. Do not give the evaluation process production tools or credentials it does not need.

The implemented [judge path][h3-analytics] can return **0.5 with fallback reasoning** after provider or
parse failures. Such a record is not evidence that quality was exactly average. Inspect reasoning
and execution logs before interpreting scores; report fallback counts separately. If the current
results response cannot filter them in the way your analysis requires, use a validated separate
analysis rather than silently treating infrastructure failures as quality observations.

#### Keep the denominators visible

An illustrative replay begins with 100 cases per configuration. Configuration A produces 95 valid
responses with mean valid-response score 0.8; configuration B produces 100 with mean 0.78. Reporting
only 0.8 versus 0.78 hides A's five failures. Under a predeclared failure-as-zero rule, A's overall
mean is 0.76 and B's is 0.78. Another policy might classify safety refusals differently, but it must
be written before seeing which model benefits.

Track attempted cases, provider failures, timeouts, invalid outputs, valid scored cases and judge
fallbacks independently. Count latency and cost for retries as well as successful responses in your
operational accounting. Missing cost is not zero cost: [pricing logic][h3-pricing] is a built-in estimate,
not a substitute for provider billing, and an unpriced model can be recorded as zero.

#### Use the analysis that matches the observation unit

If each dataset case is sent to both configurations, the results are paired by case. Repeated outputs
from one case or one user are not automatically independent. Preserve these identifiers for a paired
or clustered analysis outside the platform when its built-in analysis does not match the design.

Read [LLM analytics][h3-analytics] before interpreting the winner label. At this snapshot, winner selection
uses the largest available mean business metric, falling back to mean automatic score. It does not
by itself implement your cost/latency guardrails, prove practical significance or authorize deployment.
Likewise, mean latency does not establish a p95 latency objective.

Online, assignment is user-based and deterministic in the [experiment service][h3-service]. A case-paired
offline replay needs an explicit harness and mapping; calling ordinary user assignment does not
guarantee every case is evaluated against both arms. Keep offline comparison separate from live routing.

### Try It Yourself

You see a tightly concentrated score of 0.5 for one model, a “winner” label for another, and $0 cost
for a new model identifier. What should happen before promotion?

**Answer:** inspect judge reasoning and failures for fallback scores; validate provider execution and
the pricing table; recompute the intended quality metric with the declared missing/failure policy.
Check dataset slices, human calibration, observation dependence, uncertainty and cost/latency limits.
Treat the winner label as a summary of the implemented means, not an approval. Re-run the supported
quick start only in a controlled environment with a budget and appropriate credentials.

### Key takeaways

Evaluation quality depends on the dataset, scoring instrument and failure denominator. A largest
mean, fallback score or missing price is not a complete promotion decision.

**Review artifact:** dataset/configuration manifest, calibrated rubric, failure table, slice analysis
and a promotion decision stating which checks were automated and which were performed separately.

---

## Workshop

### Q&A

**Why not make a shipping decision from raw event totals?** The binary metric counts assigned
converting users. Repeated events and unassigned senders can change event totals without changing
the intended outcome.

**Does an LLM winner label authorize deployment?** No. Verify failures, the scoring design, useful
effect, uncertainty and operational guardrails separately.

### Quiz

1. What is the difference between percentage points and relative lift?
2. Does an inconclusive experiment establish equivalence?
3. How should a judge fallback of 0.5 be interpreted?

**Answers:** (1) Percentage points subtract rates; relative lift divides the difference by the
baseline. (2) No; the interval may still include useful benefit and material harm. (3) As an
execution/measurement issue to inspect, not proof of average quality.

### Exercise with worked solution

Write a decision record for a checkout test with valid instrumentation, positive observed lift,
an interval spanning meaningful harm and benefit, and an approaching deadline.

**Solution:** record the uncertainty and planned stopping point, label the statistical result
inconclusive, retain control under the stated rule, and describe any independent business decision
separately. A later release still needs an owner, guardrails and rollback evidence.

---

[Contents](./README.md) · [Part 2 →](./part-2-reliable-systems.md)

[h1-matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[h1-assignments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[h1-srm]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/srm_service.py
[h1-cuped]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/cuped.md
[h1-power]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/statistics/power-analysis.md
[h2-power]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/statistics/power-analysis.md
[h2-shoplab]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/demo/shoplab/README.md
[h2-experiments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/user-guide.md
[h2-dashboard]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/dashboard-and-api.md
[h2-rollouts]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/rollouts.md
[h2-safety]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/safety.md
[h3-routes]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/v1/endpoints/llm_experiments.py
[h3-quickstart]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/llm-evaluation/quickstart.md
[h3-analytics]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/llm_analytics_service.py
[h3-pricing]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/llm_pricing.py
[h3-service]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/llm_experiment_service.py
