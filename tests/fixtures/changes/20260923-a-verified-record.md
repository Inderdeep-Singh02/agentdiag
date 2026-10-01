---
schema_version: 1
id: 20260923-a-verified-record
target: toy-order-desk
status: verified
opened_at: '2026-09-23T10:00:30Z'
opened_by: support
closed_at: '2026-09-23T10:05:00Z'
title: Cancel called on a status question
trigger:
  kind: diagnosis
  summary: The Target called cancel_order on a question about an order's status (llm_call-1).
  run: 20260923T100000Z-base
  scenario: status-question-is-not-a-cancel
  trial: 1
  cites:
  - turn-1
  - llm_call-1
diagnosis:
  run: 20260923T100000Z-base
  scenario: status-question-is-not-a-cancel
  trial: 1
  text: 'The Target called cancel_order on a question about an order''s status (llm_call-1),
    which Rule 2 forbids: the customer asked only where the order was (turn-1), and nothing
    asked for a cancel.'
layer: rules
change:
  sections:
  - prompt.system
  files:
  - manifest.yaml
  git_sha: 4711963aa1b2c3d4e5f60718293a4b5c6d7e8f90
  flow_ids: []
pushes:
- kind: connector
  environment: local
  at: '2026-09-23T10:01:30Z'
  push_record: pushes/20260923T100130Z-local.json
  run: null
  fingerprint_before: 1f0e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0
  fingerprint_after: 2a1b3c4d5e6f708192a3b4c5d6e7f8092a1b3c4d5e6f708192a3b4c5d6e7f809
expected:
  stated_at: '2026-09-23T10:01:00Z'
  should_move:
  - status-question-is-not-a-cancel
  must_not_move:
  - greeting-calls-no-tool
verification:
  baseline: 20260923T100000Z-base
  run: 20260923T100200Z-prmt
  compared_at: '2026-09-23T10:05:00Z'
  expect:
  - manifest.prompts
  - fingerprint
  - sync
  result: verified
  summary: 'improvement 2  unchanged 15  declared differences only: manifest.prompts.rules.path'
observed:
- run: 20260923T100200Z-prmt
  scenario: status-question-is-not-a-cancel
  trial: 1
  eval: forbid_tools
  eval_id: null
  verdict: pass
  rationale: the Trace called none of cancel_order; the tools it called were lookup_order
- run: 20260923T100200Z-prmt
  scenario: status-question-is-not-a-cancel
  trial: 1
  eval: must_not_say
  eval_id: null
  verdict: pass
  rationale: The Target said none of 'cancel' in the Trace
superseded_by: null
why: null
imported_from: null
---
