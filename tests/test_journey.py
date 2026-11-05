"""Offline journey accounting tests with clocks and injected transports only."""
import json

import pytest

from stalecheck.acquisition import AcquisitionError, MockTransport, Response, acquire
from stalecheck.contracts import ValidationError, json_bytes
from stalecheck.gateway import GatewayClient, GatewayError, MAX_ERROR_BYTES, MAX_RESPONSE_BYTES, MockGatewayTransport
from stalecheck.journey import JourneyBudget


MESSAGES = [{"role": "user", "content": "Synthetic request"}]
PLAN = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True,
        "queries": ["Synthetic query"], "urls": [], "primary_domains": ["python.org"]}
USAGE = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}


def reply(**changes):
    return {"model": "test/model", "usage": USAGE,
            "choices": [{"message": {"content": "Synthetic reply"}, "finish_reason": "stop"}], **changes}


def gateway(transport):
    return GatewayClient("http://localhost:8080", "test/model", transport=transport,
                         allow_network=True, token="SYNTHETIC-SECRET")


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"max_attempts": 26}, {"max_attempts": True},
                                    {"max_seconds": 0}, {"max_seconds": 181}, {"max_seconds": float("nan")},
                                    {"max_seconds": float("inf")}, {"max_seconds": True}])
def test_budget_limits_only_allow_lower_positive_values(kwargs):
    with pytest.raises(ValidationError):
        JourneyBudget(**kwargs)


def test_budget_attempt_ceiling_deadline_and_nonthrowing_finish():
    tick = [10.0]
    budget = JourneyBudget(max_attempts=1, max_seconds=2, monotonic=lambda: tick[0])
    tick[0] = 11.0
    attempt, timeout = budget.begin("gateway", 60)
    assert timeout == 1
    budget.finish(attempt, usage=USAGE)
    budget.check()  # The final allowed attempt may still complete successfully.
    with pytest.raises(ValidationError, match="attempt budget"):
        budget.begin("search", 15)
    assert len(budget.records) == 1
    tick[0] = 12
    budget.finish(attempt, usage=USAGE, status="failed")  # Never masks a deadline error.
    with pytest.raises(ValidationError, match="time budget"):
        budget.check()
    assert budget.max_attempts == 1 and budget.max_seconds == 2
    assert json.loads(json.dumps(budget.records)) == budget.records


def test_shared_budget_counts_gateway_and_search_and_blocks_before_transport():
    budget = JourneyBudget(max_attempts=2)
    chat = MockGatewayTransport([{"status": 200, "body": reply()}])
    search = MockTransport([{"path": "/search", "status": 200, "body": {"results": [], "usage": {"credits": 1}}}])
    gateway(chat).chat(MESSAGES, budget=budget)
    acquire(PLAN, transport=search, budget=budget)
    with pytest.raises(ValidationError):
        gateway(chat).chat(MESSAGES, budget=budget)
    assert len(chat.calls) == len(search.calls) == 1
    assert [r["service"] for r in budget.records] == ["gateway", "search"]
    assert [r["status"] for r in budget.records] == ["ok", "ok"]
    assert budget.records[0]["usage"] == USAGE
    assert budget.records[1]["usage"] == {"credits": 1}
    assert budget.records[1]["routing"] is None


@pytest.mark.parametrize("service", ["gateway", "search"])
def test_deadline_bounds_timeout_and_retains_late_response_usage(service):
    tick = [0.0]
    budget = JourneyBudget(max_seconds=2, monotonic=lambda: tick[0])
    seen = []

    def late(*args, **kwargs):
        seen.append(kwargs["timeout"])
        tick[0] = 3
        return Response(200, json_bytes(reply() if service == "gateway" else {"results": [], "usage": {"credits": 2}}))

    with pytest.raises(ValidationError):
        if service == "gateway":
            gateway(late).chat(MESSAGES, budget=budget)
        else:
            acquire(PLAN, transport=late, allow_network=True, api_key="SYNTHETIC", budget=budget)
    assert seen == [2]
    assert budget.records[0]["status"] == "failed"
    assert budget.records[0]["usage"] == (USAGE if service == "gateway" else {"credits": 2})


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_budgeted_search_never_retries_http_failure(status):
    budget = JourneyBudget()
    transport = MockTransport([{"path": "/search", "status": status,
                                "body": {"error": "SYNTHETIC-SECRET", "usage": {"credits": 1}}}] * 2)
    sleeps = []
    with pytest.raises(AcquisitionError) as caught:
        acquire(PLAN, transport=transport, budget=budget, sleep=sleeps.append)
    assert len(transport.calls) == len(budget.records) == 1 and sleeps == []
    assert budget.records[0] == {"service": "search", "status": "failed", "usage": {"credits": 1}, "routing": None}
    assert "SYNTHETIC-SECRET" not in str(caught.value) + json.dumps(caught.value.partial_result)


