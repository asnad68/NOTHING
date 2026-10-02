"""Identity ownership and registration authorization policy for NOTHING.

Authentication answers: "Who is operating this browser?"
Authorization answers: "Does this principal have sufficient control to make
an official claim about this business/brand?"

The policy intentionally does NOT equate:
- a Google account with ownership of a company,
- an Ethereum address with ownership of a company, or
- payment with verification.

Official organization/brand control requires independently verifiable control
of the claimed organization domain, plus an authenticated principal bound to
that organization domain. Claims that cannot be bound to a domain are routed
to manual/authoritative review instead of being silently accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any, Iterable, Protocol
from urllib.parse import urlparse


class IdentityControlError(ValueError):
    """Raised when an identity-control input is invalid."""


def _is_valid_ascii_domain(value: str) -> bool:
    if not 1 <= len(value) <= 253:
        return False
    labels = value.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not 1 <= len(label) <= 63:
            return False
        if label[0] == "-" or label[-1] == "-":
            return False
        if not re.fullmatch(r"[A-Za-z0-9-]+", label):
            return False
    return True
NONCE_RE = re.compile(r"^[A-Za-z0-9]{8,}$")
ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    method: str
    subject: str
    email: str | None = None
    email_domain: str | None = None
    wallet_address: str | None = None
    hosted_domain: str | None = None


@dataclass(frozen=True)
class DomainControlChallenge:
    domain: str
    challenge: str
    record_name: str
    record_value: str
    challenge_digest: str


@dataclass(frozen=True)
class OwnershipDecision:
    state: str
    allowed: bool
    reason: str
    domain: str | None
    authentication_method: str | None


class TxtResolver(Protocol):
    def resolve(self, name: str, rdtype: str = "TXT", lifetime: float = 5.0) -> Iterable[Any]:
        ...


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().rstrip(".").lower()
    if not raw:
        raise IdentityControlError("domain is required")
    if len(raw) > 253 or any(ch.isspace() for ch in raw):
        raise IdentityControlError("domain is invalid")
    try:
        ascii_domain = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise IdentityControlError("domain is not valid IDNA") from exc
    if not _is_valid_ascii_domain(ascii_domain):
        raise IdentityControlError("domain must be a public DNS hostname")
    try:
        ipaddress.ip_address(ascii_domain)
    except ValueError:
        pass
    else:
        raise IdentityControlError("IP addresses cannot be used as organization domains")
    if ascii_domain in {"localhost", "localhost.localdomain"}:
        raise IdentityControlError("localhost cannot be used as an organization domain")
    if ascii_domain.endswith((".local", ".internal", ".lan", ".home.arpa")):
        raise IdentityControlError("private/local domains cannot be used as organization domains")
    return ascii_domain


def domain_from_email(email: str) -> str:
    address = parseaddr(str(email or "").strip())[1].lower()
    if "@" not in address:
        raise IdentityControlError("email must be a valid address")
    local, domain = address.rsplit("@", 1)
    if not local:
        raise IdentityControlError("email local part is empty")
    return normalize_domain(domain)


def domain_from_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise IdentityControlError("organization website must be an absolute HTTPS URL")
    return normalize_domain(parsed.hostname)


def same_or_subdomain(host: str, domain: str) -> bool:
    host_norm = normalize_domain(host)
    domain_norm = normalize_domain(domain)
    return host_norm == domain_norm or host_norm.endswith("." + domain_norm)


def build_domain_challenge(domain: str, *, challenge: str | None = None) -> DomainControlChallenge:
    domain = normalize_domain(domain)
    token = challenge or secrets.token_urlsafe(32)
    if not 24 <= len(token) <= 256:
        raise IdentityControlError("challenge length is outside the supported range")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return DomainControlChallenge(
        domain=domain,
        challenge=token,
        record_name=f"_nothing-challenge.{domain}",
        record_value=f"NOTHING-DOMAIN-VERIFICATION={token}",
        challenge_digest=digest,
    )


def verify_domain_txt(
    challenge: DomainControlChallenge,
    resolver: TxtResolver,
) -> bool:
    try:
        answers = resolver.resolve(challenge.record_name, "TXT", lifetime=5.0)
    except Exception:
        return False
    for answer in answers:
        try:
            chunks = getattr(answer, "strings", None)
            if chunks is not None:
                value = b"".join(chunks).decode("utf-8")
            else:
                value = str(answer).strip('"')
        except (UnicodeError, AttributeError):
            continue
        if value == challenge.record_value:
            return True
    return False


def verify_google_workspace_principal(
    *,
    subject: str,
    email: str,
    email_verified: bool,
    hosted_domain: str | None,
    expected_domain: str,
) -> AuthenticatedPrincipal:
    domain = normalize_domain(expected_domain)
    email_domain = domain_from_email(email)
    hd = normalize_domain(hosted_domain) if hosted_domain else None
    if not email_verified:
        raise IdentityControlError("Google email is not verified")
    if email_domain != domain:
        raise IdentityControlError("Google email domain does not match the controlled organization domain")
    if hd != domain:
        raise IdentityControlError("Google hosted domain does not match the controlled organization domain")
    return AuthenticatedPrincipal(
        method="google_workspace",
        subject=str(subject),
        email=str(email).strip().lower(),
        email_domain=email_domain,
        hosted_domain=hd,
    )


def normalize_wallet_address(address: str) -> str:
    value = str(address or "").strip()
    if not ETH_ADDRESS_RE.fullmatch(value):
        raise IdentityControlError("wallet address must be a 20-byte EVM address")
    return value.lower()


def wallet_principal(subject: str, address: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(method="wallet_siwe", subject=str(subject), wallet_address=normalize_wallet_address(address))


def build_siwe_message(
    *,
    domain: str,
    address: str,
    uri: str,
    chain_id: int,
    nonce: str,
    issued_at: str,
    statement: str = "Sign in to NOTHING. This proves control of the wallet only.",
    expiration_time: str | None = None,
) -> str:
    domain = normalize_domain(domain)
    address = normalize_wallet_address(address)
    parsed = urlparse(uri)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise IdentityControlError("SIWE URI must be HTTPS")
    if normalize_domain(parsed.hostname) != domain:
        raise IdentityControlError("SIWE URI host must match the SIWE domain")
    if not NONCE_RE.fullmatch(nonce):
        raise IdentityControlError("SIWE nonce must contain at least 8 alphanumeric characters")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise IdentityControlError("SIWE chain_id must be a positive integer")
    if "\n" in statement:
        raise IdentityControlError("SIWE statement must not contain line breaks")
    lines = [
        f"{domain} wants you to sign in with your Ethereum account:",
        address,
        "",
        statement,
        "",
        f"URI: {uri}",
        "Version: 1",
        f"Chain ID: {chain_id}",
        f"Nonce: {nonce}",
        f"Issued At: {issued_at}",
    ]
    if expiration_time:
        lines.append(f"Expiration Time: {expiration_time}")
    return "\n".join(lines)


def _parse_siwe(message: str) -> dict[str, str]:
    lines = str(message or "").splitlines()
    if len(lines) < 9:
        raise IdentityControlError("SIWE message is too short")
    suffix = " wants you to sign in with your Ethereum account:"
    if not lines[0].endswith(suffix):
        raise IdentityControlError("invalid SIWE domain line")
    domain = lines[0][:-len(suffix)]
    address = lines[1].strip()
    if lines[2] != "":
        raise IdentityControlError("invalid SIWE blank line")
    if lines[4] != "":
        raise IdentityControlError("invalid SIWE separator")
    fields: dict[str, str] = {"domain": normalize_domain(domain), "address": normalize_wallet_address(address)}
    for line in lines[5:]:
        if ": " not in line:
            raise IdentityControlError("invalid SIWE field")
        key, value = line.split(": ", 1)
        fields[key] = value
    for required in ("URI", "Version", "Chain ID", "Nonce", "Issued At"):
        if required not in fields:
            raise IdentityControlError(f"SIWE field missing: {required}")
    if fields["Version"] != "1":
        raise IdentityControlError("unsupported SIWE version")
    if not NONCE_RE.fullmatch(fields["Nonce"]):
        raise IdentityControlError("invalid SIWE nonce")
    try:
        chain_id = int(fields["Chain ID"])
    except ValueError as exc:
        raise IdentityControlError("invalid SIWE chain id") from exc
    if chain_id <= 0:
        raise IdentityControlError("invalid SIWE chain id")
    fields["_chain_id"] = str(chain_id)
    return fields


def _parse_utc(value: str) -> datetime:
    raw = str(value).strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise IdentityControlError("invalid SIWE timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IdentityControlError("SIWE timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_siwe_signature(
    *,
    message: str,
    signature: str,
    expected_domain: str,
    expected_uri: str,
    expected_chain_id: int,
    expected_nonce: str,
    now: datetime | None = None,
) -> AuthenticatedPrincipal:
    fields = _parse_siwe(message)
    domain = normalize_domain(expected_domain)
    expected_uri = str(expected_uri).strip()
    if fields["domain"] != domain:
        raise IdentityControlError("SIWE domain does not match the expected NOTHING origin")
    if fields["URI"] != expected_uri:
        raise IdentityControlError("SIWE URI does not match the expected origin")
    if int(fields["_chain_id"]) != expected_chain_id:
        raise IdentityControlError("SIWE chain ID does not match the expected chain")
    if fields["Nonce"] != expected_nonce:
        raise IdentityControlError("SIWE nonce does not match the server-issued challenge")
    issued_at = _parse_utc(fields["Issued At"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at > current:
        raise IdentityControlError("SIWE issued-at time is in the future")
    if "Expiration Time" in fields and current > _parse_utc(fields["Expiration Time"]):
        raise IdentityControlError("SIWE message has expired")
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:
        raise IdentityControlError("eth-account is required for wallet authentication") from exc
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as exc:
        raise IdentityControlError("wallet signature could not be verified") from exc
    if recovered.lower() != fields["address"].lower():
        raise IdentityControlError("wallet signature does not match the stated address")
    return wallet_principal(subject=fields["address"], address=fields["address"])


def issue_domain_challenge_token(domain: str, secret: str, *, now: datetime | None = None, ttl_seconds: int = 1800) -> DomainControlChallenge:
    """Issue a signed, expiring DNS challenge without requiring mutable challenge state."""
    domain = normalize_domain(domain)
    if not isinstance(secret, str) or len(secret) < 32:
        raise IdentityControlError("domain challenge secret must be at least 32 characters")
    if not 300 <= ttl_seconds <= 86400:
        raise IdentityControlError("domain challenge TTL must be between 5 minutes and 24 hours")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    expires = int(current.timestamp()) + ttl_seconds
    nonce = secrets.token_hex(24)
    unsigned = f"v1|{domain}|{expires}|{nonce}"
    signature = hmac.new(secret.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{unsigned}|{signature}"
    return build_domain_challenge(domain, challenge=token)


def verify_domain_challenge_token(
    token: str,
    domain: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Verify an issued challenge token before consulting DNS."""
    domain = normalize_domain(domain)
    if not isinstance(secret, str) or len(secret) < 32:
        return False
    parts = str(token or "").split("|")
    if len(parts) != 5 or parts[0] != "v1":
        return False
    _, token_domain, expires_text, nonce, provided_signature = parts
    if token_domain != domain or not NONCE_RE.fullmatch(nonce):
        return False
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    if expires <= int((now or datetime.now(timezone.utc)).timestamp()):
        return False
    unsigned = f"v1|{token_domain}|{expires}|{nonce}"
    expected_signature = hmac.new(secret.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(provided_signature, expected_signature)


def verify_google_id_token(
    id_token: str,
    *,
    client_id: str,
    expected_domain: str | None = None,
    expected_nonce: str | None = None,
    jwks_client: Any | None = None,
) -> dict[str, Any]:
    """Verify a Google OpenID Connect ID token and return safe claims.

    The caller must still apply organization authorization and domain-control
    policy. A valid Google identity token authenticates a user; it does not prove
    company authority.
    """
    if not client_id:
        raise IdentityControlError("Google client_id is not configured")
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:
        raise IdentityControlError("PyJWT[crypto] is required for Google authentication") from exc

    token = str(id_token or "").strip()
    if not token:
        raise IdentityControlError("Google ID token is required")
    client = jwks_client or PyJWKClient(
        "https://www.googleapis.com/oauth2/v3/certs",
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
        cache_keys=True,
    )
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise IdentityControlError("Google ID token must use RS256")
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "email", "email_verified"]},
        )
    except IdentityControlError:
        raise
    except Exception as exc:
        raise IdentityControlError("Google ID token could not be verified") from exc

    issuer = str(claims.get("iss", "")).rstrip("/")
    if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
        raise IdentityControlError("Google ID token issuer is not accepted")
    if claims.get("email_verified") is not True:
        raise IdentityControlError("Google email is not verified")

    email = str(claims.get("email", "")).strip().lower()
    email_domain = domain_from_email(email)
    hd = claims.get("hd")
    hosted_domain = normalize_domain(hd) if isinstance(hd, str) and hd else None

    result = dict(claims)
    result["email"] = email
    result["email_domain"] = email_domain
    result["hosted_domain"] = hosted_domain

    if expected_domain is not None:
        expected = normalize_domain(expected_domain)
        if email_domain != expected or hosted_domain != expected:
            raise IdentityControlError("Google identity is not bound to the expected organization domain")
    if expected_nonce is not None:
        actual_nonce = claims.get("nonce")
        if not isinstance(actual_nonce, str) or not secrets.compare_digest(actual_nonce, str(expected_nonce)):
            raise IdentityControlError("Google ID token nonce does not match the server-issued authentication challenge")
    return result


