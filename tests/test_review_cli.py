"""Offline CLI checks use synthetic review attestations only."""
import json

from stalecheck.acquisition import MockTransport, acquire
from stalecheck.cli import main
from stalecheck.contracts import json_bytes
from stalecheck.review import acquisition_digest, candidate_digest


def inputs(tmp_path):
    url = 'https://example.com/synthetic'
    content = 'Synthetic service is supported.'
    acquisition = acquire(
        {'schema_version': '1.0', 'format': 'reviewed_public_acquisition',
         'public_reviewed': True, 'queries': [], 'urls': [url], 'primary_domains': []},
        transport=MockTransport([{'path': '/extract', 'status': 200, 'body': {
            'results': [{'url': url, 'raw_content': content}], 'failed_results': []}}]),
        now=lambda: '2026-11-01T12:00:00Z')
    candidate = acquisition['candidates'][0]
    proposal = {
        'candidate_id': candidate['id'], 'acquisition_sha256': acquisition_digest(acquisition),
        'candidate_sha256': candidate_digest(candidate), 'content_sha256': candidate['content_sha256'],
        'quote': content, 'quote_start_byte': 0, 'quote_end_byte': len(content.encode('utf-8')),
        'subject': 'Synthetic service', 'assertion': 'is_supported', 'scope': 'synthetic',
        'value': True, 'effective_from': '2026-11-01', 'end_state': 'unknown',
        'reviewer': 'synthetic-test-reviewer', 'attested': True,
        'reviewed_at': '2026-11-01T12:01:00Z', 'rationale': 'Synthetic test attestation; end date unknown.',
    }
    a, p = tmp_path / 'acquisition.json', tmp_path / 'proposal.json'
    a.write_bytes(json_bytes(acquisition))
    p.write_bytes(json_bytes(proposal))
    return a, p


def test_review_cli_roundtrip_and_no_overwrite(tmp_path, capsys):
    acquisition, proposal = inputs(tmp_path)
    original = acquisition.read_bytes()
    output = tmp_path / 'review.json'
    args = ['review-evidence', '--acquisition', str(acquisition), '--proposal', str(proposal), '--output', str(output)]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['verification_performed'] is False
    retained = output.read_bytes()
    assert main(['verify-review', '--input', str(output), '--acquisition', str(acquisition)]) == 0
    assert json.loads(capsys.readouterr().out)['valid'] is True
    assert main(args) == 2
    assert capsys.readouterr().err
    assert output.read_bytes() == retained
    assert acquisition.read_bytes() == original


def test_review_cli_missing_attestation_creates_nothing(tmp_path, capsys):
    acquisition, proposal = inputs(tmp_path)
    value = json.loads(proposal.read_bytes())
    value['attested'] = False
    proposal.write_bytes(json_bytes(value))
    output = tmp_path / 'review.json'
    assert main(['review-evidence', '--acquisition', str(acquisition), '--proposal', str(proposal), '--output', str(output)]) == 2
    assert capsys.readouterr().err
    assert not output.exists()
