"""Local HTTP and collector lifecycle checks; no live provider calls."""
from copy import deepcopy
import http.client
import json
import socket
import threading
import time

import pytest

from stalecheck.contracts import decode
from stalecheck.web_jobs import DEMO_INPUT, JobManager, ServiceError, demo_transports
from stalecheck.web_service import AssessmentServer
from test_gateway_relay import KEY as VIRTUAL_KEY, bifrost_stand_in, replies
from test_stale_check import ANSWER, DOCS, PRICE, PRICE_PAGE, claims, hits, proposal, verdict

KEY = 'test-idempotency-key-01'


def wait(manager, identifier, states):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = manager.get(identifier).snapshot()
        if value['state'] in states:
            return value
        time.sleep(.01)
    pytest.fail('job did not reach expected state')


@pytest.fixture
def manager(tmp_path):
    value = JobManager(tmp_path / 'jobs')
    yield value
    value.close()
    if value.worker:
        value.worker.join(5)


def test_approval_polling_and_idempotency_do_not_repeat_extraction(manager):
    transports = demo_transports()
    manager.transport_factory = lambda: transports
    first, created = manager.create(KEY, DEMO_INPUT)
    assert created
    pending = wait(manager, first['id'], {'awaiting_approval'})
    for _ in range(5):
        assert manager.create(KEY, DEMO_INPUT)[0]['id'] == first['id']
        manager.get(first['id']).snapshot()
    assert len(transports['gateway'].transport.calls) == 1
    assert not transports['search_transport'].calls
    with pytest.raises(ServiceError, match='another assessment'):
        manager.create(KEY + '2', DEMO_INPUT)
    with pytest.raises(ServiceError, match='input conflict'):
        manager.create(KEY, {**DEMO_INPUT, 'max_attempts': 2})
    wrong = {**pending['approval'], 'queries': ['unapproved query']}
    with pytest.raises(ServiceError, match='does not match'):
        manager.approve(first['id'], wrong, True)
    manager.approve(first['id'], pending['approval'], True)
    manager.approve(first['id'], pending['approval'], True)
    with pytest.raises(ServiceError, match='conflicting'):
        manager.approve(first['id'], pending['approval'], False)
    complete = wait(manager, first['id'], {'completed'})
    assert complete['attempts'] == 3 and complete['stop_reason'] is None
    assert len(transports['gateway'].transport.calls) == 2
    assert len(transports['search_transport'].calls) == 1
    report = decode(manager.artifact(first['id'], 'report.json'))
    assert report['claims'][0]['verdict'] == 'POTENTIALLY_CHANGED'
    assert report['usage']['credits']['scope'] == 'mock_observations_not_spending'
    assert b'Full original answer' in manager.artifact(first['id'], 'report.html')


def test_decline_has_no_search_and_preserves_verified_report(manager):
    transports = demo_transports()
    manager.transport_factory = lambda: transports
    job, _ = manager.create(KEY, DEMO_INPUT)
    pending = wait(manager, job['id'], {'awaiting_approval'})
    manager.approve(job['id'], pending['approval'], False)
    stopped = wait(manager, job['id'], {'declined'})
    assert stopped['stop_reason'] == 'approval_declined'
    assert not transports['search_transport'].calls
    assert decode(manager.artifact(job['id'], 'input.json'))['approval']['approved'] is False


def test_cancel_pending_retains_failure_checkpoint(manager):
    job, _ = manager.create(KEY, DEMO_INPUT)
    wait(manager, job['id'], {'awaiting_approval'})
    manager.cancel(job['id'])
    wait(manager, job['id'], {'cancelled'})
    root = manager.get(job['id']).directory
    assert (root / 'failure.json').is_file()
    assert (root / 'pending-approval.json').is_file()
    latest = sorted(root.glob('checkpoint-*.json'))[-1]
    assert decode(latest.read_bytes())['stop_reason'] == 'interrupted_or_failed'
    with pytest.raises(ServiceError, match='not available'):
        manager.artifact(job['id'], 'report.json')


