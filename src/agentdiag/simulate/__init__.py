"""The Simulated User and the driver loop (D4's ninth module, D26 to D28, ticket 06).

One driver loop serves every Turn source (`drive`): a Scenario's literal Turns, then its
`simulate` Turn, whose messages come from a Simulated User (`user`) until agentdiag's stop
check over the Trace (`stop`) says the criterion held, the Simulated User stops, or
`max_turns` is reached. What `run.json` records about the Simulated User and the reviewer
is `configuration`'s.

Import the submodules by name: `drive` reaches the Judge and so the model SDK, and
`agentdiag.eval.simulated_user_review` imports `user` for the one renderer the Simulated
User's prompt and the reviewer's share, so this package's `__init__` imports nothing.
"""
