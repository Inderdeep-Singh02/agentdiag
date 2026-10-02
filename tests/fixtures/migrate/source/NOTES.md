# Ada — maintainer notes

## Business

Riverbend Public Library, three branches, chat on the website only. Members are mostly
retirees and students; English, with a Spanish-speaking minority the assistant does not yet
serve.

## Environments

`dev` is the default; `prod` is the live website and nobody deploys there without the head
librarian's go-ahead. Verify fixes on `dev` first.

## Traps

- The catalogue search returns a different ordering on `prod` and `dev` (different indexes),
  so a test that asserts the first result is wrong on one of them.
- `renew_loan` silently succeeds for an already-renewed item on `dev` and refuses on `prod`.

## Evidence

Website chats are kept in the `chat_log` table for 90 days; the reviewers export one with
`scripts/export_chat.py <id>`.

## Judging

- A reply that offers to look a book up when the member only mentioned it in passing is not
  a rule-1 breach: rule 1 is about claiming the library holds a title.
- Ada runs on the same model as the reviewers' judge; discount praise of her tone.
