"""Source-level invariant: shadow-loop.py must never contain signing or submission code."""

import re
from pathlib import Path

import pytest

SHADOW_LOOP = Path(__file__).resolve().parent.parent / "ops" / "shadow-loop.py"

FORBIDDEN_PATTERNS = [
    (r"\bsendTransaction\b", "sendTransaction call"),
    (r"\bKeypair\b", "Keypair import or usage"),
    (r"\bfrom_seed\b", "keypair from seed"),
    (r"\bfrom_json\b", "keypair from JSON"),
    (r"\bsecret\b", "secret key reference"),
    (r"\bmnemonic\b", "mnemonic reference"),
    (r"\bseed_phrase\b", "seed phrase reference"),
    (r"\bprivate_key\b", "private key reference"),
    (r"\b--submit\b", "submit flag"),
    (r"\bsignTransaction\b", "transaction signing"),
    (r"\bsignAllTransactions\b", "batch transaction signing"),
    (r"\bskipPreflight\s*:\s*True\b", "skipPreflight enabled"),
]

# Patterns that are allowed in comments/docstrings but not in code
ALLOWED_IN_COMMENTS = {"sendTransaction", "keypair", "sign", "submit"}


def _strip_comments_and_strings(source: str) -> list[str]:
    """Remove comments, docstrings, and string literals line by line."""
    in_docstring = False
    result = []
    for line in source.splitlines():
        # Track triple-quoted docstrings
        if '"""' in line:
            # Toggle docstring state for multi-line
            count = line.count('"""')
            if count >= 2:
                # Same line — remove the content between
                line = re.sub(r'""".*?"""', '""', line)
            else:
                in_docstring = not in_docstring
                line = re.sub(r'""".*$', '', line)
        if in_docstring:
            result.append("")
            continue
        # Remove inline comments
        if "#" in line:
            line = line.split("#", 1)[0]
        # Remove f-string prefixes
        line = re.sub(r'\bf"', '"', line)
        # Remove single-line triple-quoted
        line = re.sub(r'""".*?"""', '""', line)
        line = re.sub(r"'''.*?'''", "''", line)
        # Remove content inside double quotes
        line = re.sub(r'"[^"]*"', '""', line)
        # Remove content inside single quotes
        line = re.sub(r"'[^']*'", "''", line)
        result.append(line)
    return result


@pytest.mark.parametrize("pattern,description", FORBIDDEN_PATTERNS)
def test_shadow_loop_has_no_signing_or_submission_code(pattern: str, description: str) -> None:
    source = SHADOW_LOOP.read_text(encoding="utf-8")
    lines = _strip_comments_and_strings(source)
    for lineno, (line, original) in enumerate(zip(lines, source.splitlines()), 1):
        if re.search(pattern, line):
            pytest.fail(
                f"shadow-loop.py line {lineno} contains forbidden {description}:\n  {original.strip()}"
            )


def test_shadow_loop_enforces_dry_run() -> None:
    source = SHADOW_LOOP.read_text(encoding="utf-8")
    assert "dry_run" in source, "shadow-loop.py must check config.dry_run"
    assert "not config.dry_run" in source, "shadow-loop.py must refuse non-dry-run config"


def test_shadow_loop_transactions_submitted_is_zero() -> None:
    source = SHADOW_LOOP.read_text(encoding="utf-8")
    assert '"transactions_submitted": 0' in source, (
        "shadow-loop.py must always record transactions_submitted as 0"
    )
