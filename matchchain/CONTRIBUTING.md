# Contributing

1. Preserve deterministic consensus behavior. No wall-clock values may affect replay.
2. Add conformance and malformed vectors for protocol changes.
3. Keep chain-specific signing in adapters.
4. Treat transports as untrusted.
5. Run `python tools/release_gate.py` before proposing changes.
6. Protocol changes require a version/domain change and migration note.
