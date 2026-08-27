# SKCP-50 browser accessibility qualification

## Scope and disposition

This qualification covers the Now workspace, Portfolio, Reliability, Architecture,
AI outcomes, Governance, and Reports in a fresh real Google Chrome session through
Chrome DevTools Protocol. It exercises the shared scope controls, evidence dialog,
insight boundary content, and the read-only authorization and refresh preview
boundaries exposed by these workspaces.

The executable result is `PASS` for the automated matrix. The overall delivery is
`PASS_FOR_REVIEW` because screenshot review and an assistive-technology session must
remain independent manual review activities. No repository runtime, gateway, or live
configuration was mutated.

## Automated WCAG 2.2 AA evidence

Run:

```bash
SKCP50_ARTIFACT_DIR="$HOME/.skcapstone/evidence/work/83a8c40b/qualification" \
  node scripts/qualify_control_plane_accessibility_cdp.mjs
```

The qualifier records:

- one exposed `main` landmark and at least one navigation landmark on every surface;
- Chrome accessibility-tree landmark parity;
- accessible names for all visible native controls and dialogs;
- first-tab visible focus on every surface and matrix entry;
- no document-level horizontal overflow;
- no running animation under `prefers-reduced-motion: reduce`;
- mobile 390 by 844, tablet 768 by 1024, desktop 1440 by 1000, 200 percent
  effective zoom, and reduced-motion layouts;
- one viewport screenshot for every surface and layout, 35 total;
- network method, external request, and runtime exception counts;
- common-task interaction count and elapsed time.

The machine-readable outputs are:

- `qualification/accessibility-landmark-matrix.json`
- `qualification/screenshot-manifest.json`
- `qualification/screenshots/*.png`

## Keyboard, focus, names, and landmarks

All 35 surface and layout entries have one DOM and accessibility-tree main landmark,
at least one named navigation landmark, named visible controls and dialogs, and a
visible first-tab focus indicator. Native buttons, links, selects, and dialogs retain
browser keyboard semantics. Evidence opens from a named button. Dialogs have labelled
headings and named close buttons, and closing returns focus through the existing dialog
handlers.

## Non-color state vocabulary

The UI renders text and symbols in addition to color for all approved truth states:
`current`, `stale`, `partial`, `unavailable`, `unreachable`, `unknown`, and
`not applicable`. The metric definition and evidence content render `measured`,
`estimated`, and `forecast` as text. Badges have borders, symbols, and full state
labels, so meaning does not depend on hue alone. The synthetic full-estate fixture
contains deliberate degraded states and exercises this vocabulary.

## Common-task budgets

| Qualified task | Interactions | Automated elapsed time | Budget | Result |
| --- | ---: | ---: | ---: | --- |
| KPI or exception to evidence | 1 | Recorded in JSON | at most 2 | Pass |
| KPI to source contributors | 1 | Recorded in JSON | exactly 1 | Pass |

The evidence dialog exposes source provenance, truth, observed time, watermark, and
safe errors in the same interaction. These timings are automation timings on the
qualification host, not claims about human completion time.

## Manual review checklist

An independent reviewer should use the hashed screenshots and execute this bounded
checklist before final acceptance:

1. Review each screenshot for clipping, overlap, truncation, and readable reflow.
2. Run keyboard-only traversal on each workspace, including reverse traversal,
   dialog close, focus return, and scope-control changes.
3. Run one screen-reader session and confirm navigation, main, table, heading,
   control, dialog, status, and accessible-name announcements.
4. Confirm text and icon state distinctions with color disabled and inspect normal
   text, large text, component boundaries, and focus contrast against WCAG 2.2 AA.
5. Compare screenshots with the approved baseline or explicitly approve them as the
   new baseline. The manifest identifies every reviewed byte.

## Known limitations

- Chrome accessibility-tree inspection is automated evidence, not a substitute for
  human testing with NVDA, JAWS, VoiceOver, or another approved screen reader.
- Screenshot capture proves the rendered matrix and byte identity. Aesthetic and
  baseline approval remain reviewer decisions.
- Automated elapsed time excludes human perception and decision time.
- The qualification uses a public synthetic full-estate fixture and never reads live
  protected production records.
- Browser coverage is Google Chrome 146 on this host. Firefox is installed, but this
  CDP harness does not claim Firefox accessibility-tree equivalence.

## Repository delivery

This card changes the repository because it expands the durable Chrome qualifier,
adds matrix assertions, and disables the live-status pulse under reduced motion. The
branch and pull request are linked on card `83a8c40b`; shared evidence is published
under `~/.skcapstone/evidence/work/83a8c40b/` with SHA-256 identity.
