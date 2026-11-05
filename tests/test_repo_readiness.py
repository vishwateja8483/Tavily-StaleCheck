"""Read-only hygiene checks must not expose credentials or follow links."""
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit-repo-readiness.py"
spec = importlib.util.spec_from_file_location("repo_readiness", SCRIPT)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def test_inventory_does_not_read_credentials_or_change_files(tmp_path, monkeypatch):
    secret = tmp_path / ".env.local"
    secret.write_text("SYNTHETIC_CREDENTIAL=not-a-real-token")
    original = Path.read_text
    def read(path, *args, **kwargs):
        assert path != secret, "Credential content must not be read"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    report = audit_module.audit(tmp_path)
    assert report["sensitive_filename_candidates"] == [".env.local"]
    assert "not-a-real-token" not in str(report)
    assert secret.read_bytes() == b"SYNTHETIC_CREDENTIAL=not-a-real-token"


def test_only_known_packaging_outputs_are_candidates(tmp_path):
    for relative in ("build", "other/build", "runs/retained"):
        directory = tmp_path / relative
        directory.mkdir(parents=True)
        (directory / "source.py").write_text("pass\n")
    report = audit_module.audit(tmp_path)
    assert sorted(report["generated_candidates"]) == ["build"]
    assert report["files_inspected"] == 1
    assert (tmp_path / "runs/retained/source.py").is_file()


def test_links_are_not_traversed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_text("synthetic")
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    report = audit_module.audit(root)
    assert report["links_or_reparse_points"] == ["linked"]
    assert report["files_inspected"] == 0
    assert report["sensitive_filename_candidates"] == []


def test_tavily_pattern_in_additional_text_formats_is_location_only(tmp_path):
    token = 'tvly-' + 'a' * 30  # Invented pattern, not an actual credential.
    (tmp_path / 'sample.html').write_text(f'<div>{token}</div>\n')
    report = audit_module.audit(tmp_path)
    assert report['secret_pattern_candidates'] == [{'path': 'sample.html', 'line': 1}]
    assert token not in str(report)


def test_secret_findings_contain_locations_only(tmp_path):
    token = "ghp_" + "a" * 35  # Invented pattern, not an actual credential.
    (tmp_path / "sample.py").write_text(f"value = '{token}'\n")
    report = audit_module.audit(tmp_path)
    assert report["secret_pattern_candidates"] == [{"path": "sample.py", "line": 1}]
    assert token not in str(report)
