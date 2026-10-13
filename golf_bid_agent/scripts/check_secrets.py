#!/usr/bin/env python3
"""Block API keys and private files from reaching GitHub.

    python scripts/check_secrets.py                 # scan everything git would publish
    python scripts/check_secrets.py --staged        # scan only staged files (used by the hook)
    python scripts/check_secrets.py --install-hook  # run automatically before every commit

Exits with code 1 (and the commit is stopped) if it finds an Anthropic key, a .env file,
anything under secrets/, or generated output files.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEY_RE = re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}")
BLOCKED_NAMES = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets/.*|output/(?!\.gitkeep$).+|approval_log\.jsonl)$")
ALLOWED = {".env.example", "secrets/.env.example"}


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def files_to_check(staged: bool) -> list[str]:
    if staged:
        return git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    # tracked + untracked-but-not-ignored = what `git add -A` would publish
    return sorted(set(git("ls-files")) | set(git("ls-files", "--others", "--exclude-standard")))


def scan(paths: list[str]) -> list[str]:
    problems = []
    for rel in paths:
        name = rel.replace("\\", "/")
        if name not in ALLOWED and not name.endswith(".env.example") and BLOCKED_NAMES.search(name):
            problems.append(f"{rel}: private file type (keys, secrets/ or generated output)")
            continue
        f = REPO / rel
        if not f.is_file() or f.stat().st_size > 5_000_000:
            continue
        data = f.read_bytes()
        if KEY_RE.search(data):
            problems.append(f"{rel}: contains something that looks like an Anthropic API key (sk-ant-...)")
    return problems


def install_hook():
    hooks = REPO / ".git" / "hooks"
    if not hooks.parent.is_dir():
        sys.exit("Not a git repository yet. Run `git init` in this folder first.")
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text('#!/bin/sh\n'
                    'if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi\n'
                    'exec "$PY" "$(git rev-parse --show-toplevel)/scripts/check_secrets.py" --staged\n',
                    encoding="utf-8")
    hook.chmod(0o755)
    print(f"Installed pre-commit hook: {hook}")


def main():
    if "--install-hook" in sys.argv:
        return install_hook()
    try:
        paths = files_to_check("--staged" in sys.argv)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("git is not available or this folder is not a git repository.")
    problems = scan(paths)
    if problems:
        print("SECRET CHECK FAILED - nothing was committed:\n  " + "\n  ".join(problems))
        print("Remove these files from git (git rm --cached <file>) or move them to your workspace folder.")
        sys.exit(1)
    print(f"Secret check passed ({len(paths)} files scanned).")


if __name__ == "__main__":
    main()
