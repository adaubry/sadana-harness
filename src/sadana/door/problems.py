"""RFC 9457 Problem Details — the door's one error shape.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 12-13.
The console's contract fixes exactly thirteen codes and their statuses; this
module is where that closed set lives, and nowhere else names one directly —
a caller reaches for `problems.make("VALIDATION", ...)`, never a bare `400`.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The closed thirteen. `code -> status`. Nothing outside this dict is a
#: valid Problem code, checked once below at import rather than on every
#: `make()` call that gets it right.
CODES: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "PAYMENT_REQUIRED": 402,
    "FORBIDDEN": 403,
    "VALIDATION": 400,
    "CONFLICT": 409,
    "PRECONDITION_FAILED": 412,
    "RATE_LIMITED": 429,
    "QUOTA_EXCEEDED": 429,
    "HARNESS_OFFLINE": 503,
    "HARNESS_CAPABILITY_MISSING": 501,
    "IDEMPOTENCY_MISMATCH": 422,
    "INTERNAL": 500,
}

if len(CODES) != 13:
    raise ValueError(f"problems.CODES has {len(CODES)} entries; the closed set is exactly thirteen")


@dataclass(frozen=True)
class Problem:
    code: str
    status: int
    title: str
    detail: str | None = None
    instance: str | None = None
    errors: tuple[dict[str, str], ...] | None = None

    def to_json(self) -> dict[str, object]:
        body: dict[str, object] = {
            "type": f"https://sadana.dev/errors/{self.code}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        if self.instance is not None:
            body["instance"] = self.instance
        if self.errors is not None:
            body["errors"] = [dict(e) for e in self.errors]
        return body


def make(
    code: str,
    detail: str | None = None,
    *,
    errors: tuple[dict[str, str], ...] | None = None,
    instance: str | None = None,
) -> Problem:
    """Build a `Problem` for a registered `code`. An unregistered `code` is a
    bug at the call site, not bad data — it raises rather than producing a
    Problem Details body nothing in the closed set recognises."""
    if code not in CODES:
        raise ValueError(f"{code!r} is not a registered Problem code; the closed list is problems.CODES")
    return Problem(
        code=code,
        status=CODES[code],
        title=code.replace("_", " ").title(),
        detail=detail,
        instance=instance,
        errors=errors,
    )
