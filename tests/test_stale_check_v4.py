"""Consent, bounded collection, truthful inference and artifact replay; no live calls."""
from copy import deepcopy
from datetime import date
import json

import pytest

from stalecheck.acquisition import MockTransport
from stalecheck.cli import main
from stalecheck.contracts import ValidationError, json_bytes
from stalecheck.export import export_viewer
from stalecheck.gateway import GatewayClient, MockGatewayTransport
from stalecheck.report import write_run
from stalecheck.stale_check_v4 import _freshness, collect_transcript, make_report_v4, render_html
from stalecheck.verify_artifacts import load_verified_run
from test_stale_check import ANSWER, PRICE, VERSION, DOCS, PRICE_PAGE, NOW, claims, proposal, verdict, hits, refusal


def scenario(*, approve=lambda request: True, max_attempts=25, extraction=None, assessments=None, searches=None, **kwargs):
    gt = MockGatewayTransport([extraction or claims(proposal(PRICE, 'acme price'), proposal(VERSION, 'acme version')),
                               *(assessments if assessments is not None else [verdict('CHANGED', ('claim-1-src-1', 'contradicts', 'The Starter plan costs $24 per month'), rewrite='Costs $24.')])])
    st = MockTransport(searches if searches is not None else [hits((DOCS, PRICE_PAGE, '2026-10-20'), credits=1), hits()])
    gateway = GatewayClient('http://localhost:8080', 'openai/test', transport=gt)
    transcript = collect_transcript(ANSWER, verification_date='2026-11-01', primary_domains=['example.com'], gateway=gateway,
                                    search_transport=st, now=lambda: NOW, approve=approve, max_attempts=max_attempts, **kwargs)
    return transcript, make_report_v4(transcript, 'fd-test'), gt, st


def test_completed_flow_qualifies_inference_and_mock_usage(tmp_path):
    transcript, report, gt, st = scenario()
    assert [c['verdict'] for c in report['claims']] == ['POTENTIALLY_CHANGED', 'UNVERIFIABLE']
    assert report['claims'][0]['temporal_change_established'] is False
    assert report['claims'][0]['inference']['automatically_applied'] is False
    assert report['usage']['attempts'] == 4
    assert report['usage']['credits']['scope'] == 'mock_observations_not_spending'
    result = write_run(transcript, tmp_path / 'run')
    assert load_verified_run(result['output'])['report']['claims'] == report['claims']
    with pytest.raises(ValidationError, match='not exportable'):
        export_viewer([result['output']], tmp_path / 'viewer.json')
    html = render_html(report)
    from html import escape
    assert escape(ANSWER) in html and 'Full original answer' in html and '<script' not in html
    assert 'independent_agreement' not in html and 'VERIFIED_CHANGED' not in html


def test_every_claim_has_a_freshness_score_or_says_why_not():
    _, report, _, _ = scenario()
    assert report['claims'][0]['freshness'] == {'state': 'current', 'newest_source_date': '2026-10-20', 'age_days': 12,
                                                'score': 97, 'maximum': 100, 'calibrated': False}
    assert report['claims'][1]['freshness'] == {'state': 'none', 'newest_source_date': None, 'age_days': None,
                                                'score': None, 'maximum': 100, 'calibrated': False}
    html = render_html(report)
    assert 'The model found a contradiction in a retained source quote.' in html
    assert 'The search returned no usable results from the approved domains.' in html
    assert 'Category (model-labelled): pricing' in html
    assert 'Freshness of the quoted evidence: current (score 97/100, newest quoted source 2026-10-20, 12 day(s)' in html
    assert 'Freshness of the quoted evidence: none (not scored)' in html
    glance = html.split('<h2>At a glance</h2>')[1].split('</table>')[0]
    assert '<a href="#claim-1">claim-1</a>' in glance and 'id="claim-1"' in html
    assert '>Potentially changed</td>' in glance and '>97</td>' in glance and '>not scored</td>' in glance
    assert html.index('At a glance') < html.index('Full original answer')


@pytest.mark.parametrize('published,state,age,score', [
    ('2026-11-01', 'current', 0, 100), ('2026-10-31T17:00:00+00:00', 'current', 1, 100), ('2026-08-03', 'current', 90, 75),
    ('2026-08-02', 'recent', 91, 75), ('2025-11-01', 'recent', 365, 0), ('2025-10-31', 'dated', 366, 0)])
