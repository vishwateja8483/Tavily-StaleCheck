"""Responses shaped like the Bifrost documentation and the Tavily API reference flow through the v4 journey; no live calls."""
import json

from stalecheck.acquisition import MockTransport
from stalecheck.gateway import GatewayClient, MockGatewayTransport
from stalecheck.stale_check_v4 import collect_transcript, make_report_v4
from test_stale_check import ANSWER, DOCS, NOW, PRICE, PRICE_PAGE


def bifrost_reply(payload):
    """OpenAI-style body plus the extra_fields Bifrost documents for a request answered by a fallback."""
    return {'status': 200, 'body': {
        'id': 'chatcmpl-123', 'object': 'chat.completion', 'created': 1790000000, 'model': 'claude-3-5-sonnet-20241022',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(payload), 'refusal': None}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 150, 'total_tokens': 162},
        'extra_fields': {'request_type': 'chat_completion', 'provider': 'anthropic', 'model_requested': 'gpt-4o-mini', 'latency': 1200,
                         'routing_info': {'provider': 'anthropic', 'model': 'claude-3-5-sonnet-20241022', 'is_fallback': True,
                                          'primary_provider': 'openai', 'primary_model': 'gpt-4o-mini'}}}}


def tavily_reply():
    """Search response with the fields the API reference lists, including an RFC 2822 published_date."""
    return {'path': '/search', 'status': 200, 'body': {
        'query': 'acme price', 'answer': None, 'images': [],
        'results': [{'url': DOCS, 'title': 'Acme pricing', 'content': PRICE_PAGE, 'score': 0.81025416, 'raw_content': None,
                     'published_date': 'Tue, 20 Oct 2026 17:00:00 GMT', 'favicon': 'https://docs.example.com/favicon.png',
                     'images': [], 'id': 'a3f9c2-04'}],
        'auto_parameters': {'topic': 'general', 'search_depth': 'basic'}, 'response_time': '1.67', 'usage': {'credits': 1},
        'request_id': '123e4567-e89b-12d3-a456-426614174111'}}


def test_documented_provider_shapes_produce_a_scored_report():
    extraction = {'claims': [{'quote': PRICE, 'category': 'pricing', 'time_sensitivity': 'high', 'reason': 'changes often', 'query': 'acme price'}]}
    assessment = {'verdict': 'CHANGED', 'observed': 'The page lists $24.', 'rationale': 'The quotation contradicts the claim.',
                  'evidence': [{'source_id': 'claim-1-src-1', 'relation': 'contradicts', 'quote': 'The Starter plan costs $24 per month'}],
                  'rewrite': 'Acme Cloud\'s Starter plan costs $24 per month'}
    gateway = GatewayClient('http://127.0.0.1:8080', 'openai/gpt-4o-mini', fallbacks=['anthropic/claude-sonnet-4-5'],
                            transport=MockGatewayTransport([bifrost_reply(extraction), bifrost_reply(assessment)]))
    transcript = collect_transcript(ANSWER, verification_date='2026-11-01', primary_domains=['example.com'], gateway=gateway,
                                    search_transport=MockTransport([tavily_reply()]), now=lambda: NOW, approve=lambda request: True)
    report = make_report_v4(transcript, 'fd-test')
    assert transcript['stop_reason'] is None
    claim = report['claims'][0]
    assert claim['verdict'] == 'POTENTIALLY_CHANGED' and claim['category'] == 'pricing'
    assert claim['freshness'] == {'state': 'current', 'newest_source_date': '2026-10-20', 'age_days': 12, 'score': 97,
                                  'maximum': 100, 'calibrated': False}
    assert report['usage']['routing'] == {'observed_fallbacks': 2, 'unknown_calls': 0}
    assert report['usage']['tokens']['known_subtotal'] == {'prompt_tokens': 24, 'completion_tokens': 300, 'total_tokens': 324}
    assert report['usage']['credits']['known_subtotal'] == {'credits': 1}
