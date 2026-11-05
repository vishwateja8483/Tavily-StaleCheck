"""Read-only repository hygiene inventory; never deletes or prints secret values.

Run before a reviewed cleanup/export. Local run output is
excluded from deletion suggestions. Findings are candidates, not permission to
remove source, license notices or retained failure records.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat

EXCLUDED = {".git", ".venv", "node_modules", "runs", ".claude"}
GENERATED = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "playwright-report", "test-results"}
PACKAGING_OUTPUTS = {"build"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".jsonl", ".toml", ".md", ".mdx", ".txt", ".yaml", ".yml", ".sh", ".ps1", ".env", ".xml", ".html", ".css", ".ini", ".cfg", ".properties"}
SENSITIVE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{30,}|tvly-[A-Za-z0-9_-]{20,})\b")
ABSOLUTE_LOCAL = re.compile(r"(?:[A-Za-z]:[\\/](?:Users|home)[\\/][^\s\"'<>]+|/mnt/[a-z]/Users/[^\s\"'<>]+)")


def audit(root):
    root = Path(root).absolute()
    if (root.is_symlink() or not root.is_dir()
            or getattr(root.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("Expected a regular workspace directory")
    result = {"schema_version": "repo-readiness-audit-1", "git_initialized": (root / ".git").exists(),
              "generated_candidates": [], "sensitive_filename_candidates": [],
              "secret_pattern_candidates": [], "local_path_candidates": [],
              "links_or_reparse_points": [], "oversized_files": [], "excluded": sorted(EXCLUDED),
              "files_inspected": 0, "status": "review_required",
              "limitations": ["Heuristic scan; not a guarantee that secrets or unnecessary files are absent.",
                              "No deletion, Git changes, token acquisition or network calls.",
                              "Run output under runs/ is local and ignored by Git."]}
    for directory, dirs, files in os.walk(root, followlinks=False):
        directory = Path(directory)
        for name in list(dirs):
            path = directory / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                result["links_or_reparse_points"].append(relative)
                dirs.remove(name)
            elif name in EXCLUDED:
                dirs.remove(name)
            elif name in GENERATED or name.endswith(".egg-info") or relative in PACKAGING_OUTPUTS:
                result["generated_candidates"].append(relative)
                dirs.remove(name)
        for name in files:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                result["links_or_reparse_points"].append(relative)
                continue
            result["files_inspected"] += 1
            if (name == ".env" or name.startswith(".env.")
                    or name in {"id_rsa", "id_ed25519", "credentials.json"}
                    or name.endswith((".p12", ".pfx", ".key"))):
                result["sensitive_filename_candidates"].append(relative)
                continue  # Never read likely credential files.
            if info.st_size > 2_000_000:
                result["oversized_files"].append({"path": relative, "bytes": info.st_size})
                continue
            if path.suffix not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if SENSITIVE.search(line):
                    result["secret_pattern_candidates"].append({"path": relative, "line": number})
                if ABSOLUTE_LOCAL.search(line):
                    result["local_path_candidates"].append({"path": relative, "line": number})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    raw = json.dumps(result, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(raw)
        print(json.dumps({"status": result["status"], "files_inspected": result["files_inspected"],
                          "generated_candidates": len(result["generated_candidates"]),
                          "secret_pattern_candidates": len(result["secret_pattern_candidates"])}))
    else:
        print(raw, end="")


if __name__ == "__main__":
    main()
