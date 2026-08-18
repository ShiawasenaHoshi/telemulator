# Design history

Two specs, carried over from the private repository this emulator grew inside
and extracted from at commit `204d7e6`. They are in Russian, and they stay
that way: `tests/test_language.py` guards English everywhere except this
directory.

- `specs/2026-08-13-test-telegram-design.md` — the umbrella product spec.
  Boundaries, the three facades, the data model, the error contract, and why
  there is deliberately no "the Bot API is complete" milestone. **Wave 4
  planning starts here.**
- `specs/2026-08-16-test-telegram-wave-3-design.md` — groups and channels:
  membership, privacy mode, channel posts.

Both were written before the extraction, when the emulator still lived as a
package inside another repository, and they speak of that in the present
tense. References to the origin project are deliberately unnamed: it is
private, and its identity is not this product's to publish.

`backlog/` holds findings noticed and deliberately left for later — one file
per finding, in English, deleted once the finding is resolved or stops being
true. It arrived with the same extraction and follows the same rule about the
origin project. An earlier finding there, `400 chat not found` versus
`403 can't initiate`, was fixed before the extraction and its note retired.

What is **not** here, on purpose:

- **Implementation journals for waves 1–3.** They stayed behind with the
  project that ran them. What they describe is either restated in the specs
  above or, more precisely, pinned by the contract suite in `tests/` — 187
  tests are a sharper record of behaviour than prose.
- **A second copy of the design decisions.** The specs above are the record;
  `tests/` is the contract.
