---
schema_version: 1
id: 20260923-a-superseded-record
target: toy-order-desk
status: superseded
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
pushes: []
expected: null
verification: null
observed: []
superseded_by: 20260923-a-verified-record
why: null
imported_from: null
---
