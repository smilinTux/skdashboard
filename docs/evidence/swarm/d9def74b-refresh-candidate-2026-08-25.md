# d9def74b refresh candidate evidence

Card: d9def74b
Scope: local candidate only
Route: Codex through skgateway/sk-codex

## Exact revisions

- Base commit: 27c8f9ed4b10d8665daac724dcfed9972847f882
- Base tree: 24df619bc79abf0fd7afc8644597af4e23e3c822
- Accepted refresh source: 7cacd19df585b7750301599b9b64d75b335546ca
- Candidate commit: f01c47369b1340800201afb664ff48d8454062f1
- Candidate tree: 626ee853680896246f2aeab1d81a28e481139c4c

The candidate applies only the accepted legacy reservation refresh behavior.
The existing typed Tenant SSE changes and the exact CapAuth revision remain
unchanged. The candidate changes exactly these three paths:

- CHANGELOG.md: 97ef738537225d0c6676b2b3cbee8f355f08dbdb5b7f67d1eed02d913eee99af
- src/skdashboard/session_adapter.py: 527eda0a244e9c461af69e57d28ce5c53aca0af26ff372cc258db8c767382720
- tests/test_session_adapter.py: e335187133e05df9bf5971e331b421585b7e74c0220ccb47930d9b581d5eb851

The CapAuth requirement remains pinned to revision
56b161415748f4c3e2bea0e7fad98c6d104376de.

## Verification

- Focused session tests: 19 passed in 1.70s.
- Changed boundary tests: 65 passed in 2.51s.
- Full pytest: 544 passed, 8 inherited deprecation warnings in 28.31s.
- Focused Ruff: PASS.
- Full Ruff: PASS.
- Git diff check: PASS.
- Forward patch SHA-256: 4a311fe409fc304d0fddffcafc0ebf4c62041a70f4a151c992342e7fca43c8c9.
- Reverse patch SHA-256: 25c31671fd0c1edc8e7b4395455ce350a5c3a73a91fe95de631269832f27e352.
- Forward alternate-index apply check: PASS.
- Reverse alternate-index apply check: PASS.
- Full test log SHA-256: a1a177ad07c05311e6c5e17f0122176725f20738f3c9244513ef2f389e0fb691.
- Full Ruff log SHA-256: 82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18.

## Preservation and limits

The isolated worktree is clean at the candidate commit. The candidate was not
merged or pushed. No deployment, restart, credential access, protected data,
external action, HammerTime Inbox access, or unrelated cleanup occurred.
The historical blocked cards 2ca5632a, fda6bd7f, and 56fa5431 remain
preserved. This evidence qualifies a local candidate only and does not grant
integration or human approval authority.

Rollback is the reverse patch check above. If separately authorized after a
future integration, revert only f01c47369b1340800201afb664ff48d8454062f1.
