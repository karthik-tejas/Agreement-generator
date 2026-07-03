"""Heuristic FCRA (India) compliance checks for a generated agreement.

The Foreign Contribution (Regulation) Act, 2010 (as amended in 2020) governs
how organizations receiving foreign contribution in India may use and pass
on those funds. This module runs a keyword/regex-based checklist of common
contractual safeguards against the generated agreement's full text.

This is an automated heuristic aid, not legal advice — it flags likely gaps
in common boilerplate language but cannot certify legal compliance.

To add a new check, write a function `(text: str) -> ComplianceIssue | None`
and append it to `RULES`.
"""

from __future__ import annotations

import io
import re
from typing import Callable

from docx import Document

from .extraction import extract_full_text
from .models import ComplianceIssue, ComplianceReport

RuleFn = Callable[[str], "ComplianceIssue | None"]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


_TRANSFER_VERB = r"(?:transfer|sub-?grant|re-?transfer)"
_CONTRIBUTION_NOUN = r"(?:foreign contribution|funds|grant)"


def check_no_transfer_of_contribution(text: str) -> ComplianceIssue | None:
    permissive_patterns = [
        rf"\bmay\b.{{0,60}}\b{_TRANSFER_VERB}\b.{{0,60}}\b{_CONTRIBUTION_NOUN}\b",
        rf"\b{_CONTRIBUTION_NOUN}\b.{{0,60}}\bmay be\b.{{0,60}}\b{_TRANSFER_VERB}\w*\b.{{0,40}}\bto\b.{{0,30}}"
        r"(?:other|third)\s+(?:person|entity|organi[sz]ation)",
        rf"\bpermitted\s+to\b.{{0,60}}\b{_TRANSFER_VERB}\b.{{0,60}}\b{_CONTRIBUTION_NOUN}\b",
    ]
    if _matches_any(text, permissive_patterns):
        return ComplianceIssue(
            clause="Transfer / sub-granting of foreign contribution",
            reason=(
                "The agreement appears to permit transferring or sub-granting the foreign "
                "contribution to another person or entity. Under FCRA (as amended in 2020), "
                "foreign contribution cannot be transferred to any other person, whether or "
                "not that person is itself registered under, or has obtained prior permission "
                "under, FCRA."
            ),
            recommendation=(
                "Remove or rewrite the clause permitting onward transfer/sub-granting of "
                "foreign contribution. State explicitly that the recipient shall utilize the "
                "funds itself for the stated purpose and shall not transfer them, in cash or "
                "kind, to any other person or organization."
            ),
            severity="non_compliant",
        )

    prohibition_patterns = [
        rf"\bshall not\b.{{0,80}}\b{_TRANSFER_VERB}\w*\b.{{0,80}}\b{_CONTRIBUTION_NOUN}\b",
        rf"\bno\b.{{0,30}}\b{_CONTRIBUTION_NOUN}\b.{{0,40}}\bshall be\b.{{0,20}}\btransferred\b",
        r"\bprohibited from\b.{0,20}\btransferring\b",
        rf"\bshall not be\b.{{0,20}}(?:sub-?granted|transferred|re-?transferred)",
    ]
    if not _matches_any(text, prohibition_patterns):
        return ComplianceIssue(
            clause="Transfer / sub-granting of foreign contribution",
            reason=(
                "The agreement does not include an explicit clause prohibiting transfer or "
                "sub-granting of the foreign contribution to another person or entity, as "
                "required under the FCRA 2020 amendment."
            ),
            recommendation=(
                "Add a clause stating that the recipient shall not transfer, sub-grant, or "
                "re-transfer any part of the foreign contribution to any other person or "
                "organization, whether or not registered under FCRA."
            ),
            severity="missing",
        )
    return None


def check_purpose_restriction(text: str) -> ComplianceIssue | None:
    patterns = [
        r"utili[sz]ed?\s+(?:solely|only|exclusively)\s+for\s+the\s+purpose",
        r"shall\s+be\s+used\s+(?:solely|only|exclusively)\s+for\s+the\s+(?:purpose|project|activity|programme|program)",
        r"purpose\s+(?:for\s+which|specified|stated|sanctioned)\s+(?:the\s+)?(?:funds|foreign contribution|grant)",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Purpose / utilization restriction",
        reason=(
            "The agreement does not clearly restrict use of the funds to the specific "
            "purpose sanctioned in the FCRA-registered project, as required by FCRA."
        ),
        recommendation=(
            "Add a clause stating the funds shall be utilized solely for the purpose "
            "described in the Terms of Reference / sanctioned project, and for no other "
            "purpose without prior written consent."
        ),
        severity="missing",
    )


