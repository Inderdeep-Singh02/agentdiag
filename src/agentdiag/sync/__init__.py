"""Sync: the Fingerprint of a Target's deployed set, and which side is ahead (ADR-0007,
ADR-0008, ADR-0011 §2 to §4).

`sections` is the grammar of a section id and the canonical text each hashes;
`fingerprint` is `fingerprint.json`; `observe` gathers the local files and the deployed
set (the Connector's read first, else the Adapter's probe); `compare` is the three-hash
table and the terminal rendering; `breaks` is the Sync break files `sync` records under the
Target; `check` is `agentdiag sync` and the Sync a Run takes before its first Trial;
`pointed` maps a section to the file it lives in; `pull` writes deployed sections into those
files, and `push` previews and writes the local files to the deployed set through the
Connector, leaving a Restore point and a Push record (`pushes`, ADR-0011 §5 to §8).

Offline at import time, like `agentdiag.scenario`: only a probe, when one is made, imports
the Adapter and the SDK; the Connector's read imports the module it names and nothing more.
"""
