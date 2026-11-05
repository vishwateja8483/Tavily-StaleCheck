"""Startup failures reserve neither output directories nor abandoned listeners."""
from pathlib import Path
import socket
import tomllib
from types import SimpleNamespace

import pytest

from stalecheck import __version__
from stalecheck.cli import _serve_assessment, main
from stalecheck.web_jobs import JobManager
from stalecheck.web_service import AssessmentServer


@pytest.fixture
def static(tmp_path):
    root = tmp_path / 'static'
    root.mkdir()
    (root / 'index.html').write_text('<!doctype html><title>test</title>')
    return root


def test_occupied_port_preserves_output_and_allows_retry(tmp_path, static):
    output = tmp_path / 'sessions'
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with pytest.raises(ValueError, match='--port 0'):
            AssessmentServer.create(lambda: JobManager(output), static, port=port)
        assert not output.exists()
    server = AssessmentServer.create(lambda: JobManager(output), static, port=port)
    try:
        assert output.is_dir()
        assert server.manager.root == output
    finally:
        server.server_close()
    assert server.manager.closed


@pytest.mark.parametrize('port', [-1, 65536, True])
def test_invalid_port_does_not_reserve_output(tmp_path, static, port):
    output = tmp_path / 'sessions'
    with pytest.raises(ValueError, match='invalid port'):
        AssessmentServer.create(lambda: JobManager(output), static, port=port)
    assert not output.exists()


def test_missing_assets_do_not_reserve_output(tmp_path):
    output = tmp_path / 'sessions'
    with pytest.raises(ValueError, match='assets missing'):
        AssessmentServer.create(lambda: JobManager(output), tmp_path / 'missing')
    assert not output.exists()


def test_existing_output_is_untouched_and_listener_released(tmp_path, static):
    output = tmp_path / 'sessions'
    output.mkdir()
    evidence = output / 'retained.json'
    evidence.write_bytes(b'original evidence')
    ports = []

    class ObservedServer(AssessmentServer):
        def server_bind(self):
            super().server_bind()
            ports.append(self.server_port)

    with pytest.raises(FileExistsError):
        ObservedServer.create(lambda: JobManager(output), static)
    assert evidence.read_bytes() == b'original evidence'
    assert list(output.iterdir()) == [evidence]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', ports[0]))


def test_manager_failure_closes_listener(tmp_path, static):
    servers = []

    class ObservedServer(AssessmentServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.append(self)

    def broken():
        raise RuntimeError('manager failed')

    with pytest.raises(RuntimeError, match='manager failed'):
        ObservedServer.create(broken, static)
    assert servers[0].socket.fileno() == -1


def test_cli_handoff_failure_closes_manager_and_listener(tmp_path, monkeypatch):
    servers = []
    original = AssessmentServer.create

    def create(*args, **kwargs):
        server = original(*args, **kwargs)
        servers.append(server)
        return server

    def broken_print(*args, **kwargs):
        raise BrokenPipeError('terminal closed')

    monkeypatch.setattr(AssessmentServer, 'create', create)
    monkeypatch.setattr('builtins.print', broken_print)
    with pytest.raises(BrokenPipeError):
        _serve_assessment(SimpleNamespace(output=tmp_path / 'sessions', port=0, live=False))
    assert servers[0].manager.closed
    assert servers[0].socket.fileno() == -1


def test_doctor_is_offline_and_documents_both_live_entrypoints(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('doctor must not inspect provider environment or use the network')

    import os
    from stalecheck.web_jobs import SETTINGS
    original = os.environ.get

    def environment(key, *args):
        if key in SETTINGS:
            forbidden()
        return original(key, *args)

    monkeypatch.setattr(os.environ, 'get', environment)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    assert main(['doctor']) == 0
    output = capsys.readouterr().out
    assert 'stale-check --live --confirm-outbound' in output
    assert 'serve-assessment --live' in output
    assert 'connectivity not checked' in output


def test_distribution_and_module_versions_match():
    project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    assert tomllib.loads(project.read_text())['project']['version'] == __version__
