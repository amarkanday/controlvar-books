# Measurement and experiment design

[← Field guides](README.md) · Next: [Complete experiment walkthrough](experiment-lifecycle.md)

**Outcome:** write a metric contract that another engineer can implement without guessing what counts
as a user, an exposure or a conversion. This guide assumes basic familiarity with assignments and events.

## Start with the decision

“Make checkout better” is not a testable hypothesis. An illustrative decision is: “Ship a clearer
checkout summary if it improves the probability that an eligible shopper completes a purchase, while
keeping checkout errors below our agreed threshold.” Write the population, treatment, primary metric,
minimum useful effect, guardrails and decision method before collecting experiment traffic.

Choose the randomization unit deliberately. If one shopper can see both variants through different
device identifiers, the experiment is partly comparing devices, not stable shopper experiences.
If treatment affects an entire account, assigning account members independently can contaminate
control. An account-level experiment needs account-level identifiers and an analysis appropriate to
that unit; merely renaming a user ID does not make repeated observations independent.

## Define a metric contract

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

## Trace the implemented numerator and denominator

Read [event matching][matching] alongside the [assignment model][assignments]. Binary conversion
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

## Validate instrumentation before interpreting lift

Run a small synthetic cohort with known expected outcomes. Reconcile assigned users, matching
events, distinct converting users and experiment/variant associations. Test missing assignment,
repeated conversion, exposure-only activity, an unknown key and a user outside targeting.

Use an A/A rehearsal to find asymmetric routing and instrumentation. An A/A test does not prove
future tests are unbiased; it is one opportunity to observe the full collection path. Inspect the
[sample-ratio check][srm] before looking for a winner. An unexpected allocation imbalance can come
from eligibility, identity churn, assignment errors or unequal collection loss. Investigate it rather
than repairing the allocation by discarding inconvenient users after seeing outcomes.

Decide bot and internal-traffic exclusions before treatment. A filter based on behavior treatment
can change may condition on the outcome and bias the result. Record exclusion counts and apply the
same policy to both arms.

## Time, power and repeated looks

Distinguish event time from server receipt time. For [CUPED][cuped], history must happen in each
user's pre-assignment window and must also have reached the server before assignment. Backdating
an event submitted afterwards does not turn it into a valid pre-treatment covariate.

Use [power analysis][power] to plan a useful detectable effect and traffic requirement. The platform's
minimum sample threshold is not evidence that your particular effect has adequate power. Cover
relevant weekly cycles and conversion delays. Choose fixed-horizon or sequential inference before
launch; repeatedly checking ordinary fixed-horizon p-values and stopping at the first small one
does not preserve the nominal error rate. Review the sequential route's current limitations before
using it. Account for multiple treatments and metrics rather than reporting the best uncorrected result.

## Exercise and worked answer

You have 80 assignments in each arm. Control has 16 events from eight assigned users. Treatment has
12 events from ten assigned users, plus four events from unassigned users. What is the binary lift?
What else do you need before shipping?

**Answer:** control is 8/80 = 10%; treatment is 10/80 = 12.5%. The absolute difference is 2.5
percentage points and relative lift is 25%. Unassigned users do not enter this conversion numerator.
You still need eligibility and SRM checks, the planned sample/runtime, uncertainty and multiplicity
handling, guardrail results and the agreed decision rule. Event totals are not the primary metric.

**Review artifact:** save the metric contract, synthetic cohort reconciliation and analysis plan with
the experiment. Another engineer should be able to explain every denominator before launch.

[matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[assignments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[srm]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/srm_service.py
[cuped]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/cuped.md
[power]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/statistics/power-analysis.md
