# Recovery and Fork Policy

Recovery is verification, not longest-chain selection. A candidate bundle is accepted only when:

1. genesis and session match;
2. every artifact verifies;
3. every local block hash is an identical prefix of the candidate; and
4. the candidate is equal or longer.

An equal replay with the same tip is idempotent. A longer identical-prefix replay is a strict extension. Any conflicting block at a shared height is a fork and must not replace local state.
