# Sabha Matchchain
## A Chain-Agnostic Signed Replay Protocol for Serverless Shared Applications

### Abstract

Many peer-to-peer applications do not need a new global blockchain. They need two simpler properties: every participant must agree on one authenticated sequence of actions, and any recovered history must be rejected if it conflicts with what was already verified. Sabha Matchchain provides those properties as a deterministic signed replay layer independent of transport and settlement chain.

### Motivation

A direct WebRTC game can exchange moves without a gameplay server, yet transport delivery alone does not establish authoritative history. Messages may be duplicated, reordered, delayed, forged, or replayed from another session. A readable event log can also accidentally become a second source of truth. Matchchain separates concerns: the application reducer decides legality, Matchchain authenticates and orders the resulting transitions, the transport merely carries bytes, and an optional blockchain adapter anchors identity or settlement.

### Design

A genesis object commits the complete participant set, slots, session, rules, initial state, membership policy and application context. Every block strictly extends the verified predecessor, binds state-before and state-after hashes, identifies a genesis slot and address, and carries a domain-separated signature. A supplied deterministic transition function can re-execute the application rule before the block is appended.

Recovery uses strict extension rather than a longest-chain rule. A candidate must reproduce the local prefix byte-for-byte. Longer conflicting histories are not silently preferred. Signed ACKs assist delivery and finality policies but never substitute for block validity. Checkpoints, final proofs and loser-signed forfeits bind the exact replay tip.

### Transport independence

Matchchain does not require Sakshat. Sakshat is Sabha's serverless WebRTC/USM communication design; Matchchain is the authenticated history carried over it. The same blocks may travel over LAN, Bluetooth, files, QR codes, email, WebSockets or another chain. The carrier is untrusted.

### Security boundary

Matchchain provides integrity, binding and deterministic recovery. It does not create global Byzantine consensus, force unavailable signers to participate, traverse every NAT, hide plaintext payloads, or adjudicate rules unless the application reducer is supplied. It is not by itself an escrow contract.

### Current implementation

The SMC3 RC supports 2–64 participants, fixed or monotonically shrinking membership, canonical JSON, domain-separated signatures, strict-extension bundles, acknowledgements, checkpoints, final proofs, forfeits, randomness helpers and bounded gossip. The Algorand adapter is optional. Test vectors cover valid conformance and malformed signatures, sessions, predecessors, actors, masks and finals.

### Roadmap

The RC is intended for public review. Stable v1.0.0 requires independent security review, interoperability implementations, expanded fuzzing, formalized schemas and completion of physical/live evidence in the first production application, Sabha Ludo.
