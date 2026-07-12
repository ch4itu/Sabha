# Threat Model

SMC3 defends against untrusted transports, reordered/duplicated/stale messages, wrong match/session/participant/slot evidence, modified payloads, invalid signatures, unknown membership bits, malformed finals/checkpoints and conflicting recovery bundles.

It does not guarantee liveness when required signers disappear, Sybil resistance for open public membership, network traversal, privacy of plaintext payloads, correctness of an omitted application transition verifier, or enforcement of monetary settlement by itself.
