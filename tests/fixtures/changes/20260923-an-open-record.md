---
schema_version: 1
id: 20260923-an-open-record
target: toy-order-desk
status: open
opened_at: '2026-09-23T10:00:30Z'
opened_by: support
closed_at: null
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
change: null
pushes: []
expected: null
verification: null
observed: []
superseded_by: null
why: null
imported_from: null
---
