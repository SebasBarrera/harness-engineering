"""Password hashing (A2): PBKDF2-HMAC-SHA256, random 16-byte salt, 100 000 iterations.

Only the salt, the derived key and the iteration count are ever kept (V1).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

PBKDF2_ITERATIONS = 100_000
SALT_BYTES = 16


@dataclass(slots=True, frozen=True)
class PasswordHash:
    salt: bytes
    digest: bytes
    iterations: int

    def __repr__(self) -> str:  # never print key material
        return f"PasswordHash(iterations={self.iterations})"


def hash_password(password: str) -> PasswordHash:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return PasswordHash(salt=salt, digest=digest, iterations=PBKDF2_ITERATIONS)


def verify_password(password: str, stored: PasswordHash) -> bool:
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), stored.salt, stored.iterations)
    return hmac.compare_digest(candidate, stored.digest)
