# watchtower-contracts

Pydantic models and enums that define the wire protocol between the Watchtower
controller, workers, token servers and result receivers. Both services import this
package, so a protocol change is made in one place.

- `watchtower_contracts.models` — register/sync protocol, stream config, token-server
  verification response, detection event.
- `watchtower_contracts.crypto` (extra `crypto`) — the result envelope:
  RSA-OAEP(SHA-256) wraps a random AES-256 key, AES-GCM encrypts the JSON payload.

Compatibility rule: new fields are optional with defaults, and every model ignores
unknown fields, so a newer peer can talk to an older one. Incompatible changes bump
`PROTOCOL_VERSION`.
