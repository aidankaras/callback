# Concept notes

One note per design decision that is not self-evident from the code, written as
the subsystem is built.

Each note covers what the decision is, why it was taken here, what was
considered and rejected, which code implements it, and the question a reviewer
would reasonably ask about it.

They exist because several of this project's decisions look like mistakes from
inside a single file. A module that deliberately parses badly, a gate that fails
a document a person would call fine, a tolerance whose default only holds
because of a template's grid width — each is a defect to anyone who meets it
without the reasoning, and each is one edit away from being "fixed" into
uselessness.

| Note | Subsystem |
|---|---|
| [01 — Adversarial extraction](01-adversarial-extraction.md) | `checks/naive_extract`, `checks/roundtrip` |
| [02 — Gate result shapes](02-gate-result-shapes.md) | `gates` (superseded `checks/result`, now removed), `checks/grounding`, `checks/roundtrip`, `checks/hidden` |
| [03 — Open findings](03-open-findings.md) | `checks/hidden`, `checks/grounding`, `checks/roundtrip`, `checks/numbers` |
| [04 — Network policy](04-network-policy.md) | `discovery/fetch`, `discovery/page`, `discovery/store` |
