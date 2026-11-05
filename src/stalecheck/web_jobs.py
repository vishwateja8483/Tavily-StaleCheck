"""Bounded launch-local jobs; one collector invocation survives human approval."""
from copy import deepcopy
from pathlib import Path
import re
import secrets
import threading

from .contracts import ValidationError, json_bytes, require, safe_path
from .export import write_exclusive_json
from .gateway import BifrostRelayTransport, GatewayClient, MockGatewayTransport, canonical
from .acquisition import MockTransport
from .journey import JourneyBudget
from .report import write_run
from .stale_check_v4 import collect_transcript, render_html, validate_inputs
from .verify_artifacts import load_verified_run

SETTINGS = ('BIFROST_URL', 'BIFROST_MODEL', 'BIFROST_VIRTUAL_KEY', 'TAVILY_API_KEY', 'BIFROST_SEARCH_RELAY')
ARTIFACTS = ('report.html', 'report.json', 'input.json', 'manifest.json')
TERMINAL = {'completed', 'partial', 'declined', 'expired', 'cancelled', 'failed'}
DEMO_INPUT = {
    'answer_text': "Acme Cloud's Starter plan costs $19 per month.",
    'verification_date': '2026-11-01', 'answer_date': None,
    'primary_domains': ['example.com'], 'max_claims': 5,
    'max_attempts': 25, 'max_seconds': 180, 'consent': True,
}


class ServiceError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Interrupted(Exception):
    """Not ValidationError: do not turn cancellation into a provider failure."""


class ServiceBudget(JourneyBudget):
    def __init__(self, cancelled, **kwargs):
        super().__init__(**kwargs)
        self.cancelled = cancelled

    def check(self):
        if self.cancelled.is_set():
            raise Interrupted('cancelled')
        if self.remaining_seconds() <= 0:
            raise Interrupted('expired')