def test_expiry_does_not_reset_deadline(manager):
    job, _ = manager.create(KEY, {**DEMO_INPUT, 'max_seconds': .15})
    pending = wait(manager, job['id'], {'awaiting_approval'})
    wait(manager, job['id'], {'expired'})
    with pytest.raises(ServiceError, match='no longer pending'):
        manager.approve(job['id'], pending['approval'], True)
    assert (manager.get(job['id']).directory / 'failure.json').is_file()


@pytest.mark.parametrize('stage', ['extraction', 'search', 'assessment', 'transport_failure'])
def test_expiry_during_provider_work_never_publishes_report(manager, monkeypatch, stage):
    from stalecheck import web_jobs
    from stalecheck.web_jobs import ServiceBudget

    clock = [0.0]
    monkeypatch.setattr(web_jobs, 'ServiceBudget',
                        lambda cancelled, **kwargs: ServiceBudget(cancelled, monotonic=lambda: clock[0], **kwargs))
    transports = demo_transports()
    target = transports['search_transport'] if stage == 'search' else transports['gateway'].transport
    original = type(target).__call__
    calls = [0]

    def delayed(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        calls[0] += 1
        if calls[0] == (2 if stage == 'assessment' else 1):
            clock[0] = 181.0
            if stage == 'transport_failure':
                raise RuntimeError('private provider failure')
        return response

    monkeypatch.setattr(type(target), '__call__', delayed)
    manager.transport_factory = lambda: transports
    job, _ = manager.create(KEY, DEMO_INPUT)
    if stage in ('search', 'assessment'):
        pending = wait(manager, job['id'], {'awaiting_approval'})
        manager.approve(job['id'], pending['approval'], True)
    expired = wait(manager, job['id'], {'expired'})
    assert expired['artifacts'] == []
    root = manager.get(job['id']).directory
    assert (root / 'failure.json').is_file()
    assert not (root / 'completion.json').exists()
    assert not (root / 'run').exists()
    latest = decode(sorted(root.glob('checkpoint-*.json'))[-1].read_bytes())
    assert latest['attempts']
    with pytest.raises(ServiceError, match='not available'):
        manager.artifact(job['id'], 'report.json')


def test_cancel_inflight_keeps_slot_and_unknown_usage(manager):
    from stalecheck.gateway import MockGatewayTransport, GatewayClient
    entered, release = threading.Event(), threading.Event()
    class Blocking(MockGatewayTransport):
        def __call__(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            raise RuntimeError('secret provider failure')
    manager.transport_factory = lambda: {'gateway': GatewayClient('http://127.0.0.1:8080', 'openai/demo', transport=Blocking([]))}
    job, _ = manager.create(KEY, DEMO_INPUT)
    assert entered.wait(5)
    try:
        assert manager.cancel(job['id'])['state'] == 'cancel_requested'
        with pytest.raises(ServiceError, match='another assessment'):
            manager.create(KEY + '2', DEMO_INPUT)
    finally:
        release.set()
    wait(manager, job['id'], {'cancelled'})
    root = manager.get(job['id']).directory
    latest = decode(sorted(root.glob('checkpoint-*.json'))[-1].read_bytes())
    assert len(latest['attempts']) == 1 and latest['attempts'][0]['usage'] is None
    assert 'secret provider failure' not in ''.join(p.read_text() for p in root.glob('*.json'))


def test_capacity_and_restart_never_resume(tmp_path):
    manager = JobManager(tmp_path / 'launch', max_sessions=1)
    job, _ = manager.create(KEY, DEMO_INPUT)
    wait(manager, job['id'], {'awaiting_approval'})
    manager.close()
    manager.worker.join(5)
    assert manager.get(job['id']).snapshot()['state'] == 'cancelled'
    with pytest.raises(FileExistsError):
        JobManager(tmp_path / 'launch')
    fresh = JobManager(tmp_path / 'fresh', max_sessions=1)
    try:
        with pytest.raises(ServiceError, match='unknown job'):
            fresh.get(job['id'])
        job2, _ = fresh.create(KEY, DEMO_INPUT)
        pending = wait(fresh, job2['id'], {'awaiting_approval'})
        fresh.approve(job2['id'], pending['approval'], False)
        fresh.worker.join(5)
        with pytest.raises(ServiceError) as caught:
            fresh.create(KEY + '2', DEMO_INPUT)
        assert caught.value.status == 429
        assert fresh.create(KEY, DEMO_INPUT)[1] is False
    finally:
        fresh.close()


def test_unconfigured_live_disabled_and_never_exposes_values(tmp_path):
    manager = JobManager(tmp_path / 'live', live=True, configuration={'BIFROST_VIRTUAL_KEY': 'private-value'})
    try:
        assert not manager.capabilities()['configured']
        assert 'private-value' not in json.dumps(manager.capabilities())
        with pytest.raises(ServiceError, match='configuration required'):
            manager.create(KEY, DEMO_INPUT)
        assert not manager.jobs
    finally:
        manager.close()


LIVE = {'BIFROST_URL': 'http://127.0.0.1:8080', 'BIFROST_MODEL': 'ollama/qwen2.5:3b', 'BIFROST_VIRTUAL_KEY': VIRTUAL_KEY}


def test_live_relay_configuration_keeps_the_tavily_key_out_of_the_service(tmp_path):
    from stalecheck.gateway import BifrostRelayTransport
    manager = JobManager(tmp_path / 'relay', live=True, configuration={**LIVE, 'TAVILY_API_KEY': 'tavily-secret-value', 'BIFROST_SEARCH_RELAY': 'tavily'})
    try:
        capabilities = manager.capabilities()
        assert capabilities['configured'] is True
        assert capabilities['configuration'] == {'BIFROST_URL': True, 'BIFROST_MODEL': True, 'BIFROST_VIRTUAL_KEY': True,
                                                 'TAVILY_API_KEY': False, 'BIFROST_SEARCH_RELAY': True}
        transports = manager._live_transports()
        assert isinstance(transports['search_transport'], BifrostRelayTransport) and transports['search_key'] == VIRTUAL_KEY
        assert 'tavily-secret-value' not in json.dumps(capabilities) + repr(manager.configuration)
    finally:
        manager.close()


def test_live_search_needs_either_a_tavily_key_or_a_valid_relay_name(tmp_path):
    managers = {name: JobManager(tmp_path / name, live=True, configuration=settings) for name, settings in {
        'neither': LIVE, 'direct': {**LIVE, 'TAVILY_API_KEY': 'tavily-secret-value'},
        'bad_relay': {**LIVE, 'BIFROST_SEARCH_RELAY': 'not a relay'}, 'bad_key': {**LIVE, 'BIFROST_VIRTUAL_KEY': 'plain', 'BIFROST_SEARCH_RELAY': 'tavily'}}.items()}
    try:
        assert {name: item.capabilities()['configured'] for name, item in managers.items()} == {
            'neither': False, 'direct': True, 'bad_relay': False, 'bad_key': False}
        assert 'search_transport' not in managers['direct']._live_transports()
    finally:
        for item in managers.values():
            item.close()


def test_live_job_searches_through_the_relay_with_the_virtual_key_only(tmp_path):
    chat = replies(claims(proposal(PRICE, 'acme starter price')),
                   verdict('CHANGED', ('claim-1-src-1', 'contradicts', 'The Starter plan costs $24 per month')))
    relay = replies(hits((DOCS, PRICE_PAGE, '2026-10-20'), credits=1))
    with bifrost_stand_in(chat, relay) as (port, seen):
        manager = JobManager(tmp_path / 'live', live=True, configuration={
            **LIVE, 'BIFROST_URL': f'http://127.0.0.1:{port}', 'TAVILY_API_KEY': 'tavily-secret-value', 'BIFROST_SEARCH_RELAY': 'tavily'})
        try:
            job, _ = manager.create(KEY, {**DEMO_INPUT, 'answer_text': ANSWER, 'verification_date': '2026-11-01', 'max_claims': 1})
            pending = wait(manager, job['id'], {'awaiting_approval'})
            manager.approve(job['id'], pending['approval'], True)
            assert wait(manager, job['id'], {'completed'})['stop_reason'] is None
            report = decode(manager.artifact(job['id'], 'report.json'))
        finally:
            manager.close()
            if manager.worker:
                manager.worker.join(5)
    assert [request['path'] for request in seen] == ['/v1/chat/completions', '/relay/tavily/search', '/v1/chat/completions']
    assert all(request['headers']['x-bf-vk'] == VIRTUAL_KEY and 'authorization' not in request['headers'] for request in seen)
    assert b'tavily-secret-value' not in b''.join(request['body'] for request in seen)
    assert report['claims'][0]['verdict'] == 'POTENTIALLY_CHANGED'
    assert report['usage']['provenance'] == {'gateway': 'bifrost', 'search': 'tavily'} and report['usage']['search_calls'] == 1


@pytest.fixture
def service(manager, tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<!doctype html><title>Assessment</title>')
    server = AssessmentServer(manager, static)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(5)


def request(service, method='GET', path='/api/capabilities', body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', service.server_port, timeout=5)
    merged = {'X-Assessment-Token': service.token}
    if method == 'POST':
        merged.update({'Origin': service.origin, 'Content-Type': 'application/json'})
    merged.update(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body)
    connection.request(method, path, body, headers=merged)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_http_create_approve_download(service):
    status, headers, raw = request(service)
    assert status == 200 and decode(raw)['mode'] == 'demo'
    assert headers['Cache-Control'] == 'no-store'
    assert 'Access-Control-Allow-Origin' not in headers
    status, _, raw = request(service, 'POST', '/api/jobs', {'idempotency_key': KEY, 'input': DEMO_INPUT})
    assert status == 201
    job = decode(raw)
    pending = wait(service.manager, job['id'], {'awaiting_approval'})
    path = '/api/jobs/' + job['id']
    assert request(service, 'POST', path + '/approval', {'request': pending['approval'], 'approved': True})[0] == 200
    wait(service.manager, job['id'], {'completed'})
    assert request(service, path=path + '/report')[0] == 200
    for name in ('report.html', 'report.json', 'input.json', 'manifest.json'):
        status, headers, raw = request(service, path=path + '/artifacts/' + name)
        assert status == 200 and raw
        assert 'attachment' in headers['Content-Disposition']
        assert request(service, path=path + '/artifacts/' + name, headers={'X-Assessment-Token': ''})[0] == 401
    assert request(service, path=path + '/artifacts/session.json')[0] == 404


@pytest.mark.parametrize('headers,status', [
    ({'X-Assessment-Token': ''}, 401), ({'Host': 'evil.example'}, 403),
    ({'Sec-Fetch-Site': 'cross-site'}, 403), ({'Origin': 'http://evil.example'}, 403),
    ({'Content-Type': 'text/plain'}, 415), ({'Transfer-Encoding': 'chunked'}, 400),
])
def test_mutation_boundary(service, headers, status):
    assert request(service, 'POST', '/api/jobs', {'idempotency_key': KEY, 'input': DEMO_INPUT}, headers)[0] == status
    assert not service.manager.jobs


@pytest.mark.parametrize('body', ['{"x":1,"x":2}', '[]', '{', '{"x":NaN}', '{"idempotency_key":"x","input":{},"extra":1}'])
def test_malformed_json_rejected(service, body):
    assert request(service, 'POST', '/api/jobs', body)[0] == 400
    assert not service.manager.jobs


@pytest.mark.parametrize('path', ['/../secret', '/%2e%2e/secret', '/api/capabilities?token=x', '//evil.example', '/assets/../index.html'])
def test_no_path_or_query_authority(service, path):
    assert request(service, path=path)[0] == 404


def test_duplicate_authority_headers_and_preflight(service):
    with socket.create_connection(('127.0.0.1', service.server_port), timeout=5) as sock:
        sock.sendall(f'GET / HTTP/1.1\r\nHost: {service.host}\r\nHost: evil.example\r\n\r\n'.encode())
        assert b'400' in sock.recv(1024).split(b'\r\n')[0]
    assert request(service, 'OPTIONS')[0] == 405
    assert request(service, 'POST', '/api/jobs', 'x' * 100001)[0] == 413


def test_artifact_tampering_is_not_presented(service):
    job, _ = service.manager.create(KEY, DEMO_INPUT)
    pending = wait(service.manager, job['id'], {'awaiting_approval'})
    service.manager.approve(job['id'], pending['approval'], True)
    wait(service.manager, job['id'], {'completed'})
    (service.manager.get(job['id']).directory / 'run' / 'report.json').write_text('{}')
    assert request(service, path='/api/jobs/' + job['id'] + '/report')[0] == 400


def test_provider_refusal_never_retries_or_falls_back(manager):
    from stalecheck.gateway import GatewayClient, MockGatewayTransport
    transport = MockGatewayTransport([{'status': 402, 'body': {'error': {'type': 'budget_exceeded', 'message': 'PRIVATE'}}}])
    manager.transport_factory = lambda: {'gateway': GatewayClient('http://127.0.0.1:8080', 'openai/demo', transport=transport)}
    job, _ = manager.create(KEY, DEMO_INPUT)
    stopped = wait(manager, job['id'], {'partial'})
    assert stopped['stop_reason'] == 'gateway_failed'
    assert len(transport.calls) == stopped['attempts'] == 1
    report = decode(manager.artifact(job['id'], 'report.json'))
    assert report['usage']['tokens']['complete'] is False
    assert 'PRIVATE' not in json.dumps(report)


def test_static_symlinks_are_not_served(service, tmp_path):
    target = tmp_path / 'outside.html'
    target.write_text('PRIVATE')
    index = service.static_root / 'index.html'
    try:
        index.unlink()
        index.symlink_to(target)
    except OSError:
        pytest.skip('OS does not permit symlink creation')
    assert request(service, path='/')[0] == 400


def test_bounded_handlers_reject_excess_connections(service):
    # Reserve all server-owned slots without sending slow traffic.
    for _ in range(8):
        assert service.slots.acquire(blocking=False)
    try:
        with socket.create_connection(('127.0.0.1', service.server_port), timeout=2) as sock:
            try:
                sock.sendall(f'GET / HTTP/1.0\r\nHost: {service.host}\r\n\r\n'.encode())
                assert sock.recv(1024) == b''
            except ConnectionError:
                pass
    finally:
        for _ in range(8):
            service.slots.release()
    assert request(service)[0] == 200


def test_bad_consent_and_limits_reserve_nothing(manager):
    for change in ({'consent': False}, {'max_claims': True}, {'max_seconds': float('inf')},
                   {'answer_text': '\ud800'}, {'primary_domains': ['http://localhost']}, {'unexpected': 1}):
        with pytest.raises(ServiceError):
            manager.create(KEY, {**DEMO_INPUT, **change})
    assert not manager.jobs and not manager.keys


def test_approval_answer_leaves_awaiting_state_and_terminal_clock_is_frozen(manager):
    manager.transport_factory = demo_transports
    job, _ = manager.create(KEY, DEMO_INPUT)
    pending = wait(manager, job['id'], {'awaiting_approval'})
    assert manager.approve(job['id'], pending['approval'], True)['state'] != 'awaiting_approval'
    complete = wait(manager, job['id'], {'completed'})
    time.sleep(1.1)
    assert complete['remaining_seconds'] == manager.get(job['id']).snapshot()['remaining_seconds'] == 0


def test_declined_job_never_reports_research(manager):
    manager.transport_factory = demo_transports
    job, _ = manager.create(KEY, DEMO_INPUT)
    pending = wait(manager, job['id'], {'awaiting_approval'})
    seen = {manager.approve(job['id'], pending['approval'], False)['state']}
    deadline = time.monotonic() + 5
    while 'declined' not in seen and time.monotonic() < deadline:
        seen.add(manager.get(job['id']).snapshot()['state'])
    assert 'declined' in seen and 'researching' not in seen


def test_unexpected_storage_failure_returns_generic_json_error(service, monkeypatch):
    def broken(*args, **kwargs):
        raise PermissionError('C:/private/path denied')
    monkeypatch.setattr(service.manager, 'create', broken)
    status, _, raw = request(service, 'POST', '/api/jobs', {'idempotency_key': KEY, 'input': DEMO_INPUT})
    assert status == 500 and decode(raw) == {'error': 'internal service error'}
