## What the Target does

Ada is the chat assistant of the Riverbend Public Library's website: she looks titles up in
the catalogue (`find_book`) and extends a member's loan by 21 days (`renew_loan`). Her prompt
and tool schemas were copied from the source maintenance repository's `prompts/` and `tools/`.

## Who it serves

Members of the library's three branches, mostly retirees and students, in English, on the
website's chat only; the Spanish-speaking minority is not served yet.

## Environments

`dev` is the default and the one a fix is verified on. `prod` is the live website, protected
in the Manifest: nothing is pushed there without the head librarian's go-ahead.

## Known traps

- The catalogue search orders results differently on `prod` and `dev` (different indexes),
  so a Scenario never asserts which result comes first.
- `renew_loan` silently succeeds for an already-renewed item on `dev` and refuses on `prod`.

## Where evidence lives

Website chats are kept in the `chat_log` table for 90 days; the reviewers export one with the
source repository's `scripts/export_chat.py <id>`. No Connector reads it yet. The fix history
from before the migration stays in the source repository's `HISTORY.md`: a Change record
closes only through `compare` (ADR-0012), so none was imported.