def test_freshness_score_follows_the_age_of_the_newest_quoted_source(published, state, age, score):
    result = _freshness([{'source_id': 's1'}], {'s1': {'published_at': published}}, date(2026, 11, 1))
    assert (result['state'], result['age_days'], result['score']) == (state, age, score)


def test_freshness_ignores_undated_and_future_sources_and_uses_the_newest_date():
    verification = date(2026, 11, 1)
    quotes = [{'source_id': 'a'}, {'source_id': 'b'}, {'source_id': 'c'}, {'source_id': 'd'}]
    sources = {'a': {'published_at': None}, 'b': {'published_at': '2099-01-01'}, 'c': {'published_at': '2025-01-01'},
               'd': {'published_at': '2026-09-01'}}
    result = _freshness(quotes, sources, verification)
    assert (result['newest_source_date'], result['age_days'], result['score']) == ('2026-09-01', 61, 83)
    undated = _freshness(quotes[:2], sources, verification)
    assert (undated['state'], undated['score']) == ('undated', None)
    assert _freshness([], sources, verification)['state'] == 'none'


@pytest.mark.parametrize('approve', [None, lambda request: False, lambda request: 'yes'])
def test_unapproved_queries_never_leave(approve):
    transcript, report, gt, st = scenario(approve=approve)
    assert len(gt.calls) == 1 and not st.calls
    assert transcript['stop_reason'] == 'approval_declined'
    assert all(c['reason_code'] == 'APPROVAL_DECLINED' for c in report['claims'])


def test_eof_declines_and_callback_cannot_mutate_approval():
    def eof(request):
        raise EOFError()
    assert scenario(approve=eof)[0]['stop_reason'] == 'approval_declined'
    def mutate(request):
        request['queries'].clear()
        return True
    assert scenario(approve=mutate)[0]['approval']['queries'] == ['acme price', 'acme version']


@pytest.mark.parametrize('ceiling,attempts', [(1, 1), (2, 2), (3, 3)])
def test_attempt_ceiling_is_shared_and_stops(ceiling, attempts):
    transcript, report, gt, st = scenario(max_attempts=ceiling)
    assert len(transcript['attempts']) == attempts
    assert transcript['stop_reason'] == 'journey_limit'
    assert len(gt.calls) + len(st.calls) == attempts


def test_unretainable_search_link_does_not_abort_remaining_claims():
    first = hits(("https://docs.example.com/page?view=1", "unretainable", "2026-10-20"),
                 (DOCS, PRICE_PAGE, "2026-10-20"))
    transcript, report, gateway, search = scenario(searches=[first, hits()])
    assert transcript["stop_reason"] is None
    assert len(search.calls) == 2 and len(gateway.calls) == 2
    assert report["claims"][0]["verdict"] == "POTENTIALLY_CHANGED"
    assert report["claims"][1]["verdict"] == "UNVERIFIABLE"


def test_gateway_refusal_and_search_failure_are_terminal():
    transcript, report, gt, st = scenario(assessments=[refusal()])
    assert transcript['stop_reason'] == 'gateway_failed' and len(st.calls) == 1
    assert report['usage']['tokens']['complete'] is False
    transcript, report, gt, st = scenario(searches=[{'path': '/search', 'status': 429, 'body': {}}])
    assert transcript['stop_reason'] == 'search_failed' and len(gt.calls) == len(st.calls) == 1
    assert report['usage']['credits']['complete'] is False


@pytest.mark.parametrize('payload', ['garbage', {'claims': [{'quote': PRICE, 'category': [], 'time_sensitivity': {}, 'reason': 'x', 'query': 'q'}]}])
def test_malformed_extraction_does_not_spend_search(payload):
    from test_stale_check import completion
    transcript, report, gt, st = scenario(extraction=completion(payload))
    assert not report['claims'] and not st.calls
    assert 'At a glance' not in render_html(report)


