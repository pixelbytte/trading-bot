"""
Days 33-34 - Security audit.

Checks the codebase for common security issues before launch.
Exits 1 if any critical finding is found.

Run with:
    python -m scripts.security_audit
"""

import sys
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
PASS_TAG = "  PASS "
FAIL_TAG = "  FAIL "
WARN_TAG = "  WARN "

findings = []   # (severity, name, detail)


def passed(name):
    print(f"{PASS_TAG} {name}")
    findings.append(("pass", name, ""))


def failed(name, detail=""):
    print(f"{FAIL_TAG} {name}")
    if detail:
        print(f"         -> {detail}")
    findings.append(("fail", name, detail))


def warn(name, detail=""):
    print(f"{WARN_TAG} {name}")
    if detail:
        print(f"         -> {detail}")
    findings.append(("warn", name, detail))


def _py_files():
    """Production .py files, excluding venv, __pycache__, and this script."""
    excluded_names = {"security_audit.py", "stress_test.py"}
    # Also exclude root-level test_ files — they're Day 1-3 sanity scripts,
    # not production code, so TradingClient direct usage is fine there.
    return [
        p for p in ROOT.rglob("*.py")
        if "venv" not in p.parts
        and "__pycache__" not in p.parts
        and p.name not in excluded_names
        and not (p.parent == ROOT and p.name.startswith("test_"))
    ]


def section(title):
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


# ---------------------------------------------------------------------------
# 1. No hardcoded secrets
# ---------------------------------------------------------------------------
section("1. No hardcoded secrets")

# Patterns that suggest hardcoded credentials
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"sk-[a-zA-Z0-9]{32,}", "OpenAI/Anthropic API key"),
    (r"xox[bpoa]-[0-9A-Za-z\-]{10,}", "Slack token"),
    (r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_\-]+", "Discord webhook URL"),
    (r"ALPACA_KEY\s*=\s*['\"][A-Z0-9]{15,}", "Alpaca key literal"),
    (r"ALPACA_SECRET\s*=\s*['\"][A-Za-z0-9+/]{25,}", "Alpaca secret literal"),
]


def check_no_hardcoded_secrets():
    hits = []
    for py in _py_files():
        text = py.read_text(errors="replace")
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(f"{py.relative_to(ROOT)}: {label}")

    if hits:
        for h in hits:
            failed("Hardcoded secret", h)
    else:
        passed("No hardcoded secrets detected")


check_no_hardcoded_secrets()


# ---------------------------------------------------------------------------
# 2. .gitignore covers sensitive files
# ---------------------------------------------------------------------------
section("2. .gitignore covers sensitive paths")

MUST_IGNORE = [".env", "data/*.db", "logs/", "venv/", "*.log"]


def check_gitignore():
    gi = ROOT / ".gitignore"
    if not gi.exists():
        failed(".gitignore exists", "file not found")
        return

    text = gi.read_text()
    for entry in MUST_IGNORE:
        if entry in text:
            passed(f".gitignore covers '{entry}'")
        else:
            failed(f".gitignore covers '{entry}'", f"'{entry}' not in .gitignore")


check_gitignore()


# ---------------------------------------------------------------------------
# 3. USE_PAPER = True
# ---------------------------------------------------------------------------
section("3. Paper mode enforced")


def check_use_paper():
    cfg = ROOT / "config" / "settings.py"
    text = cfg.read_text()
    if "USE_PAPER = True" in text:
        passed("USE_PAPER = True in config/settings.py")
    else:
        failed("USE_PAPER = True", "not found in config/settings.py")


def check_no_live_url_active():
    alpaca = ROOT / "brokers" / "alpaca.py"
    text = alpaca.read_text()
    if "base_url=ALPACA_LIVE_URL" in text or "ALPACA_LIVE_URL," in text:
        failed("ALPACA_LIVE_URL not passed to TradingClient",
               "live URL appears to be active")
    else:
        passed("ALPACA_LIVE_URL defined but never passed to TradingClient")


check_use_paper()
check_no_live_url_active()


# ---------------------------------------------------------------------------
# 4. Wrapper pattern - no direct SDK calls outside brokers/
# ---------------------------------------------------------------------------
section("4. Wrapper pattern - no direct SDK use outside brokers/")


