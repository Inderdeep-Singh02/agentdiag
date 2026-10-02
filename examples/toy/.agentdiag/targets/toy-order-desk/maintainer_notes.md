<!--
Maintainer notes (ADR-0016 section 5): what whoever maintains this Target needs before
touching it. Every agentdiag skill reads this file before its first step; the Judge never
does.

What belongs here: the facts a maintenance cycle needs and nothing else records — what the
Target does and for whom, which environment is the default and which reach real users, the
traps a previous cycle fell into, where its evidence (conversations, logs, Flow runs) is
kept and how it is read. What does not: rules for judging it (judge_notes.md, which the
Judge reads), anything the Manifest already says (point at it instead), and any credential
value (a Manifest names the variable; the value lives in ~/.agentdiag/env or the shell).

At most 600 words: this file is part of a read set budgeted at 10k tokens.
-->

## What the Target does

_The job, in its owners' words._

## Who it serves

_The users and the business; the language, hours and channel they expect._

## Environments

_The default environment and what it reaches; each protected one and why; the one a fix is
verified on._

## Known traps

_What misled a previous cycle: a stale snapshot, a tool that answers differently per
environment, a name that changed._

## Where evidence lives

_Each Evidence store by kind and the command that reads it; what a store lacks (a truncated
body, no end time)._