def test_fabricated_quote_and_future_dates_do_not_establish_truth():
    _, report, _, _ = scenario(assessments=[verdict('CHANGED', ('claim-1-src-1', 'contradicts', 'invented quote'))])
    assert report['claims'][0]['verdict'] == 'UNVERIFIABLE'
    _, report, _, _ = scenario(searches=[hits((DOCS, PRICE_PAGE, '2099-01-01')), hits()])
    strength = report['claims'][0]['evidence_strength']
    assert strength['components']['future_date_present'] is True and strength['calibrated'] is False
    assert strength['heuristic_score'] == 2
    assert report['claims'][0]['freshness']['state'] == 'undated' and report['claims'][0]['freshness']['score'] is None


@pytest.mark.parametrize('mutate', [
    lambda t: t['approval']['queries'].append('unapproved query'),
    lambda t: t['approval'].__setitem__('approved', False),
    lambda t: t['attempts'].pop(),
    lambda t: t['attempts'][0].__setitem__('service', 'search'),
    lambda t: t['attempts'][0]['usage'].__setitem__('total_tokens', 999),
    lambda t: t.__setitem__('stop_reason', 'gateway_failed'),
    lambda t: t['checks'][0]['search']['sources'][0].__setitem__('content', 'different'),
])
def test_coherent_input_tampering_rejected(mutate):
    transcript = scenario()[0]
    mutate(transcript)
    with pytest.raises(ValidationError):
        make_report_v4(transcript, 'fd-test')


def test_html_escapes_original_answer_and_model_prose():
    report = scenario()[1]
    report['answer_text'] = '<script>alert(1)</script>'
    report['claims'][0]['inference']['rationale'] = '<img src=x onerror=alert(1)>'
    html = render_html(report)
    assert '<script>' not in html and '<img' not in html
    assert '&lt;script&gt;' in html and "default-src 'none'" in html


def test_cli_validates_output_before_credentials(tmp_path, monkeypatch, capsys):
    answer = tmp_path / 'answer.txt'
    answer.write_text(ANSWER)
    existing = tmp_path / 'existing'
    existing.mkdir()
    monkeypatch.setattr('stalecheck.cli._env', lambda name: pytest.fail('credential lookup before validation'))
    rc = main(['stale-check', '--answer', str(answer), '--live', '--confirm-outbound', '--model', 'openai/test',
               '--primary-domain', 'example.com', '--output', str(existing)])
    assert rc == 2 and 'live output must be new' in capsys.readouterr().err


def test_checkpoints_preserve_research_before_assessment():
    snapshots = []
    transcript = scenario(checkpoint=snapshots.append)[0]
    assert snapshots[0]['extraction'] is None
    assert any(t['checks'] and t['checks'][0]['assessment'] == {'status': 'skipped', 'reason': 'pending'} for t in snapshots)
    assert snapshots[-1] == transcript


def test_deadline_race_before_gateway_begin_is_not_billed():
    from stalecheck.journey import JourneyBudget
    clock = iter([0, 0, 181, 181])
    budget = JourneyBudget(monotonic=lambda: next(clock, 181))
    transcript, report, gt, st = scenario(budget=budget)
    assert transcript['stop_reason'] == 'journey_limit'
    assert report['usage']['attempts'] == 0 and not gt.calls and not st.calls


def test_oversized_assessment_is_preflight_failure_not_a_billed_call():
    rows = [(f'https://docs.example.com/page{i}', '\x00' * 8000, '2026-10-20') for i in range(3)]
    transcript, report, gt, st = scenario(searches=[hits(*rows)])
    assert transcript['stop_reason'] == 'preflight_failed'
    assert report['usage']['attempts'] == 2 and len(gt.calls) == len(st.calls) == 1


def test_interrupted_gateway_attempt_survives_checkpoint():
    snapshots = []
    class Interrupted(MockGatewayTransport):
        def __call__(self, *args, **kwargs):
            raise KeyboardInterrupt()
    gateway = GatewayClient('http://localhost:8080', 'openai/test', transport=Interrupted([]))
    with pytest.raises(KeyboardInterrupt):
        collect_transcript(ANSWER, verification_date='2026-11-01', primary_domains=['example.com'], gateway=gateway,
                           search_transport=MockTransport([]), checkpoint=snapshots.append)
    assert snapshots[-1]['stop_reason'] == 'interrupted_or_failed'
    assert snapshots[-1]['attempts'] == [{'service': 'gateway', 'status': 'failed', 'usage': None, 'routing': None}]


