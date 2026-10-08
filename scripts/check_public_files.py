"""Check Git-tracked release files without displaying any matched secrets."""

import argparse
import re
import subprocess
from pathlib import Path


SECRET_PATTERNS = {
    "API credential": re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "local machine path": re.compile(r"[A-Za-z]:[\\/](?:Users|OpenTCM|P2CoC|Anaconda)", re.I),
}
PRIVATE_ROOTS = {"research", "TCMKE", "logs", "cloudflared", ".aws", ".cloudflared", ".tmp", "tmp"}
PRIVATE_SUFFIXES = {".sqlite", ".db", ".pem", ".key", ".p12", ".pfx", ".jsonl", ".docx", ".xlsx", ".pptx"}


def check_paths(root: Path, paths: list[str]) -> list[str]:
    findings = []
    for name in paths:
        path = Path(name)
        file = root / path
        if not file.is_file():
            continue
        forbidden = (
            any(part in PRIVATE_ROOTS for part in path.parts)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or (path.parts[0] == "data" and path.as_posix() != "data/TCMKG.py")
        )
        if forbidden:
            findings.append(f"{name}: private file must not be tracked")
            continue
        if file.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{name}: unsupported binary file requires manual review")
            continue
        for number, line in enumerate(content.splitlines(), 1):
            for kind, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{name}:{number}: possible {kind}")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=args.root, check=True, capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    findings = check_paths(args.root, [path for path in tracked if path])
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print("Tracked files passed the public-release privacy check.")


if __name__ == "__main__":
    main()
