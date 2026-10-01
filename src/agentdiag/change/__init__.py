"""The Change record (ADR-0012): one recorded fix for a Target, as a file under its
`changes/`, with a closed lifecycle whose `verified` and `refuted` gates go through
`compare`.

`record` is the model and the file, `redact` what never reaches the file, `lifecycle` the
transitions and the gates, `checks` what `validate` checks, and `command` the functions the
CLI and the UI both call. Offline: nothing here imports the model client at import time;
the gates import `compare` inside the call that needs it.
"""