def check_audit_and_inspection_rights(text: str) -> ComplianceIssue | None:
    patterns = [
        r"right\s+to\s+audit",
        r"audit(?:\s+and)?\s+inspect",
        r"inspect\s+(?:the\s+)?(?:books|records|accounts)",
        r"maintain(?:ing)?\s+(?:proper\s+)?(?:books|records|accounts).{0,40}(?:audit|inspection)",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Audit and inspection rights",
        reason=(
            "The agreement does not grant a right to audit or inspect the counterparty's "
            "books, records, or accounts relating to utilization of the funds."
        ),
        recommendation=(
            "Add a clause giving the FCRA-registered organization (and, where applicable, "
            "government authorities) the right to audit and inspect books, records, and "
            "accounts related to the use of the funds."
        ),
        severity="missing",
    )


def check_reporting_obligations(text: str) -> ComplianceIssue | None:
    patterns = [
        r"periodic(?:al)?\s+report",
        r"submit\s+(?:a\s+)?(?:progress|financial|utili[sz]ation|narrative)\s+report",
        r"reporting\s+(?:requirement|obligation)",
        r"utili[sz]ation\s+certificate",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Reporting and documentation obligations",
        reason=(
            "The agreement does not require periodic financial or narrative reporting, "
            "which is needed to support the FCRA-registered organization's statutory "
            "returns (e.g. Form FC-4)."
        ),
        recommendation=(
            "Add a clause requiring the counterparty to submit periodic progress and "
            "financial/utilization reports in a format and frequency sufficient to support "
            "statutory FCRA reporting."
        ),
        severity="missing",
    )


def check_refund_on_cancellation_or_misuse(text: str) -> ComplianceIssue | None:
    patterns = [
        r"refund.{0,20}(?:unspent|unutili[sz]ed|misused)",
        r"repay.{0,30}(?:funds|amount|contribution).{0,40}(?:cancellation|suspension|revocation|misuse)",
        r"registration\s+(?:is\s+)?(?:cancelled|suspended|revoked).{0,60}(?:refund|repay|return)",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Refund on cancellation, suspension, or misuse",
        reason=(
            "The agreement does not require repayment of unspent or misused funds if FCRA "
            "registration is suspended or cancelled, or if funds are used for purposes "
            "other than those sanctioned."
        ),
        recommendation=(
            "Add a clause requiring the counterparty to refund unspent or misused funds "
            "immediately if FCRA registration is suspended/cancelled, or if funds are found "
            "to have been used for an unsanctioned purpose."
        ),
        severity="missing",
    )


def check_fcra_status_representation(text: str) -> ComplianceIssue | None:
    patterns = [
        r"represents?\s+and\s+warrants?.{0,60}fcra",
        r"fcra\s+registration",
        r"foreign contribution\s*\(regulation\)\s*act",
        r"not\s+(?:itself\s+)?required\s+to\s+(?:register|obtain).{0,20}fcra",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Counterparty's FCRA status representation",
        reason=(
            "The agreement does not include a representation from the counterparty about "
            "its own FCRA registration/compliance status, or an acknowledgement of the "
            "applicability of FCRA to the engagement."
        ),
        recommendation=(
            "Add a representation clause where the counterparty confirms its FCRA "
            "registration status (or confirms it is not itself required to be FCRA-"
            "registered for this engagement) and agrees to comply with applicable FCRA "
            "provisions."
        ),
        severity="missing",
    )


def check_prohibited_use_language(text: str) -> ComplianceIssue | None:
    patterns = [
        r"political\s+(?:party|activity|purpose)",
        r"religious\s+conversion",
        r"speculative\s+(?:business|investment|activity)",
    ]
    if _matches_any(text, patterns):
        return None
    return ComplianceIssue(
        clause="Prohibited-use language",
        reason=(
            "The agreement does not explicitly state that funds may not be used for "
            "political activities, religious conversion, or speculative investment — "
            "activities FCRA restricts foreign contribution from funding."
        ),
        recommendation=(
            "Consider adding a clause stating that funds shall not be used for any "
            "political activity, religious conversion, or speculative business/investment "
            "activity."
        ),
        severity="advisory",
    )


RULES: list[RuleFn] = [
    check_no_transfer_of_contribution,
    check_purpose_restriction,
    check_audit_and_inspection_rights,
    check_reporting_obligations,
    check_refund_on_cancellation_or_misuse,
    check_fcra_status_representation,
    check_prohibited_use_language,
]


def run_fcra_check(docx_bytes: bytes) -> ComplianceReport:
    """Run all registered FCRA rules against a generated agreement's text."""
    document = Document(io.BytesIO(docx_bytes))
    text = extract_full_text(document)

    issues: list[ComplianceIssue] = []
    for rule in RULES:
        issue = rule(text)
        if issue is not None:
            issues.append(issue)

    is_compliant = not any(issue.severity in ("non_compliant", "missing") for issue in issues)
    return ComplianceReport(is_compliant=is_compliant, issues=issues)
