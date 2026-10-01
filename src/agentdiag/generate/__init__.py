"""Generation: a coding agent's drafts turned into a Suite (ticket 12, phase-6 decisions
29-31, ADR-0002 §7-§8).

`ids` holds the four provenance forms, the id derivation and the matching that keeps ids
stable across regeneration; `command` checks the drafts, writes the Suite with a comment per
Scenario and adds it to the Manifest. The procedure an agent follows to write the drafts is
the packaged skill `skills/generate/SKILL.md`. Offline, like `validate`: nothing here
imports the SDK.
"""