@pytest.mark.parametrize("endpoint,field", [
    ("/search", "content"), ("/search", "title"), ("/search", "url"),
    ("/search", "published_date"), ("/search", "outside_url"),
    ("/extract", "raw_content"), ("/extract", "title"), ("/extract", "published_date"),
])
def test_acquisition_discards_credential_reflection_before_checkpoint(endpoint, field):
    token = "SYNTHETIC-SECRET"
    url = "https://docs.python.org/3/"
    row = {"url": url, "content": "Synthetic content", "raw_content": "Synthetic content", "title": "Synthetic title"}
    if field in ("url", "outside_url"):
        host = "docs.python.org" if field == "url" else "outside.example.com"
        row["url"] = f"https://{host}/{token}"
    else:
        row[field] = f"Reflected {token}"
    plan = PLAN if endpoint == "/search" else {**PLAN, "queries": [], "urls": [url]}
    body = {"results": [row], "usage": {"credits": 2}}
    if endpoint == "/extract":
        body["failed_results"] = []
    transport = MockTransport([{"path": endpoint, "status": 200, "body": body}])
    budget = JourneyBudget()
    with pytest.raises(AcquisitionError, match="reflected the credential") as caught:
        acquire(plan, transport=transport, api_key=token, budget=budget)
    partial = caught.value.partial_result
    assert partial["candidates"] == []
    assert partial["requests"][0]["provider_usage"] == {"credits": 2}
    assert budget.records[0]["status"] == "failed" and budget.records[0]["usage"] == {"credits": 2}
    assert token not in str(caught.value) + json.dumps(partial) + json.dumps(budget.records)


@pytest.mark.parametrize("changes", [{"results": "invalid"}, {"request_id": []}])
def test_search_failed_contract_keeps_independently_validated_credits(changes):
    budget = JourneyBudget()
    transport = MockTransport([{"path": "/search", "status": 200,
                                "body": {"results": [], "usage": {"credits": 2}, **changes}}])
    with pytest.raises(AcquisitionError) as caught:
        acquire(PLAN, transport=transport, budget=budget)
    assert budget.records[0]["usage"] == {"credits": 2}
    assert budget.records[0]["status"] == "failed"
    assert caught.value.partial_result["requests"][0]["provider_usage"] == {"credits": 2}


@pytest.mark.parametrize("succeeds", [True, False])
def test_gateway_missing_usage_matches_success_reply_but_failure_stays_unknown(succeeds):
    budget = JourneyBudget()
    body = reply()
    del body["usage"]
    if not succeeds:
        body["choices"] = []
    transport = MockGatewayTransport([{"status": 200, "body": body}])
    if succeeds:
        result = gateway(transport).chat(MESSAGES, budget=budget)
        assert budget.records[0]["usage"] == result["usage"] == {
            "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        assert budget.records[0]["status"] == "ok"
    else:
        with pytest.raises(GatewayError):
            gateway(transport).chat(MESSAGES, budget=budget)
        assert budget.records[0]["usage"] is None
        assert budget.records[0]["status"] == "failed"


def test_gateway_failed_contract_keeps_usage_and_sanitizes_routing():
    budget = JourneyBudget()
    transport = MockGatewayTransport([{"status": 200, "body": reply(choices=[],
        extra_fields={"routing_info": {"provider": "SYNTHETIC-SECRET", "model": "test/model", "is_fallback": True}})}])
    with pytest.raises(GatewayError, match="contract"):
        gateway(transport).chat(MESSAGES, budget=budget)
    record = budget.records[0]
    assert record["status"] == "failed" and record["usage"] == USAGE
    assert record["routing"] == {"provider": None, "model": "test/model", "is_fallback": True}
    assert "SYNTHETIC-SECRET" not in json.dumps(budget.records)


@pytest.mark.parametrize("kind", [[], {}, ["budget_exceeded"]])
def test_gateway_unhashable_refusal_is_sanitized(kind):
    budget = JourneyBudget()
    transport = MockGatewayTransport([{"status": 402, "body": {"error": {"type": kind}, "usage": USAGE}}])
    with pytest.raises(GatewayError) as caught:
        gateway(transport).chat(MESSAGES, budget=budget)
    assert caught.value.kind is None and caught.value.status == 402
    assert budget.records[0]["usage"] == USAGE and budget.records[0]["status"] == "failed"


@pytest.mark.parametrize("error", [OSError, ValidationError, GatewayError])
def test_custom_gateway_transport_errors_cannot_echo_credentials(error):
    budget = JourneyBudget()

    def boom(*args, **kwargs):
        raise error("SYNTHETIC-SECRET")

    with pytest.raises(GatewayError) as caught:
        gateway(boom).chat(MESSAGES, budget=budget)
    assert "SYNTHETIC-SECRET" not in str(caught.value)
    assert budget.records == [{"service": "gateway", "status": "failed", "usage": None, "routing": None}]


@pytest.mark.parametrize("status,limit", [(200, MAX_RESPONSE_BYTES), (402, MAX_ERROR_BYTES)])
def test_custom_gateway_response_bytes_are_bounded(status, limit):
    budget = JourneyBudget()

    def oversized(*args, **kwargs):
        body = json_bytes({"type": "budget_exceeded", "padding": "x" * limit})
        return Response(status, body)

    with pytest.raises(GatewayError) as caught:
        gateway(oversized).chat(MESSAGES, budget=budget)
    if status == 402:
        assert caught.value.kind is None and caught.value.status == status
    else:
        assert "byte bound" in str(caught.value)
    assert budget.records[0]["usage"] is None and budget.records[0]["status"] == "failed"


def test_budget_preflight_failure_does_not_invent_search_attempt():
    budget = JourneyBudget(max_attempts=1)
    record, _ = budget.begin("gateway", 1)
    budget.finish(record)
    transport = MockTransport([])
    with pytest.raises(AcquisitionError) as caught:
        acquire(PLAN, transport=transport, budget=budget)
    assert len(budget.records) == 1 and transport.calls == []
    assert caught.value.partial_result["requests"] == []
