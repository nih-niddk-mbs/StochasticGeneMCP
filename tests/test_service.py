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
stochasticgene_version = "2.1.0"
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


def histogram(service):
    path = service.roots[0] / "hist.txt"
    path.write_text("\n".join(["10", "20", "15", "5", "1"]) + "\n")
    return path


@pytest.mark.parametrize("kwargs", [
    {"G": 5}, {"G": True}, {"R": -1}, {"S": 1}, {"insertstep": 2}, {"name": "../x"}, {"name": ""},
    {"halflife": -1}, {"halflife": float("nan")}, {"maxtime": 0}, {"maxtime": 31}, {"nchains": 17},
    {"samplesteps": 10}, {"threads": 9},
])
def test_reject_fit_settings(service, kwargs):
    with pytest.raises(ValueError):
        service.prepare_fit_rna(str(histogram(service)), **kwargs)


def test_fit_data_must_be_inside_roots(service, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("1\n2\n3\n4\n")
    with pytest.raises(ValueError):
        service.prepare_fit_rna(str(outside))


def test_fit_preparation_snapshots_and_does_not_compute(service, monkeypatch):
    operations = []
    def julia(job, operation, payload, threads):
        operations.append(operation)
        return {"bins": 5, "cells": 51.0, "runtime": {"version": "2.1.0"}}
    monkeypatch.setattr(service, "_julia", julia)
    source = histogram(service)
    result = service.prepare_fit_rna(str(source), name="MYC", halflife=600)
    assert operations == ["prepare_fit"]
    assert result["state"] == "awaiting_approval"
    plan = result["plan"]
    assert plan["kind"] == "fit_rna" and plan["expected_version"] == "2.1.0"
    snapshot = service._job(result["run_id"]) / plan["prepared"]["snapshots"][0]["snapshot"]
    assert snapshot.read_bytes() == source.read_bytes()
    assert not (service._job(result["run_id"]) / "approval.json").exists()


def test_changed_version_is_rejected(service, monkeypatch):
    monkeypatch.setattr(service, "_julia", lambda *args: {"bins": 5})
    run_id = service.prepare_fit_rna(str(histogram(service)))["run_id"]
    job = service._job(run_id)
    atomic_json(job / "approval.json", {"plan_hash": digest(json.loads((job / "plan.json").read_text()))})
    service.version = "2.2.0"
    service.execute_approved(run_id)
    status = service.run_status(run_id)
    assert status["state"] == "failed" and "version changed" in status["error"]


def test_bad_version_in_config(tmp_path):
    (tmp_path / "inputs").mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f'''input_roots = [{json.dumps(str(tmp_path / "inputs"))}]
jobs_root = {json.dumps(str(tmp_path / "runs"))}
julia = {json.dumps(sys.executable)}
julia_project = {json.dumps(str(tmp_path))}
stochasticgene_version = "latest"
''')
    with pytest.raises(ValueError):
        Service(config)


def test_setup_config_roundtrip(tmp_path):
    import tomllib
    from stochasticgene_mcp.setup import write_config
    values = {"input_roots": ["/a b", "/c"], "stochasticgene_version": "2.1.0", "max_threads": 8}
    write_config(tmp_path / "c.toml", values)
    assert tomllib.loads((tmp_path / "c.toml").read_text()) == values


def test_mcp_tools_and_calls(service, monkeypatch):
    monkeypatch.setattr(service, "inspect_results", lambda folder: {"folder": folder, "models": []})
    async def run():
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {"inspect_results", "compute_correlations", "run_status",
                                                           "prepare_fit_rna", "describe_fit"}
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
            assert len(tools.tools) == 5
            result = await client.call_tool("run_status", {"run_id": "not-a-run"})
            assert result.is_error
    asyncio.run(run())
