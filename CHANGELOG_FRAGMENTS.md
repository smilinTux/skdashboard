# Changelog Fragments Guide

This project uses [towncrier](https://github.com/twisted/towncrier) to manage changelog entries as fragment files. This prevents CHANGELOG.md merge conflicts when multiple PRs are open concurrently.

## Why Fragments?

When every PR edits the same CHANGELOG.md file, concurrent PRs conflict. Even with 12 PRs open, every PR must be re-based after each merge. With fragments, each PR adds its own file to `changelog.d/` and a release step concatenates them into CHANGELOG.md.

## Adding a Fragment

When your PR touches `src/` or `pyproject.toml`, add a fragment file to `changelog.d/`:

```bash
# Choose a descriptive name (card ID or PR number is recommended)
cat > changelog.d/card123.added.md << 'EOF'
Added provider-neutral read-only dashboard assistant through SKGateway with
typed request/response validation and fail-closed error handling.
[PR #130](https://github.com/smilinTux/skdashboard/pull/130)
EOF
```

### Fragment Types

Use these suffixes:

- `*.added.md` - New features
- `*.changed.md` - Changes to existing functionality
- `*.deprecated.md` - Soon-to-be removed features
- `*.removed.md` - Removed features
- `*.fixed.md` - Bug fixes
- `*.security.md` - Security fixes
- `*.docs.md` - Documentation changes

### Format

Fragments use Markdown format. Keep entries concise and user-facing:

```
Short description of the change.
[Link to PR or issue](url)
```

## CI Behavior

The `docs-check` CI gate accepts **either**:
1. An edit to `CHANGELOG.md` (for fixes to released entries), or
2. A new fragment in `changelog.d/` (for new unreleased entries)

## Release Process

When cutting a release tag:

1. Run `towncrier build --version X.Y.Z --yes`
2. This compiles all fragments into CHANGELOG.md under the new version header
3. Fragments are removed from `changelog.d/`
4. Commit the updated CHANGELOG.md

The `publish.yml` workflow runs `towncrier build` automatically before publishing.

## Skipping the Changelog

For trivial changes (typos, formatting, docs-only), use the escape hatch:
- Add `[skip-changelog]` to the PR title, OR
- Apply the `docs-exempt` label

## Example Fragment

For card c75f1c98 (this changelog fragment implementation):

```markdown
changelog.d/c75f1c98.changed.md:
```

```markdown
Adopted towncrier-based changelog fragments to prevent CHANGELOG.md merge
conflicts from concurrent PRs. Each PR now adds its own fragment file to
changelog.d/ and the release step concatenates them into CHANGELOG.md.
```
