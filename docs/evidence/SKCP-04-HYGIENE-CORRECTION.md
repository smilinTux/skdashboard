# SKCP-04 hygiene correction

One temporary hygiene note was created with shell output before this correction.
It was removed without copying or preserving test key material. The candidate
and retained evidence were scanned for the printed test values and both scans
returned zero hits. No signing test uses them. Any future signing test must
generate an ephemeral key in memory and must not log or persist private bytes.

The discarded values are intentionally not reproduced here.
