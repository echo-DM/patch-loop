# Bounded provider retries for Gemini free-tier use

Status: Design confirmed on 2026-10-14. Runtime implementation has not started
and was not authorized by this design interview.

This specification records the retry-policy design confirmed on 2026-10-14.
The policies below are implementation requirements, not claims about current
runtime behavior.
Domain terms are defined in [CONTEXT.md](../../CONTEXT.md).

## Confirmed constraints

- The cumulative provider retry waiting time across one run must not exceed
  60 seconds. This is a waiting-time budget, not a combined request-and-wait
  budget; the existing total run-time budget also applies.
- Recovery stays within the current run. Confirmed daily quota exhaustion ends
  that run with an actionable explanation rather than scheduling a later run.
- This change focuses on recovery after a failed provider request. Proactive
  request throttling and coordination across runs/repositories are outside the
  current discussion's scope.
- A pending request may receive at most three additional attempts, with at most
  six additional attempts across the entire run. A successful response resets
  the per-request counter for the next request, but not the run-wide retry count
  or cumulative waiting time. Initial attempts do not count as retries.
- Respect a valid provider-suggested minimum delay. If it exceeds the remaining
  waiting or total run-time budget, stop rather than retrying earlier. Without
  a provider hint, use delays of 5, 10, and 20 seconds with small random jitter,
  always within the cumulative 60-second waiting budget.
- Use fixed defaults for this change, with no new `.patchloop.yml` settings.
- Expose a safe provider retry summary in report JSON and the Actions summary:
  retry attempts, cumulative waiting time, whether any failed pending request
  subsequently recovered, and the reason provider recovery stopped on failure.
  A recovered request does not by itself imply the whole run succeeded.
  Successful runs remain successful even if provider recovery was needed.
- Each provider request uses a timeout of the lesser of 60 seconds and the
  remaining total run time. Continue checking the run clock after return;
  transport timeout does not guarantee cancellation of all local processing.

The user expects retries to address minute-limit errors. This is an expectation
to test, not a guarantee: recovery requires available quota within the remaining
waiting and run-time budgets.

## Current implementation evidence

- [core.py](../../src/patchloop/core.py) provides one additional provider retry
  across the entire run, immediately, without resetting eligibility after a
  successful response. Its final provider error is the first failure's error.
- [gemini.py](../../src/patchloop/gemini.py) maps exceptions to timeout,
  rate-limited, or generic provider errors. The core currently treats all three
  as retryable, without distinguishing permanent errors or daily quota exhaustion.
- The adapter rolls back the failed turn's appended inputs before another
  attempt; completed controlled tool actions are not repeated.
- [Gemini acceptance tests](../../tests/acceptance/test_gemini_patch_flow.py)
  cover initial recovery, recovery after edits, and repeated provider failure.
  These are deterministic tests, not evidence of free-tier service recovery.

## Confirmed failure classification

- Retry recognized transient transport/connection failures, request timeouts,
  HTTP 408, ambiguous HTTP 429, and transient HTTP 500/502/503/504 responses.
- Stop immediately for confirmed daily quota exhaustion, permanent client
  failures such as invalid arguments, authentication/permission errors, or an
  unavailable model identifier, and other unclassified exceptions. The same
  input must not be retried merely because an exception occurred.
- A 429 or `RESOURCE_EXHAUSTED` alone does not prove daily quota exhaustion.
  Recognize explicit per-day quota identifiers/metrics in structured provider
  quota violations. Unrecognized or malformed details must not be interpreted
  as confirmed daily quota exhaustion.
- Report the last normalized provider failure when retries cannot continue,
  with a separate stable reason explaining which retry bound stopped recovery.
  A recovered request does not add an unresolved failure to `errors`.

## Confirmed recovery policy

The user confirmed the complete design in Q10, including the failure
classification, jitter range, and rule for simultaneous provider retry hints.

### Delays and counters

- Each fallback retry uses its per-request ordinal: 5, 10, then 20 seconds.
- Add uniformly distributed jitter from 0 to 1 second to the chosen delay.
  Waiting, including jitter, consumes the shared 60-second allowance.
- Read valid structured `RetryInfo` and HTTP `Retry-After` hints. If multiple
  valid minimum waits coexist, use the longest. For `Retry-After`, accept a
  nonnegative duration or a valid HTTP date; an elapsed date means zero minimum
  wait. Missing/malformed/nonfinite hints fall back to the default backoff.
- A hint's minimum wait takes precedence over fallback timing. If the minimum
  wait exceeds the remaining budget, stop without shortening it. If only jitter
  would exceed the waiting budget, reduce jitter to fit, preserving the minimum.
  For fallback backoff, stop if its base delay does not fit rather than retrying
  earlier. Do not wait when no total run time will remain to issue the request.
