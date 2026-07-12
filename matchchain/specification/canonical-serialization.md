# Canonical Serialization

SMC3 uses deterministic JSON with lexicographically sorted object keys. Undefined object fields are omitted; undefined array members are rejected. NaN, infinities and negative zero are rejected. Big integers serialize as decimal strings. SHA-256 is applied to UTF-8 bytes of the canonical form.

Implementations must not substitute locale-sensitive formatting or ordinary insertion-order JSON when hashing or signing.
