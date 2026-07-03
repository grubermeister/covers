# Developer And Operator Docs

Start here. Pick the row that matches what you are trying to do.

| I want to... | Read |
|---|---|
| Set up a local dev environment from scratch | [BUILD.md](BUILD.md) |
| Understand what the product does and why | [../vision.md](../vision.md), [design.md](design.md) |
| Learn the domain terms (markings, covers, regions) | [../glossary.md](../glossary.md) |
| See which features live in the SPA vs Django admin | [../../ISSUE.md](../../ISSUE.md) (implementation surfaces) |
| Understand the database schema | [model.md](model.md) |
| Name UI elements and actions consistently | [vocab.md](vocab.md) |
| Digitize an ASCC state (build and import a bundle) | [PIPELINE.md](PIPELINE.md) |
| Look up a management command or data script | [TOOLS.md](TOOLS.md) |
| Run day-to-day server operations | [RUNBOOK.md](RUNBOOK.md) |
| Understand or change the deploy flow | [DEPLOY.md](DEPLOY.md) |
| Provision a staging or production host | [DEPLOY.md](DEPLOY.md#provisioning-a-fresh-host) |
| Make sense of legacy v1 data (tblRawStateData etc.) | [v1/v1-legacy-summary.md](v1/v1-legacy-summary.md) |

Reading order for a brand-new developer: [../vision.md](../vision.md) ->
[../glossary.md](../glossary.md) -> [BUILD.md](BUILD.md) ->
[design.md](design.md), then the rest as needed.

Files under `docs/devel/` are internal and are excluded from the live Help
page. The public Help page serves only the Markdown files directly under
`docs/` (faq, glossary, vision, acknowledgements) via
`backend/common/api/help.py` -- anything you write in those four files is
end-user visible after the next deploy.

## Documentation Conventions

Each kind of content has exactly one home. When writing or reviewing docs,
route content accordingly instead of duplicating it:

- **Specification** (what the system should do): [design.md](design.md)
  and [model.md](model.md). These are pure spec -- never add current
  implementation status, route lists, or progress notes to them.
- **Implementation status and progress** (what is built, partial, or
  pending): `ISSUE.md` at the repo root, including the feature
  implementation-surfaces table.
- **Procedures** (how to do a task): BUILD.md, PIPELINE.md, TOOLS.md,
  RUNBOOK.md, DEPLOY.md. Each procedure is written once in its home doc;
  other docs link to it rather than restating it.
- **Terminology**: user-facing terms in [../glossary.md](../glossary.md),
  developer-only UI/action vocabulary in [vocab.md](vocab.md).
- All repo text is 7-bit ASCII only (see the review checklist in
  [DEPLOY.md](DEPLOY.md)).
