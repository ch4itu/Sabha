# Sabha Matchchain

**Release candidate: v0.1.0-rc1 · protocol SMC3**

Sabha Matchchain is a chain-agnostic, transport-independent signed replay protocol for a fixed or monotonically shrinking set of participants. It gives applications one deterministic authenticated history without requiring a gameplay server or trusting the message carrier.

It is not a public blockchain, a global Byzantine-consensus network, a transport, or an adversarial escrow judge. Participants still need a carrier such as WebRTC, local networking, files, QR codes, email, WebSockets, or a blockchain.

## Components in this review branch

- `specification/` — normative protocol documents.
- `whitepaper/` — design rationale and security boundary.
- `SECURITY.md` — vulnerability scope and known limitations.
- `PUBLICATION_STATUS.md` — why the executable implementation remains gated until r24.

## Publication boundary

This documentation-first RC deliberately does not declare a second public implementation canonical. The executable core, adapters, vectors and adversarial tests remain in the signed r23.7.2 engineering bundle until r24 proves byte-equivalence between one public canonical source and the Sabha Ludo embedded core.

## Status

The implementation has extensive deterministic and adversarial tests but has not yet received an independent professional security audit or formal verification. Use the RC for review and experimentation, not as an unqualified guarantee of funds safety.