def test_unencodable_source_metadata_is_discarded_with_accounting():
    from stalecheck.acquisition import Response
    class Surrogate(MockTransport):
        def __call__(self, *args, **kwargs):
            return Response(200, json.dumps({'results': [{'url': DOCS, 'title': '\ud800', 'content': PRICE_PAGE}], 'usage': {'credits': 1}}).encode())
    gt = MockGatewayTransport([claims(proposal(PRICE))])
    gateway = GatewayClient('http://localhost:8080', 'openai/test', transport=gt)
    transcript = collect_transcript(ANSWER, verification_date='2026-11-01', primary_domains=['example.com'], gateway=gateway,
                                    search_transport=Surrogate([]), approve=lambda request: True)
    assert transcript['stop_reason'] == 'search_failed'
    assert make_report_v4(transcript, 'fd-test')['usage']['credits']['known_subtotal'] == {'credits': 1}
    json_bytes(transcript)


@pytest.mark.parametrize('mutate', [
    lambda t: t['limits'].__setitem__('max_seconds', 10**400),
    lambda t: t['attempts'][1]['usage'].__setitem__('credits', 10**400),
    lambda t: t['attempts'][0]['routing'].__setitem__('is_fallback', 0),
])
def test_extreme_numbers_and_bool_integer_confusion_fail_cleanly(mutate):
    transcript = scenario()[0]
    mutate(transcript)
    with pytest.raises(ValidationError):
        make_report_v4(transcript, 'fd-test')


@pytest.mark.parametrize('quote', [' ', '.', 'a', 'per month'])
def test_trivial_quotes_never_promote_a_verdict(quote):
    for relation, kind in (('contradicts', 'CHANGED'), ('supports', 'SUPPORTED')):
        _, report, _, _ = scenario(assessments=[verdict(kind, ('claim-1-src-1', relation, quote))])
        claim = report['claims'][0]
        assert claim['verdict'] == 'UNVERIFIABLE' and claim['evidence'] == []
        assert {'stage': 'assessment', 'reference': 'claim-1', 'reason': 'quote_too_short'} in report['rejected_proposals']


def test_trivial_claim_spans_are_rejected_before_any_call():
    _, report, gt, st = scenario(extraction=claims(proposal(' ', 'acme blank'), proposal('$19', 'acme figure'), proposal(PRICE, 'acme price')),
                                 searches=[hits((DOCS, PRICE_PAGE, '2026-10-20'))])
    assert [c['text'] for c in report['claims']] == [PRICE]
    assert [r['reason'] for r in report['rejected_proposals'][:2]] == ['quote_too_short', 'quote_too_short']
    assert len(st.calls) == 1


def test_retrieved_sources_are_linked_even_without_a_quote():
    report = scenario(assessments=[verdict('UNVERIFIABLE')])[1]
    claim = report['claims'][0]
    assert claim['verdict'] == 'UNVERIFIABLE' and claim['evidence'] == []
    html = render_html(report)
    assert f'href="{DOCS}"' in html and 'not evidence unless quoted above' in html


@pytest.mark.parametrize('raw,expected', [('2026-10-01T10:00:00Z', '2026-10-01T10:00:00+00:00'),
                                          ('2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30'),
                                          ('2026-10-01T10:00:00', '2026-10-01'), ('', None)])
def test_iso_publication_timestamps_are_retained(raw, expected):
    transcript, report, _, _ = scenario(searches=[hits((DOCS, PRICE_PAGE, raw)), hits()])
    assert report['evidence'][0]['published_at'] == expected
    assert report['claims'][0]['evidence_strength']['components']['dated_urls'] == (expected is not None)


def test_transcript_replay_rejects_non_stale_input_before_writing(tmp_path, capsys):
    legacy = tmp_path / 'v1.json'
    legacy.write_text(json.dumps({'schema_version': '1.0'}))
    out = tmp_path / 'out'
    assert main(['stale-check', '--transcript', str(legacy), '--output', str(out)]) == 2
    assert not out.exists() and 'schema 3.0/4.0' in capsys.readouterr().err
