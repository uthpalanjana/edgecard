"""
crypto.py — Signing (Ed25519) and Encryption (AES-256-GCM) for Cards.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional, Union

from .card import Card, ProvenanceBlock

logger = logging.getLogger(__name__)


class KeyManager:
    """
    Loads cryptographic keys from:
    - File path (Path or str)
    - Environment variable name (str starting with env:)
    - Explicit bytes
    """

    @staticmethod
    def load_key(source: Union[str, Path, bytes]) -> bytes:
        if isinstance(source, bytes):
            return source
        if isinstance(source, str) and source.startswith("env:"):
            env_var = source[4:]
            value = os.environ.get(env_var)
            if value is None:
                raise ValueError(f"Environment variable not set: {env_var}")
            return base64.b64decode(value)
        path = Path(source)
        if path.exists():
            return path.read_bytes()
        raise ValueError(f"Cannot load key from: {source}")


class Signer:
    """
    Signs Card bodies using Ed25519.
    Signature covers everything except the provenance block.
    """

    def __init__(self, private_key_source: Union[str, Path, bytes], public_key_id: str = "default") -> None:
        self.public_key_id = public_key_id
        self._private_key = self._load_private_key(private_key_source)

    def _load_private_key(self, source: Union[str, Path, bytes]):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key_bytes = KeyManager.load_key(source)
        try:
            private_key = load_pem_private_key(key_bytes, password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("Key is not an Ed25519 private key")
            return private_key
        except Exception:
            # Try raw bytes (32 bytes for Ed25519)
            if len(key_bytes) == 32:
                return Ed25519PrivateKey.from_private_bytes(key_bytes)
            raise

    def sign(self, card: Card) -> Card:
        """Sign the card body and return a new Card with provenance.signing populated."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        # Compute card body (everything except provenance.signing)
        body = self._card_body_bytes(card)

        signature_bytes = self._private_key.sign(body)
        signature_b64 = base64.b64encode(signature_bytes).decode()

        provenance = card.provenance or ProvenanceBlock()
        new_provenance = provenance.model_copy(update={
            "signing": {
                "algorithm": "ed25519",
                "public_key_id": self.public_key_id,
                "signature": signature_b64,
            }
        })
        return card.model_copy(update={"provenance": new_provenance})

    def verify(self, card: Card) -> bool:
        """Verify a card's signature. Returns True if valid."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.exceptions import InvalidSignature

        if not card.provenance or not card.provenance.signing:
            return False

        signing = card.provenance.signing
        sig_b64 = signing.get("signature")
        if not sig_b64:
            return False

        body = self._card_body_bytes(card)
        signature_bytes = base64.b64decode(sig_b64)

        try:
            public_key = self._private_key.public_key()
            public_key.verify(signature_bytes, body)
            return True
        except InvalidSignature:
            return False
        except Exception as exc:
            logger.error("Signature verification error: %s", exc)
            return False

    @staticmethod
    def _card_body_bytes(card: Card) -> bytes:
        """Serialize card body (sans signing field) to canonical JSON bytes."""
        data = json.loads(card.model_dump_json())
        if "provenance" in data and data["provenance"] and "signing" in data["provenance"]:
            # Remove signature from the body before signing
            data["provenance"]["signing"] = None
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def generate_key_pair() -> tuple[bytes, bytes]:
        """Generate a new Ed25519 key pair. Returns (private_key_pem, public_key_pem)."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, PublicFormat, NoEncryption
        )
        private_key = Ed25519PrivateKey.generate()
        private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        public_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        return private_pem, public_pem


class Encryptor:
    """
    Encrypts sensitive card fields using AES-256-GCM.
    """

    def __init__(self, key_source: Union[str, Path, bytes], key_id: str = "default") -> None:
        self.key_id = key_id
        raw = KeyManager.load_key(key_source)
        if len(raw) == 32:
            self._key = raw
        else:
            # Derive 32-byte key using SHA-256
            import hashlib
            self._key = hashlib.sha256(raw).digest()

    def encrypt(self, card: Card) -> Card:
        """Encrypt card state readings and return new Card with provenance.encryption populated."""
        import os as _os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(self._key)
        nonce = _os.urandom(12)

        # Encrypt state as JSON
        state_json = card.state.model_dump_json().encode()
        ciphertext = aesgcm.encrypt(nonce, state_json, None)

        # Store nonce+ciphertext as base64 in derived_state
        encrypted_blob = base64.b64encode(nonce + ciphertext).decode()
        new_state = card.state.model_copy(
            update={"derived_state": {**card.state.derived_state, "_encrypted_state": encrypted_blob}}
        )

        provenance = card.provenance or ProvenanceBlock()
        new_provenance = provenance.model_copy(update={
            "encryption": {
                "algorithm": "AES-256-GCM",
                "key_id": self.key_id,
            }
        })
        return card.model_copy(update={"state": new_state, "provenance": new_provenance})

    def decrypt_state(self, card: Card) -> Card:
        """Decrypt card state and return card with decrypted state."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        encrypted_blob = card.state.derived_state.get("_encrypted_state")
        if not encrypted_blob:
            return card

        aesgcm = AESGCM(self._key)
        raw = base64.b64decode(encrypted_blob)
        nonce = raw[:12]
        ciphertext = raw[12:]
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        from .card import StateBlock as _StateBlock
        state = _StateBlock.model_validate_json(plaintext)
        return card.model_copy(update={"state": state})

    @staticmethod
    def generate_key() -> bytes:
        """Generate a random 32-byte AES-256 key."""
        import os as _os
        return _os.urandom(32)
