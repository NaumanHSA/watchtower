import base64
import json
import uuid
import sys
from typing import Optional, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from threading import Lock

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# =========================
# In-memory key registry
# =========================
class KeyEntry(BaseModel):
    kid: str
    private_pem: str
    public_pem: str


KEYS: Dict[str, KeyEntry] = {}
KEYS_LOCK = Lock()

def gen_rsa_keypair(bits: int = 3072) -> KeyEntry:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,  # PKCS#1
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    pub = priv.public_key()
    public_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return KeyEntry(kid=str(uuid.uuid4()), private_pem=private_pem, public_pem=public_pem)


def load_private_key(pem: str):
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)


# =========================
# Models
# =========================
class KeyResponse(BaseModel):
    kid: str
    isSuccess: bool
    pub_cov: str


class Envelope(BaseModel):
    alg: str = Field(default="RSA-OAEP-256 + AES-256-GCM")
    ek: str  # base64 RSA-OAEP encrypted AES key
    n: str   # base64 12B nonce
    c: str   # base64 ciphertext
    t: str   # base64 16B GCM tag
    meta: Optional[Dict[str, Any]] = None  # can include {"kid": "..."} etc.


# =========================
# Utils
# =========================
def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def decrypt_envelope(envelope: Envelope, private_pem: str) -> Dict[str, Any]:
    ek = b64d(envelope.ek)
    n = b64d(envelope.n)
    c = b64d(envelope.c)
    t = b64d(envelope.t)

    priv = load_private_key(private_pem)
    data_key = priv.decrypt(
        ek,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    aes = AESGCM(data_key)
    plaintext = aes.decrypt(n, c + t, associated_data=None)
    return json.loads(plaintext.decode("utf-8"))

def _looks_like_envelope(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    required = {"alg", "ek", "n", "c", "t"}
    return required.issubset(set(obj.keys()))


# =========================
# FastAPI app
# =========================
app = FastAPI(title="Encryption Test Server", version="1.0.0")


@app.get("/token/verify", response_model=KeyResponse)
async def verify_token(watchtower_token: Optional[str] = Header(default=None)):
    if not watchtower_token:
        raise HTTPException(status_code=400, detail="Missing watchtower token")
    entry = gen_rsa_keypair()
    with KEYS_LOCK:
        KEYS[watchtower_token] = entry
    return KeyResponse(kid=entry.kid, isSuccess=True, pub_cov=entry.public_pem)


@app.post("/ingest/encrypted")
async def ingest_encrypted(
    req: Request,
    watchtower_token: Optional[str] = Header(default=None, alias="watchtower-token"),
    x_enc: Optional[str] = Header(default=None, alias="X-Enc"),
):
    # 1) Parse JSON
    try:
        body = await req.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")

    # 2) Decide encrypted vs plaintext
    is_encrypted = bool(x_enc) or _looks_like_envelope(body)

    if is_encrypted:
        # 3) Validate envelope + token
        try:
            env = Envelope(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid envelope: {e}")

        if not watchtower_token:
            raise HTTPException(status_code=400, detail="Missing watchtower token for encrypted payload")

        with KEYS_LOCK:
            entry = KEYS.get(watchtower_token)

        if not entry:
            raise HTTPException(status_code=404, detail="Unknown watchtower token")

        # 4) Decrypt
        try:
            payload = decrypt_envelope(env, entry.private_pem)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Decryption failed: {e}")

        # 5) Logging (stdout)
        print("\n=== Encrypted Envelope ===", file=sys.stdout)
        print(json.dumps(body, indent=2, ensure_ascii=False), file=sys.stdout)

        print("\n=== Decrypted Payload ===", file=sys.stdout)
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stdout)
        print("=========================================\n", file=sys.stdout)

        # 6) Minimal response
        return {
            "ok": True,
            "mode": "encrypted",
            "received": True,
            "keys_available": len(KEYS),
        }

    else:
        # Plaintext path: just echo/print the payload
        print("\n=== Plain Payload ===", file=sys.stdout)
        print(json.dumps(body, indent=2, ensure_ascii=False), file=sys.stdout)
        print("=========================================\n", file=sys.stdout)

        return {
            "ok": True,
            "mode": "plaintext",
            "received": True,
        }

# Optional: simple GET to list registered kids (for testing)
@app.get("/keys")
def list_keys():
    with KEYS_LOCK:
        return {"kids": list(KEYS.keys())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000, reload=False)