def demo_transports():
    def completion(payload):
        return {'status': 200, 'body': {
            'model': 'demo', 'choices': [{'message': {'content': canonical(payload).decode()}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}}
    gateway = MockGatewayTransport([
        completion({'claims': [{'quote': DEMO_INPUT['answer_text'][:-1], 'category': 'pricing',
                               'time_sensitivity': 'high', 'reason': 'Synthetic changing price', 'query': 'Acme Starter pricing'}]}),
        completion({'verdict': 'CHANGED', 'observed': 'Synthetic current price is $24.',
                    'evidence': [{'source_id': 'claim-1-src-1', 'relation': 'contradicts',
                                  'quote': 'The Starter plan costs $24 per month.'}],
                    'rationale': 'Scripted example, not live evidence.', 'rewrite': 'The Starter plan costs $24 per month.'}),
    ])
    search = MockTransport([{'path': '/search', 'status': 200, 'body': {
        'results': [{'url': 'https://docs.example.com/pricing', 'title': 'Synthetic pricing',
                     'content': 'The Starter plan costs $24 per month.', 'published_date': '2026-10-20', 'score': 0.9}],
        'usage': {'credits': 1}}}])
    return {'gateway': GatewayClient('http://127.0.0.1:8080', 'openai/demo', transport=gateway),
            'search_transport': search, 'now': lambda: '2026-11-01T12:00:00+00:00'}


def validated_input(value):
    if not isinstance(value, dict) or set(value) != set(DEMO_INPUT):
        raise ServiceError(400, 'invalid input fields')
    if value['consent'] is not True:
        raise ServiceError(400, 'full-answer upload consent required')
    try:
        validate_inputs(value['answer_text'], value['verification_date'], value['answer_date'],
                        value['primary_domains'], value['max_claims'], value['max_attempts'], value['max_seconds'])
        canonical(value)  # Reject unencodable text before reserving work.
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ServiceError(400, 'invalid assessment input') from None
    return deepcopy(value)


class Job:
    def __init__(self, identifier, value, directory, mode):
        self.id, self.input, self.directory, self.mode = identifier, value, directory, mode
        self.condition = threading.Condition(threading.RLock())
        self.cancelled = threading.Event()
        self.state, self.reason = 'extracting', None
        self.approval = self.decision = self.budget = None
        self.sequence, self.attempts = 0, 0
        self.has_report = False
        self.final_seconds = None

    def snapshot(self):
        with self.condition:
            return {'id': self.id, 'mode': self.mode, 'state': self.state,
                    'remaining_seconds': self.final_seconds if self.final_seconds is not None else self.budget.remaining_seconds() if self.budget else 0,
                    'approval': deepcopy(self.approval), 'stop_reason': self.reason,
                    'attempts': self.attempts, 'artifacts': list(ARTIFACTS) if self.has_report else []}


class JobManager:
    def __init__(self, root, *, live=False, configuration=None, max_sessions=32, transport_factory=None):
        require(type(max_sessions) is int and 1 <= max_sessions <= 128, 'invalid session bound')
        self.root = Path(root).absolute()
        safe_path(self.root.parent, self.root.name)
        require(self.root.parent.is_dir(), 'assessment output parent must exist')
        self.root.mkdir(exist_ok=False)
        self.mode = 'live' if live else 'demo'
        self.configuration = {key: (configuration or {}).get(key) for key in SETTINGS}
        if self.configuration['BIFROST_SEARCH_RELAY']:
            self.configuration['TAVILY_API_KEY'] = None  # searches go through Bifrost, so this process never holds a Tavily key
        self.configured = not live
        c = self.configuration
        if live and all(c[key] for key in SETTINGS[:3]) and (c['TAVILY_API_KEY'] or c['BIFROST_SEARCH_RELAY']):
            try:
                self._live_transports()
                self.configured = True
            except (ValueError, TypeError):
                pass
        self.max_sessions, self.transport_factory = max_sessions, transport_factory
        self.lock = threading.RLock()
        self.jobs, self.keys = {}, {}
        self.active, self.closed = None, False
        self.worker = None

    def _live_transports(self):
        c = self.configuration
        token = c['BIFROST_VIRTUAL_KEY']
        require(isinstance(token, str) and token.startswith('sk-bf-'), 'invalid configuration')
        gateway = GatewayClient(c['BIFROST_URL'], c['BIFROST_MODEL'], token=token, allow_network=True)
        if c['BIFROST_SEARCH_RELAY']:  # the virtual key authorises the relay; the Tavily key stays in the gateway
            return {'gateway': gateway, 'search_key': token, 'allow_network': True,
                    'search_transport': BifrostRelayTransport(c['BIFROST_URL'], c['BIFROST_SEARCH_RELAY'])}
        key = c['TAVILY_API_KEY']
        require(isinstance(key, str) and 0 < len(key) <= 512 and all(33 <= ord(x) <= 126 for x in key), 'invalid configuration')
        return {'gateway': gateway, 'search_key': key, 'allow_network': True}

    def capabilities(self):
        return {'mode': self.mode, 'configured': self.configured,
                'configuration': {key: bool(value) for key, value in self.configuration.items()},
                'max_sessions': self.max_sessions, 'demo_input': deepcopy(DEMO_INPUT) if self.mode == 'demo' else None}

    def get(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise ServiceError(404, 'unknown job')
            return self.jobs[identifier]

    def create(self, key, value):
        if not isinstance(key, str) or re.fullmatch(r'[A-Za-z0-9_-]{16,100}', key) is None:
            raise ServiceError(400, 'invalid idempotency key')
        value = validated_input(value)
        if self.mode == 'demo' and any(value[k] != DEMO_INPUT[k] for k in ('answer_text', 'verification_date', 'answer_date', 'primary_domains')):
            raise ServiceError(400, 'demo requires the supplied synthetic answer, dates and domains')
        encoded = canonical(value)
        with self.lock:
            if key in self.keys:
                previous, identifier = self.keys[key]
                if encoded != previous:
                    raise ServiceError(409, 'idempotency key input conflict')
                return self.jobs[identifier].snapshot(), False
            if self.closed or not self.configured:
                raise ServiceError(409, 'service closed or live configuration required')
            if self.active is not None:
                raise ServiceError(409, 'another assessment is still active')
            if len(self.keys) >= self.max_sessions:
                raise ServiceError(429, 'launch session capacity reached')
            identifier = secrets.token_hex(16)
            directory = safe_path(self.root, identifier)
            directory.mkdir(exist_ok=False)
            write_exclusive_json({'status': 'incomplete', 'mode': self.mode, 'input': value}, directory / 'session.json')
            job = Job(identifier, value, directory, self.mode)
            self.jobs[identifier] = job
            self.keys[key] = (encoded, identifier)
            self.active = identifier
            self.worker = threading.Thread(target=self._run, args=(job,), name='assessment-worker', daemon=True)
            self.worker.start()
            return job.snapshot(), True

    def approve(self, identifier, request, approved):
        job = self.get(identifier)
        if type(approved) is not bool or not isinstance(request, dict):
            raise ServiceError(400, 'invalid approval decision')
        with job.condition:
            if job.approval is None or canonical(request) != canonical(job.approval):
                raise ServiceError(409, 'approval does not match this job')
            if job.decision is not None:
                if job.decision is not approved:
                    raise ServiceError(409, 'conflicting approval decision')
                return job.snapshot()
            if job.state != 'awaiting_approval' or job.budget.remaining_seconds() <= 0:
                raise ServiceError(409, 'approval is no longer pending')
            write_exclusive_json({'request': request, 'approved': approved}, job.directory / 'decision.json')
            job.decision = approved
            job.state = 'researching' if approved else job.state
            job.condition.notify_all()
            return job.snapshot()

    def cancel(self, identifier):
        job = self.get(identifier)
        with job.condition:
            if job.state not in TERMINAL:
                job.cancelled.set()
                job.state = 'cancel_requested'
                job.condition.notify_all()
            return job.snapshot()

    def _run(self, job):
        try:
            value = {k: v for k, v in job.input.items() if k not in ('answer_text', 'consent')}
            job.budget = ServiceBudget(job.cancelled, max_attempts=value['max_attempts'], max_seconds=value['max_seconds'])

            def checkpoint(transcript):
                # Never check cancellation here: outer collector failure must preserve evidence.
                with job.condition:
                    job.sequence += 1
                    job.attempts = len(transcript['attempts'])
                    write_exclusive_json(transcript, job.directory / f'checkpoint-{job.sequence:03d}.json')

            def approve(request):
                with job.condition:
                    write_exclusive_json(request, job.directory / 'pending-approval.json')
                    job.approval = deepcopy(request)
                    if not job.cancelled.is_set():
                        job.state = 'awaiting_approval'
                    while job.decision is None:
                        if job.cancelled.is_set():
                            raise Interrupted('cancelled')
                        remaining = job.budget.remaining_seconds()
                        if remaining <= 0:
                            raise Interrupted('expired')
                        job.condition.wait(timeout=remaining)
                    if job.cancelled.is_set():
                        raise Interrupted('cancelled')
                    if job.budget.remaining_seconds() <= 0:
                        raise Interrupted('expired')
                    if job.decision:
                        job.state = 'researching'
                    return job.decision

            transports = self.transport_factory() if self.transport_factory else (self._live_transports() if self.mode == 'live' else demo_transports())
            transcript = collect_transcript(job.input['answer_text'], **value, **transports,
                                            approve=approve, checkpoint=checkpoint, budget=job.budget)
            with job.condition:
                job.budget.check()
                write_run(transcript, job.directory / 'run')
                load_verified_run(job.directory / 'run')
                job.reason = transcript['stop_reason']
                job.state = 'completed' if job.reason is None else ('declined' if job.reason == 'approval_declined' else 'partial')
                write_exclusive_json({'state': job.state, 'stop_reason': job.reason}, job.directory / 'completion.json')
                job.has_report = True
                job.final_seconds = 0  # freeze the clock together with the terminal state, not after the lock is released
        except BaseException as exc:
            with job.condition:
                job.state = str(exc) if isinstance(exc, Interrupted) else 'failed'
                job.reason = job.state
                job.final_seconds = 0
                try:
                    write_exclusive_json({'state': job.state, 'error': 'collection stopped; checkpoints retained; no automatic retry'}, job.directory / 'failure.json')
                except OSError:
                    pass
        finally:
            with job.condition:
                job.final_seconds = 0
            with self.lock:
                self.active = None

    def artifact(self, identifier, name):
        if name not in ARTIFACTS:
            raise ServiceError(404, 'unknown artifact')
        job = self.get(identifier)
        with job.condition:
            if not job.has_report:
                raise ServiceError(409, 'verified report not available')
            safe_path(self.root, job.id)
            run = safe_path(job.directory, 'run')
            snapshot = load_verified_run(run)
            if name == 'report.html':
                return render_html(snapshot['report']).encode('utf-8')
            if name in ('input.json', 'report.json'):
                return json_bytes(snapshot[name[:-5]])
            return safe_path(run, name).read_bytes()

    def close(self):
        with self.lock:
            self.closed = True
            identifier = self.active
        if identifier:
            self.cancel(identifier)
