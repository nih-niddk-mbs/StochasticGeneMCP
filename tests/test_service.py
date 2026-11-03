import asyncio
import json
from pathlib import Path
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from stochasticgene_mcp.server import create_server
from stochasticgene_mcp.service import Service, atomic_json, digest


@pytest.fixture
def service(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f'''input_roots = [{json.dumps(str(source))}]
jobs_root = {json.dumps(str(tmp_path / "runs"))}
julia = {json.dumps(sys.executable)}
julia_project = {json.dumps(str(tmp_path))}
expected_revision = "{'0' * 40}"
max_threads = 8
timeout_seconds = 30
''')
    return Service(config)


@pytest.mark.parametrize("folder", ["..", "/", "../missing"])
def test_reject_paths(service, folder):
    with pytest.raises((ValueError, FileNotFoundError)):
        service.input_folder(folder)


def test_symlink_escape(service, tmp_path):
    (service.roots[0] / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        service.input_folder("escape")


@pytest.mark.parametrize("kwargs", [
    {"threads": 9}, {"threads": True}, {"threads": 0}, {"ratetype": "guess"},
    {"frame_interval": 0}, {"frame_interval": float("nan")}, {"maxlag": -1},
    {"maxlag": 1.2}, {"window_length": 1}, {"window_length": True},
    {"trace_center": "true"}, {"window_length": 2},
])
def test_reject_settings(service, kwargs):
    with pytest.raises(ValueError):
        service.compute_correlations(str(service.roots[0]), **kwargs)


def test_preparation_does_not_compute(service, monkeypatch):
    operations = []
    def julia(job, operation, payload, threads):
        operations.append(operation)
        return {"model_count": 1}
    monkeypatch.setattr(service, "_julia", julia)
    result = service.compute_correlations(str(service.roots[0]))
    assert operations == ["prepare"]
    assert result["state"] == "awaiting_approval"
    assert service.run_status(result["run_id"])["state"] == "awaiting_approval"
    assert not (service._job(result["run_id"]) / "approval.json").exists()


def test_changed_plan_is_rejected(service, monkeypatch):
    monkeypatch.setattr(service, "_julia", lambda *args: {"model_count": 1})
    run_id = service.compute_correlations(str(service.roots[0]))["run_id"]
    job = service._job(run_id)
    plan = json.loads((job / "plan.json").read_text())
    atomic_json(job / "approval.json", {"plan_hash": digest(plan)})
    plan["settings"]["threads"] = 999
    atomic_json(job / "plan.json", plan)
    service.execute_approved(run_id)
    assert service.run_status(run_id)["state"] == "failed"
    assert "changed after approval" in service.run_status(run_id)["error"]


def test_mcp_tools_and_calls(service, monkeypatch):
    monkeypatch.setattr(service, "inspect_results", lambda folder: {"folder": folder, "models": []})
    async def run():
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {"inspect_results", "compute_correlations", "run_status"}
            result = await client.call_tool("inspect_results", {"folder": "example"})
            assert not result.is_error
            assert result.structured_content["folder"] == "example"
            rejected = await client.call_tool("compute_correlations", {"folder": str(service.roots[0]), "threads": 100})
            assert rejected.is_error
    asyncio.run(run())


def test_stdio_roundtrip(service):
    async def run():
        transport = StdioServerParameters(command=sys.executable, args=["-m", "stochasticgene_mcp.cli",
            "--config", str(service.config_file), "serve"])
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 3
            result = await client.call_tool("run_status", {"run_id": "not-a-run"})
            assert result.is_error
    asyncio.run(run())