def check_wrapper_pattern():
    violations = []
    for py in _py_files():
        if "brokers" in py.parts:
            continue  # alpaca.py is allowed to use it
        text = py.read_text(errors="replace")
        if "TradingClient(" in text:
            violations.append(str(py.relative_to(ROOT)))

    if violations:
        for v in violations:
            failed("TradingClient used outside brokers/", v)
    else:
        passed("TradingClient only used in brokers/alpaca.py")


def check_no_direct_discord():
    violations = []
    for py in _py_files():
        if "utils" in py.parts:
            continue
        text = py.read_text(errors="replace")
        # Flag direct requests.post to discord webhook
        if "discord.com/api/webhooks" in text:
            violations.append(str(py.relative_to(ROOT)))

    if violations:
        for v in violations:
            failed("Direct Discord webhook call outside utils/", v)
    else:
        passed("Discord calls only via utils/discord.py")


check_wrapper_pattern()
check_no_direct_discord()


# ---------------------------------------------------------------------------
# 5. Position sizing stays in risk/sizing.py
# ---------------------------------------------------------------------------
section("5. Position sizing only in risk/sizing.py")


def check_no_qty_in_strategies():
    violations = []
    qty_patterns = [r"\bqty\s*=\s*\d", r"compute_position_size\("]
    for py in _py_files():
        if "strategies" not in py.parts:
            continue
        if py.name == "base.py":
            continue
        text = py.read_text(errors="replace")
        for pat in qty_patterns:
            if re.search(pat, text):
                violations.append(f"{py.relative_to(ROOT)}: matches '{pat}'")
                break

    if violations:
        for v in violations:
            warn("Strategy file may compute qty", v)
    else:
        passed("No strategy files compute position qty")


check_no_qty_in_strategies()


# ---------------------------------------------------------------------------
# 6. No secrets in git log (last 50 commits)
# ---------------------------------------------------------------------------
section("6. No secrets in git history (last 50 commits)")


def check_git_history():
    try:
        result = subprocess.run(
            ["git", "log", "-50", "-p", "--all"],
            capture_output=True, text=True, cwd=str(ROOT),
            encoding="utf-8", errors="replace"
        )
        log = result.stdout
        secret_hits = []
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, log):
                secret_hits.append(label)

        if secret_hits:
            for h in secret_hits:
                failed("Secret in git history", h)
        else:
            passed("No secret patterns found in last 50 commits")
    except Exception as e:
        warn("Git history check skipped", str(e))


check_git_history()


# ---------------------------------------------------------------------------
# 7. No .env file accidentally staged
# ---------------------------------------------------------------------------
section("7. .env not staged or committed")


def check_env_not_staged():
    try:
        result = subprocess.run(
            ["git", "ls-files", ".env"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        if result.stdout.strip():
            failed(".env is tracked by git", "run: git rm --cached .env")
        else:
            passed(".env is not tracked by git")
    except Exception as e:
        warn(".env check skipped", str(e))


check_env_not_staged()


# ---------------------------------------------------------------------------
# 8. Junk files in repo root
# ---------------------------------------------------------------------------
section("8. No junk files in repo root")


def check_no_junk_root():
    allowed_root_files = {
        "CLAUDE.md", "README.md", "requirements.txt", ".gitignore",
        "test_connection.py", "test_real_trade.py", "test_strategy.py",
        ".env",  # local only, gitignored
    }
    allowed_extensions = {".py", ".md", ".txt", ".yml", ".yaml", ".json",
                          ".gitignore", ".env"}
    junk = []
    for f in ROOT.iterdir():
        if f.is_file() and f.name not in allowed_root_files:
            if f.suffix not in allowed_extensions and not f.name.startswith("."):
                junk.append(f.name)

    if junk:
        for j in junk:
            failed("Junk file in repo root", j)
    else:
        passed("No junk files in repo root")


check_no_junk_root()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total_fails = sum(1 for s, _, _ in findings if s == "fail")
total_warns = sum(1 for s, _, _ in findings if s == "warn")
total_pass  = sum(1 for s, _, _ in findings if s == "pass")

print(f"\n{'='*60}")
print(f"  Security audit: {total_pass} passed, "
      f"{total_fails} failed, {total_warns} warnings")
print(f"{'='*60}\n")

if total_fails:
    print("  Fix all FAIL items before launch.\n")
    sys.exit(1)
