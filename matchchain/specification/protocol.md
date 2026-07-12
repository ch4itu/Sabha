# SMC3 Protocol Specification

## 1. Scope

SMC3 defines an authenticated linear replay for 2–64 genesis-bound participants. It does not define message transport or application rules.

## 2. Genesis

Genesis commits protocol version, match ID, 32-byte session ID, game type, ordered participant slots and addresses, active mask, membership and ordering policies, acknowledgement policy, rules hash, client-build hash, initial-state hash, network, stake context, escrow context, randomness protocol and deterministic extra metadata. The genesis hash is SHA-256 over canonical JSON.

## 3. Blocks

Each block commits protocol, kind, match/session/genesis, exact height, deterministic round/logical time, actor address and slot, predecessor hash, state-before and state-after hashes, active masks, payload and extra metadata. The actor signs a domain-separated block payload.

## 4. Verification order

A verifier checks structural version/kind, match/session/genesis binding, exact extension height, monotonic deterministic ordering, predecessor, state-before, masks, actor slot/address/active status, canonical block hash, signature, and—when supplied—the deterministic transition. Application mutation occurs only after all checks succeed.

## 5. Recovery

A bundle may replace local replay only when it verifies completely and strictly extends the exact local prefix. A longer conflicting fork is rejected and retained only as dispute evidence. Equal-height conflicting tips are rejected.

## 6. Finality artifacts

ACKs are delivery evidence, not block validity. Checkpoints and final proofs bind the exact verified tip. Final payout vectors must cover every genesis participant and reject zero-signature or incomplete proofs.

## 7. Membership

Fixed membership forbids mask changes. Monotonic membership permits deactivation but never reactivation. Unknown active-mask bits are invalid.
