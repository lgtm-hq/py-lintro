The working tree you can read is the base ref `{base_ref}`, not this PR: the diff below
is authoritative, and any file you read from disk shows its pre-change content. Unmarked
files above are part of this PR but are not in this chunk's diff; a copy of them read
from disk is that stale base-commit version, so never treat it as evidence that such a
file was not updated, not touched, or missing a change.
