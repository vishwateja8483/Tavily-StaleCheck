"""Version dispatch must preserve retained semantics and reject substitutions."""
import copy
import json
from pathlib import Path

import pytest

from stalecheck import report
from stalecheck.contracts import ValidationError, json_bytes, sha
from stalecheck.verify_artifacts import verify_artifacts
from stalecheck.versions import report_builder, v1_config


FIXTURE = Path(__file__).parents[1] / 'src/stalecheck/fixtures/demo.json'


def request():
    return json.loads(FIXTURE.read_bytes())


def rewrite_registered(directory, name, value):
    raw = json_bytes(value)
    (directory / name).write_bytes(raw)
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    for entry in manifest['artifacts']:
        if entry['path'] == name:
            entry.update(sha256=sha(raw), bytes=len(raw))
    (directory / 'manifest.json').write_bytes(json_bytes(manifest))


@pytest.mark.parametrize('version', [None, True, 1, '2.0', '1.1', ''])
def test_unknown_input_version_has_no_fallback(version):
    value = request()
    value['schema_version'] = version
    with pytest.raises(ValidationError, match='schema version'):
        report.make_report(value, 'test')


@pytest.mark.parametrize('field,value', [
    ('network', 0), ('max_claims', 5.0), ('package_version', '0.2.0'),
    ('algorithm', 'reviewed-lifecycle-intervals-v2'), ('model', False),
])
def test_rehashed_config_substitution_rejected(tmp_path, field, value):
    directory = tmp_path / 'run'
    report.write_run(request(), directory)
    config = v1_config()
    config[field] = value
    rewrite_registered(directory, 'config.json', config)
    with pytest.raises(ValidationError, match='config/version'):
        verify_artifacts(directory)


def test_rehashed_unknown_input_does_not_downgrade(tmp_path):
    directory = tmp_path / 'run'
    value = request()
    report.write_run(value, directory)
    value['schema_version'] = '2.0'
    rewrite_registered(directory, 'input.json', value)
    with pytest.raises(ValidationError, match='schema version'):
        verify_artifacts(directory)


def test_invalid_v1_input_is_not_reinterpreted():
    value = request()
    value['verification_date'] = 'not-a-date'
    with pytest.raises(ValidationError):
        report_builder(value)(value, 'test')


def test_report_and_compatibility_aliases_cannot_mutate_v1_rules(monkeypatch, tmp_path):
    value = request()
    expected = report.make_report(value, 'test')
    changed = report.make_report(value, 'test')
    changed['limitations'].clear()
    monkeypatch.setattr(report, 'VERSION', 'future-package-version')
    monkeypatch.setattr(report, 'CONFIG', {'algorithm': 'future'})
    monkeypatch.setattr(report, 'LIMITATIONS', [])
    assert report.make_report(value, 'test') == expected
    directory = tmp_path / 'run'
    report.write_run(value, directory)
    assert json.loads((directory / 'config.json').read_bytes()) == v1_config()
    assert verify_artifacts(directory)['valid'] is True


def test_config_copies_do_not_share_mutable_state():
    config = v1_config()
    expected = copy.deepcopy(config)
    config['network'] = True
    assert v1_config() == expected
