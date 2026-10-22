# SKCP-50 browser accessibility qualification - FAIL

Card: `83a8c40b`

Revision: `436b094b86f422c7eaf94ebbbd98b846e7f3a6af`

Verdict: **FAIL. Do not complete the card.** This is a qualification result only. No product repair was made.

## Scope and method

The qualification covered the Now, Portfolio, Schedule, Reliability, Architecture, AI outcomes, Governance, and Reports surfaces, plus the separate authorization-preview wireframe lane. The local app used a new empty temporary home. Existing browser qualifiers used only their repository public-synthetic fixtures. No protected data, credential, deployment, service restart, or external request was used.

Real Google Chrome 147 was driven through CDP. The attempted real Mozilla Firefox 154 lane used the installed geckodriver and failed before session creation because Firefox is Snap-packaged and geckodriver rejected `/usr/bin/firefox` as not being a Firefox executable. The direct Snap ELF cannot be used as the WebDriver binary outside its launcher confinement. This exact environment blocker is retained in `browser-matrix.json`.

The Chrome matrix covered:

- mobile: 390 by 844 CSS pixels
- tablet: 768 by 1024 CSS pixels
- desktop: 1440 by 1000 CSS pixels
- 200 percent zoom equivalent: 720 by 500 CSS pixels with page scale 2
- reduced motion: 1440 by 1000 CSS pixels with `prefers-reduced-motion: reduce`

## Acceptance criteria

### 1. Keyboard completion, visible focus, names, and landmarks - FAIL

- Accessible-name inspection found no unnamed visible interactive element in the 40 Chrome route/scenario rows.
- The existing landmark Chrome qualifier passed for its seven routes.
- The Schedule page has no native or ARIA main landmark. The full matrix recorded this on all five Chrome scenarios.
- The computed focus audit found no visible outline or focus shadow on many controls across all eight surfaces. This includes sidebar links and native controls. The exact per-route control lists are in `browser-matrix.json`.
- Existing authenticated keyboard lanes for Now and most specialist workspaces could not run against synthetic data because their fixture setup was unavailable. Therefore every common task was not proven keyboard complete.

### 2. Non-color meaning for truth and measurement states - FAIL

Textual evidence was found for current, stale, partial, unavailable, unknown, not applicable, and forecast. The qualified surfaces did not expose measured and estimated states in this run. Full non-color coverage for all required states was therefore not proven.

The unavailable state itself is conveyed through words and not color alone in the reviewed screenshots.

### 3. Click budgets - PARTIAL, task-time evidence blocked

Existing focused real-Chrome lanes prove these interaction counts where their fixtures ran:

- Now KPI to evidence: one activation in `qualify_control_plane_now_cdp.mjs` source, but the current run was blocked by a 403 from the current decision-bound API.
- Portfolio signal to evidence: one activation in qualifier source, but the fixture did not start.
- Architecture exception to CI evidence: one activation in qualifier source, but the fixture did not start.
- Authorization preview state inspection: one activation and no write in the passing preview qualifier.

Because the original qualification run did not start authenticated synthetic fixtures for Now, Portfolio, Schedule, Reliability, Architecture, Governance, and Reports, exact end-to-end task times were not measured. A later qualification-only probe used an ephemeral import-only `pytest` shim and proved that the committed Schedule, Reliability, Architecture, Governance, and Reports real-Chrome fixtures can run without installing or changing repository dependencies. The Portfolio lane remains invalid because of the two qualifier defects recorded below. The acceptance criterion requires linked click count and task time, so it still fails.

#### Confirmed qualifier defects — do not treat as product repairs or a PASS

1. `scripts/qualify_control_plane_portfolio_cdp.mjs` sends `Origin: http://10.0.0.139:7778`, but the imported synthetic `Rig` allows `https://10.0.0.139:7778`. The mismatch causes the committed qualifier's authenticated projection to fail closed and its signals not to render.
2. The same qualifier classifies every request not beginning with its localhost application URL as external. That predicate incorrectly includes `data:` SVG navigation assets as external HTTP requests. An ephemeral corrected-Origin probe rendered and exercised the Portfolio fixture, then failed only because those embedded `data:` assets were included in the asserted external-request list.