- Count a retry only when an additional provider request is attempted. Do not
  increment attempt counts merely because a planned wait was rejected.
- Account actual deliberate waiting with a monotonic clock and recheck both
  budgets before a request. An OS scheduling overrun must prevent further waits
  or retries; 60 seconds is a scheduling allowance, not a real-time guarantee.

### Run boundary and reporting

- Keep SDK retries disabled. The core owns attempt limits and waiting policy;
  the Gemini adapter owns provider-specific normalization and request execution.
- Retry the same pending turn without replaying completed controlled tools.
  Preserve per-run tool-call and verification budgets, and account retry attempts
  separately from successful model turns so recovery does not consume the
  existing useful-turn allowance.
- Pass the remaining request time through the model seam; never increase the
  configured total run-time limit to make retries possible.
- Normalize only bounded, recognized metadata from SDK exceptions and their
  exception chains. Raw responses, provider bodies, messages, and headers remain
  outside model context, persistent reports, and logs.
- Add an optional retry summary so existing reports remain readable. Include
  attempt count, cumulative waiting seconds, recovered-request count, and a
  stable stop reason where applicable. Actions summary displays validated
  numeric values and recognized classifications, not arbitrary provider strings.
- Leave PR-body and Issue-feedback expansion outside this reporting change;
  existing final failure feedback continues to use stable normalized errors.

### Acceptance evidence required for a future implementation

Use fake provider responses, clocks, waiting, and randomness rather than live
credentials or real sleeps. Cover:

- Initial failure recovery and recovery after edits, without duplicate tool
  actions or duplicate pending-turn inputs.
- Three retries per request, six retries per run, resets after success, and the
  shared waiting allowance across separate requests.
- Valid, conflicting, absent, malformed, and over-budget provider hints;
  fallback backoff, jitter bounds, and clock advancement during waiting.
- Ambiguous 429 recovery, structured daily-quota termination, permanent-error
  termination, and no automatic retry for unclassified exceptions.
- Different errors on successive attempts report the last failure; successful
  recovery is visible without marking a successful run as failed.
- Retry exhaustion after edits stays failed and has no publication intent or
  returned patch; the existing tool/total-time budget behavior stays separate.
- Request timeout shrinks with remaining run time; waits and requests stop when
  the relevant allowance is exhausted. Useful model turns remain available.
- Report/Actions compatibility with older reports without a retry summary;
  safe validation and redaction of provider-derived data.

Before reporting implementation complete, run focused Gemini/retry acceptance
tests, the relevant offline suite, strict typing, and whitespace/link checks.
Deterministic acceptance cannot establish live free-tier recovery rates.

## Existing behavior to preserve

Provider failures currently end with `failed`, no publication intent, and no
returned patch, even after local edits. Preserve that behavior for retry-limit
or retry-wait exhaustion. In [core.py](../../src/patchloop/core.py), failures
classified as `budget` can retain an existing patch for a Draft PR; new provider
retry exhaustion must not accidentally take that branch or change the existing
tool and total run-time budget behavior. Provider retries must not replay
completed tool actions or reduce the successful-turn allowance by consuming the
current implicit model-loop bound.

## External evidence

Checked on 2026-10-14:

- [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits): quotas
  include RPM, TPM, and RPD and apply per project. Actual model/project limits
  should be checked in AI Studio rather than hardcoded from a public quota table.
- [Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting):
  transient failures should use bounded exponential backoff with jitter;
  permanent client errors should not be automatically retried.

## Conversation

- Q1: The user selected a maximum of one minute of additional retry waiting
  across the entire run.
- Q2: The user accepted ending the current run for confirmed daily quota
  exhaustion, without cross-run deferred recovery.
- Q3: The user expects retries to resolve minute-limit errors and did not choose
  proactive throttling for this change.
- Q4: The user accepted three retries per pending request and six per run, with
  per-request counters resetting after success and cumulative budgets retained.
- Q5: The user accepted provider-hint precedence, stopping when a required wait
  exceeds the remaining budget, and fallback backoff with jitter.
- The initial answer repeated Q5 rather than naming Q6. Confirmation of Q6 was
  requested explicitly and subsequently resolved by Q10.
- Q7: The user accepted fixed defaults without additional YAML configuration.
- Q8: The user accepted safe retry information in JSON and Actions summaries,
  without changing successful runs into failures after recovery.
- Q9: The user accepted shrinking each request timeout to the remaining total
  run time, subject to the existing 60-second request timeout ceiling.
- Q10: The user confirmed the complete design, including Q6, 0-1 second jitter,
  and selecting the longest of simultaneous valid provider minimum-wait hints.
  This confirms the design; runtime implementation remains a separate task.
