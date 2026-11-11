# Synthetic harness envelopes

Every file here is **synthetic**: written by hand for `tests/test_harness.py`, not recorded from a
harness build. The orchestrator replaces them with envelopes recorded from the pinned build; a
replacement must keep the field values listed below, or the test that reads it must change with it.

A `.stdout` file is what the harness printed on standard output; a `.stderr` file is what it printed
on standard error. `kit.lib.harness.read` takes the last stdout line that is a JSON object with
`"type": "result"` as the envelope.

| File | Expected status | Field values the classification relies on |
|---|---|---|
| `ok-structured.stdout` | `ok` | `subtype` `success`, `is_error` false, `structured_output` an object; exit 0 |
| `ok-result-string.stdout` | `ok` | `subtype` `success`, `is_error` false, no `structured_output`, `result` a string holding a JSON object; exit 0 |
| `no-result-object.stdout` | `failed`, reason `no result object` | `subtype` `success`, `is_error` false, no `structured_output`, `result` a string that is not JSON; exit 0 |
| `with-log-lines.stdout` | `ok` | non-JSON and non-result lines before the last line, an envelope with `structured_output` an object |
| `out-of-usage-status.stdout` | `out_of_usage` | `api_error_status` 429 |
| `out-of-usage-text.stdout` | `out_of_usage` | `is_error` true, no `api_error_status`, `result` matches `(?i)usage limit\|out of usage` |
| `login-ended-status.stdout` | `login_ended` | `api_error_status` 401 (403 is read the same way) |
| `login-ended-text.stdout` | `login_ended` | `is_error` true, no `api_error_status`, `result` matches `(?i)token (has )?(expired\|been revoked)\|not logged in` |
| `failed-subtype.stdout` | `failed` | `subtype` other than `success` (`error_max_turns`), `is_error` false |
| `failed-error.stdout` | `failed` | `is_error` true, `result` matching neither the usage nor the login pattern |
| `proxy-down.stderr` | `proxy_down` (with empty stdout) | no envelope on stdout; stderr matches `(?i)ECONNREFUSED\|connection refused` |
