# Sabha Matchchain

**Release candidate: v0.1.0-rc1 · protocol SMC3**

Sabha Matchchain is a chain-agnostic, transport-independent signed replay protocol for a fixed or monotonically shrinking set of participants. It gives applications one deterministic authenticated history without requiring a gameplay server or trusting the message carrier.

It is not a public blockchain, a global Byzantine-consensus network, a transport, or an adversarial escrow judge. Participants still need a carrier such as WebRTC, local networking, files, QR codes, email, WebSockets, or a blockchain.

## Components

- `src/matchchain-core.js` — canonical serialization, genesis, blocks, replay, ACKs, checkpoints, finals, forfeits and strict-extension merge.
- `src/matchchain-gossip.js` — bounded transport-independent dissemination and resynchronization.
- `adapters/algorand/` — Algorand address and signature adapter.
- `specification/` — normative protocol documents.
- `vectors/` and `tests/` — conformance and adversarial rejection evidence.

## Verify

```bash
python tools/release_gate.py
```

## Status

The code has extensive deterministic and adversarial tests but has not yet received an independent professional security audit or formal verification. Use the RC for review and experimentation, not as an unqualified guarantee of funds safety.
