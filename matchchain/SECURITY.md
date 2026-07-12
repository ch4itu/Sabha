# Security Policy

Report suspected vulnerabilities privately before public disclosure. Do not include mnemonics, private keys, live wallet secrets, or unredacted personal data.

## In scope

Canonicalization ambiguity, signature/domain confusion, participant/session binding, fork acceptance, unsafe strict-extension handling, active-mask errors, malformed final/checkpoint acceptance, replay nondeterminism, or secret persistence.

## Known boundaries

Matchchain cannot force an offline participant to sign, cannot guarantee network traversal or availability, and does not itself adjudicate application rules unless a deterministic transition verifier is supplied.
