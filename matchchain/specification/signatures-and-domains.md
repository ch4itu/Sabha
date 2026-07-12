# Signatures and Domain Separation

Every signed artifact has a distinct domain: blocks, ACKs, checkpoints, finals, final digests, forfeits, HELLO messages, random commitments and random mixes. A signature valid for one artifact class must not be reusable for another.

Adapters map protocol byte payloads to chain-specific signing and verification. The core never assumes Algorand, Ethereum or another address format.
