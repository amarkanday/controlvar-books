# A complete experiment: from hypothesis to removal

[← Field guides](README.md) · Prerequisite: [Measurement](measurement.md)

**Outcome:** produce a defensible decision and an operational handoff, including the cases where you
do not ship. This worked checkout experiment is illustrative; none of its traffic or outcomes are
claimed as production results.

## 1. Write the plan before creating traffic

The hypothesis is that a clearer checkout summary increases purchase completion. Control is the
current summary; treatment changes only that summary. Use a stable shopper ID and an eligibility
rule evaluated before treatment. Choose `checkout_completed` as the primary binary event. Specify
the smallest useful effect, conversion maturation period, method, confidence level, multiplicity
policy and minimum planned runtime. Guardrails cover checkout errors and latency.

Use the [power guide][power] with a measured baseline and expected eligible traffic. Do not choose a
sample size by copying this lesson. Forecast runtime after eligibility and traffic allocation, not
from total website visits. Record who can stop the experiment for operational harm.

## 2. Rehearse the public integration path

Start the disposable stack using [onboarding](../guides/onboarding.md). Use [ShopLab][shoplab] to
observe assignment, event collection and results through a real SDK integration. It is a learning
fixture; create a separate experiment for your own exercise rather than altering the demo's expected
configuration. Use synthetic shoppers and a nonproduction API key.

Create a draft using the [experiment guide][experiments]. Configure the actual primary event name,
variant payloads and planned traffic. Trace the application call that requests an assignment and
renders the corresponding summary. Ensure assignment happens before a conversion is submitted.
Keep the fallback safe when no assignment is available.

For a known cohort, record assigned user IDs, variant IDs, exposures and conversions. Include one
repeated conversion, one unassigned user and one ineligible user. Reconcile the expected converting
users against results. Check the implementation's attribution behavior against the business plan;
do not assume an unimplemented attribution window exists because the plan mentions one.

## 3. Validate readiness and start

Run an A/A or equivalent instrumentation rehearsal before interpreting a treatment difference.
Confirm eligibility, assignment balance, event associations and the data sources behind guardrails.
Review [dashboard versus API availability][dashboard] so the operator knows where each action lives.
For example, creating and controlling rollout schedules may require API calls even though the
dashboard displays their stages.

Start the real experiment only after the owner signs off on the plan and instrumentation. Save its
configuration and start time. Restrict mid-experiment changes; changing targeting, identities or
metrics can change what comparison the accumulated data represents.

## 4. Monitor integrity and harm separately from success

Check collection loss, assignment balance, schema errors and guardrails. Inspect SRM before reading
the recommendation. A safety stop protects users; it is not evidence that treatment loses under the
planned statistical test. Conversely, a favorable p-value does not excuse operational harm.

If guardrail errors spike, pause exposure using the supported experiment controls and, if the
integration uses a separately configured flag, operate that flag's kill switch. Experiment pause and
flag rollback are different controls. Do not assume completing or pausing an experiment automatically
changes every application's rendered experience. Verify the client behavior and the audit evidence.

## 5. Make one of three decisions

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

## 6. Ship with a separate rollout plan

If the decision permits shipping, implement the chosen experience behind a flag with a named owner,
safe default and removal date. Use [rollouts][rollouts] and [safety monitoring][safety] deliberately.
Confirm that client error and evaluation reports actually reach the configured monitor; a threshold
without its source data is not protection. Rehearse rollback in the disposable environment.

Roll out in stages, check guardrails and keep an operator available for the risk window. Safety
checks are periodic, not instantaneous. Verify server and SDK-held state after a rollback. A flag
serving the selected treatment is a separate configuration from the experiment's historical result.

## 7. Finish the experiment and remove the branch

Complete the experiment using the supported lifecycle operation after deciding what clients will do.
Keep its decision record and configuration for auditability. Remove the losing application branch,
stop obsolete event production where appropriate, and follow [flag retirement](flag-lifecycle.md).
Historical analysis may still require retained events and configuration; application cleanup does not
authorize deleting that history.

## Exercise and worked answer

At the planned stopping point, the observed lift is positive but its interval spans both a useful
benefit and material harm. A product deadline is tomorrow. What goes in the decision record?

**Answer:** “Inconclusive under the planned analysis; retain control.” Include the uncertainty and
guardrail evidence, the business cost of waiting, and options for a better-powered follow-up. If the
business chooses to ship for independent reasons, state that decision separately; do not rewrite the
experiment as proof of benefit. Name a rollout owner and rollback policy for any release.

**Review artifact:** one experiment plan, one instrumentation reconciliation, a decision record and
a rollout/removal handoff. Each should name its owner and the evidence needed to pass its next stage.

[power]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/statistics/power-analysis.md
[shoplab]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/demo/shoplab/README.md
[experiments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/user-guide.md
[dashboard]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/dashboard-and-api.md
[rollouts]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/rollouts.md
[safety]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/safety.md