def evaluate_official_claim(
    *,
    brand_name: str,
    requested_domain: str | None,
    domain_controlled: bool,
    principal: AuthenticatedPrincipal | None,
    website_url: str | None = None,
    authoritative_name_match: bool = False,
) -> OwnershipDecision:
    if not str(brand_name or "").strip():
        raise IdentityControlError("brand_name is required")
    domain = normalize_domain(requested_domain) if requested_domain else None
    if principal is None:
        return OwnershipDecision("SELF_CLAIMED_ONLY", False, "No authenticated principal is present.", domain, None)
    if domain is None:
        return OwnershipDecision("REQUIRES_AUTHORITATIVE_REVIEW", False, "Official control requires a public organization domain or an authoritative registry path.", None, principal.method)
    if not domain_controlled:
        return OwnershipDecision("AUTHENTICATED_BUT_NOT_ORGANIZATION_CONTROLLED", False, "The requester is authenticated, but control of the organization domain has not been independently verified.", domain, principal.method)
    if website_url:
        website_domain = domain_from_url(website_url)
        if not same_or_subdomain(website_domain, domain):
            return OwnershipDecision("DOMAIN_MISMATCH", False, "The supplied website is outside the controlled organization domain.", domain, principal.method)
    if principal.method == "google_workspace":
        if principal.email_domain != domain or principal.hosted_domain != domain:
            return OwnershipDecision("AUTHENTICATION_DOMAIN_MISMATCH", False, "The Google Workspace identity is not bound to the controlled organization domain.", domain, principal.method)
    elif principal.method != "wallet_siwe":
        return OwnershipDecision("UNSUPPORTED_AUTHENTICATION_METHOD", False, "The authentication method is not approved for official organization claims.", domain, principal.method)
    normalized_brand = re.sub(r"[^a-z0-9]+", "", str(brand_name).casefold())
    domain_labels = domain.split(".")
    normalized_domain = domain_labels[0].casefold()
    direct_name_match = (
        len(domain_labels) == 2
        and normalized_brand == re.sub(r"[^a-z0-9]+", "", normalized_domain)
    )
    if direct_name_match or authoritative_name_match:
        return OwnershipDecision("ORGANIZATION_CONTROLLED", True, "Authenticated principal and independently verified organization domain satisfy the official claim policy.", domain, principal.method)
    return OwnershipDecision("REQUIRES_AUTHORITATIVE_REVIEW", False, "The requester controls the domain, but the brand name is not sufficiently bound to that domain for automatic official registration.", domain, principal.method)