These are defects in the committed qualifier. They were observed and recorded only; neither qualifier was repaired under card `83a8c40b`. They do not erase the independent product failures or cross-browser limitations, and the qualification remains **FAIL**.

### 4. Mobile, tablet, desktop, zoom, reduced motion, and screenshots - FAIL

The five reviewed Chrome screenshots are linked below.

- Desktop preserves the full sidebar and readable content.
- Mobile collapses navigation to unlabeled icons. Although accessible names remain in the DOM, visible navigation meaning is lost to sighted users who do not recognize each icon.
- The mobile and 200 percent zoom screenshots show clipped navigation labels and persistent inner horizontal scrolling for the estate table.
- The broad document overflow assertion passed because tables use inner scrolling. That does not make the reviewed mobile or zoom visual behavior acceptable.
- Reduced-motion inspection still found the live status dot animation on Now (`dot on`).
- Firefox screenshots are absent because the real-browser session could not be created. Cross-browser visual qualification is incomplete.

### 5. WCAG 2.2 AA automated/manual evidence and limitations - FAIL

Automated results:

- PASS: landmark qualifier.
- PASS: AI workspace qualifier, including keyboard, focus return, AX names, light and dark contrast, reduced motion, 390/320 responsive widths, no writes, no external requests, and no runtime exceptions.
- PASS: authorization preview qualifier, including fail-closed states and no writes.
- PASS: the dedicated 11-surface navigation contrast qualifier, minimum 7.4735:1 in light mode and 8.2103:1 in dark mode. This supersedes the broad matrix's naive translucent-background contrast calculation for navigation.
- BLOCKED: Now and quality qualifiers under the current API authorization behavior.
- BLOCKED in the original run: Portfolio, Schedule, Reliability, Architecture, Governance, and Reports synthetic decision fixtures because the active Python environment has no `pytest`, which their imported test fixture requires.
- FOLLOW-UP PROBE: an ephemeral import-only `pytest` shim allowed Schedule, Reliability, Architecture, Governance, and Reports to pass their committed real-Chrome lanes without repository changes. Portfolio remains invalid because of the exact Origin and `data:`-asset qualifier defects recorded above.

Manual screenshot review found the mobile/zoom issues above. Screen-reader output from an assistive technology was not available; Chrome's accessibility tree was used for automated semantic inspection only. No claim is made that this substitutes for a manual screen-reader pass.

## Evidence

- Full machine-readable matrix, route results, focused qualifier logs, blockers, click budgets, and limitations: [`artifacts/SKCP-50-2026-08-25/browser-matrix.json`](artifacts/SKCP-50-2026-08-25/browser-matrix.json)
- Run log: [`artifacts/SKCP-50-2026-08-25/automated.log`](artifacts/SKCP-50-2026-08-25/automated.log)
- Chrome screenshots:
  - [`chrome-mobile.png`](artifacts/SKCP-50-2026-08-25/chrome-mobile.png)
  - [`chrome-tablet.png`](artifacts/SKCP-50-2026-08-25/chrome-tablet.png)
  - [`chrome-desktop.png`](artifacts/SKCP-50-2026-08-25/chrome-desktop.png)
  - [`chrome-zoom-200.png`](artifacts/SKCP-50-2026-08-25/chrome-zoom-200.png)
  - [`chrome-reduced-motion.png`](artifacts/SKCP-50-2026-08-25/chrome-reduced-motion.png)
- Artifact hashes: [`artifacts/SKCP-50-2026-08-25/SHA256SUMS`](artifacts/SKCP-50-2026-08-25/SHA256SUMS)
- Qualification harness: [`../../scripts/qualify_skcp50_browser.mjs`](../../scripts/qualify_skcp50_browser.mjs)

## Safety and disposition

No product source, product test, dependency, deployment, service, card criterion, or protected record was changed. No commit or push was performed.

The qualification work is frozen at revision `436b094b86f422c7eaf94ebbbd98b846e7f3a6af` with verdict **FAIL**. All evidence that this qualification-only card can authorize is recorded above, including the two confirmed Portfolio qualifier defects. Remaining acceptance work requires separately authorized product remediation, qualifier repair, or browser/runtime provisioning; none is authorized by card `83a8c40b`. The claim is therefore released without completion. Requalification requires a new exact claim after those blockers are resolved; this card must not be marked complete from the present evidence.
