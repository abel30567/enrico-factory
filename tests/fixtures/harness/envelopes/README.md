# Harness envelopes

Eight files here are recorded from harness build `2.1.290 (hbar)`. The three `recorded-*` files,
`recorded-ok-approved.stdout`, `recorded-ok-blocked.stdout` and `recorded-background-turn.stdout`,
were recorded on 2026-11-12 during the launches `12-3`, `12-2` and `12-1` of enrico-factory issue #85,
and copied unchanged. The five files `ok-structured.stdout`, `failed-subtype.stdout`,
`failed-error.stdout`, `login-ended-status.stdout` and `proxy-down.stdout` were recorded on
2026-11-13 by the orchestrator for enrico-factory issue #379, and copied unchanged except that
session ids, uuids and request ids are placeholders. Standard error was empty in every recorded run.
Every other file is **synthetic**: written by hand for `tests/test_harness.py`, because no run
produced that shape (a real out-of-usage answer could not be recorded).

A `.stdout` file is what the harness printed on standard output; a `.stderr` file is what it printed
on standard error. `kit.lib.harness.read` takes the last stdout line that is a JSON object with
`"type": "result"` as the envelope.

| File | Source | Expected status | Field values the classification relies on |
|---|---|---|---|
| `ok-structured.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-13 | `ok`, marker `APPROVED` | `subtype` `success`, `is_error` false, `structured_output` an object; exit 0 |
| `ok-result-string.stdout` | synthetic | `ok` | `subtype` `success`, `is_error` false, no `structured_output`, `result` a string holding a JSON object; exit 0 |
| `no-result-object.stdout` | synthetic | `failed`, reason `no result object` | `subtype` `success`, `is_error` false, no `structured_output`, `result` a string that is not JSON; exit 0 |
| `with-log-lines.stdout` | synthetic | `ok` | non-JSON and non-result lines before the last line, an envelope with `structured_output` an object |
| `out-of-usage-status.stdout` | synthetic | `out_of_usage` | `api_error_status` 429 |
| `out-of-usage-text.stdout` | synthetic | `out_of_usage` | `is_error` true, no `api_error_status`, `result` matches `(?i)usage limit\|out of usage` |
| `login-ended-status.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-13 | `login_ended` | `api_error_status` 401 (403 is read the same way); `result` (`OAuth access token is invalid.`) matches no text pattern; exit 1 |
| `login-ended-text.stdout` | synthetic | `login_ended` | `is_error` true, no `api_error_status`, `result` matches `(?i)token (has )?(expired\|been revoked)\|not logged in` |
| `failed-subtype.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-13 | `failed` | `subtype` `error_max_turns`, `is_error` true, no `result` key, `errors` a list; exit 1 |
| `failed-error.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-13 | `failed` | `is_error` true, `api_error_status` 404, `result` matching neither the usage, the login nor the refused pattern; exit 1 |
| `proxy-down.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-13 | `proxy_down` (with empty stderr) | `is_error` true, no `api_error_status`, `result` `API Error: Unable to connect to API (ConnectionRefused)` matches `(?i)ECONNREFUSED\|connection ?refused`; exit 1 |
| `proxy-down.stderr` | synthetic | `proxy_down` (with empty stdout) | no envelope on stdout; stderr matches `(?i)ECONNREFUSED\|connection ?refused` |
| `recorded-ok-approved.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-12 | `ok`, marker `APPROVED` | `subtype` `success`, `is_error` false, `structured_output` an object with `marker` `APPROVED`; exit 0 |
| `recorded-ok-blocked.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-12 | `ok`, marker `BLOCKED` | `subtype` `success`, `is_error` false, `structured_output` an object with `marker` `BLOCKED` and `blocked` holding `missing`, `tried` and `resume_from`; exit 0 |
| `recorded-background-turn.stdout` | recorded, `2.1.290 (hbar)`, 2026-11-12 | `failed`, reason `no result object` | `subtype` `success`, `is_error` false, no `structured_output`, `result` a prose string (the turn after a background command ended); exit 0 |
