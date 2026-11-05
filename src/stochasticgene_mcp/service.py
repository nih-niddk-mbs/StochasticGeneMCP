from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib
import uuid

DEFAULT_STOCHASTICGENE_VERSION = "2.1.1"
DEFAULT_HOME = Path("~/.stochasticgene-mcp").expanduser()
DEFAULT_ENV = DEFAULT_HOME / "julia-env"
MAX_DATA_BYTES = 50 * 1024 * 1024
MAX_TRACE_FILES = 2000
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Service:
    def __init__(self, config_file: str | Path):
        self.config_file = Path(config_file).expanduser().resolve(strict=True)
        self.config = tomllib.loads(self.config_file.read_text())
        required = {"input_roots", "jobs_root"}
        if not required <= self.config.keys():
            raise ValueError(f"Missing configuration fields: {sorted(required - self.config.keys())}")
        self.roots = [Path(p).expanduser().resolve(strict=True) for p in self.config["input_roots"]]
        if not self.roots or not all(p.is_dir() for p in self.roots):
            raise ValueError("input_roots must contain existing directories")
        self.jobs = Path(self.config["jobs_root"]).expanduser().resolve()
        if any(self.jobs.is_relative_to(p) for p in self.roots):
            raise ValueError("jobs_root must be outside input_roots")
        # Default project is the environment created by `stochasticgene-mcp setup`.
        self.project = Path(self.config.get("julia_project") or DEFAULT_ENV).expanduser().resolve(strict=True)
        julia = self.config.get("julia") or shutil.which("julia")
        if not julia:
            raise ValueError("Julia was not found; set `julia` in the configuration")
        self.julia = Path(julia).expanduser().resolve(strict=True)
        self.runner = Path(__file__).resolve().parents[2] / "julia" / "runner.jl"
        self.max_threads = self.config.get("max_threads", 8)
        self.timeout = self.config.get("timeout_seconds", 600)
        if type(self.max_threads) is not int or not 1 <= self.max_threads <= 64:
            raise ValueError("max_threads must be an integer from 1 to 64")
        if type(self.timeout) is not int or not 1 <= self.timeout <= 86400:
            raise ValueError("timeout_seconds must be an integer from 1 to 86400")
        self.version = str(self.config.get("stochasticgene_version", DEFAULT_STOCHASTICGENE_VERSION))
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError("stochasticgene_version must look like 2.1.0")
        # Optional: only for a development checkout that must match a commit.
        self.revision = self.config.get("expected_revision")
        if self.revision is not None and not re.fullmatch(r"[a-f0-9]{40}", self.revision):
            raise ValueError("expected_revision must be a full Git commit hash")

    def input_folder(self, folder: str) -> Path:
        path = Path(folder).expanduser()
        if not path.is_absolute():
            path = self.roots[0] / path
        path = path.resolve(strict=True)
        if not path.is_dir() or not any(path.is_relative_to(root) for root in self.roots):
            raise ValueError("Input folder is outside the configured input_roots")
        return path

    def input_file(self, file: str) -> Path:
        path = Path(file).expanduser()
        if not path.is_absolute():
            path = self.roots[0] / path
        path = path.resolve(strict=True)
        if not path.is_file() or not any(path.is_relative_to(root) for root in self.roots):
            raise ValueError("Input file is outside the configured input_roots")
        if path.stat().st_size > MAX_DATA_BYTES:
            raise ValueError(f"Input file is larger than {MAX_DATA_BYTES // 2**20} MB")
        return path

    def _new_job(self) -> Path:
        self.jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = self.jobs / uuid.uuid4().hex
        job.mkdir(mode=0o700)
        return job

    def _job(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid run ID")
        job = (self.jobs / run_id).resolve(strict=True)
        if job.parent != self.jobs:
            raise ValueError("Invalid run directory")
        return job

    def _julia(self, job: Path, operation: str, payload: dict, threads: int) -> dict:
        request = {"protocol": 1, "operation": operation, "job_dir": str(job),
                   "expected_version": self.version, "expected_revision": self.revision, **payload}
        request_file = job / f"{operation}.request.json"
        response_file = job / f"{operation}.response.json"
        atomic_json(request_file, request)
        env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        command = [str(self.julia), "--startup-file=no", f"--project={self.project}",
                   "-t", str(threads), str(self.runner), str(request_file), str(response_file)]
        with (job / f"{operation}.log").open("wb") as log:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, env=env, timeout=self.timeout)
        if not response_file.is_file():
            raise RuntimeError(f"Julia exited {completed.returncode}; see {operation}.log in {job}")
        response = json.loads(response_file.read_text())
        if completed.returncode or not response.get("ok"):
            raise RuntimeError(response.get("error", "Julia operation failed"))
        return response["result"]

    def inspect_results(self, folder: str) -> dict:
        source = self.input_folder(folder)
        job = self._new_job()
        result = self._julia(job, "inspect", {"folder": str(source)}, 1)
        return {"inspection_id": job.name, **result}

    def compute_correlations(self, folder: str, ratetype: str = "ml", trace_center: bool = True,
                             window_length: int = 216, frame_interval: float = 5/3,
                             maxlag: float = 120.0, threads: int = 8) -> dict:
        source = self.input_folder(folder)
        if ratetype not in {"ml", "mean", "median", "last"}:
            raise ValueError("ratetype must be ml, mean, median, or last")
        if type(trace_center) is not bool:
            raise ValueError("trace_center must be Boolean")
        if type(threads) is not int or not 1 <= threads <= self.max_threads:
            raise ValueError(f"threads must be an integer from 1 to {self.max_threads}")
        if type(window_length) is not int or not 2 <= window_length <= 10000:
            raise ValueError("window_length must be an integer from 2 to 10000")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (frame_interval, maxlag)):
            raise ValueError("Lag and frame interval must be finite numbers")
        if frame_interval <= 0 or maxlag <= 0 or maxlag / frame_interval > 10000:
            raise ValueError("Invalid lag range or frame interval")
        if not math.isclose(maxlag / frame_interval, round(maxlag / frame_interval), abs_tol=1e-8):
            raise ValueError("maxlag must be an integer multiple of frame_interval")
        if trace_center and maxlag >= window_length * frame_interval:
            raise ValueError("Trace-centered lags must be shorter than the trace window")
        job = self._new_job()
        settings = dict(ratetype=ratetype, trace_center=trace_center, window_length=window_length,
                        frame_interval=frame_interval, maxlag=maxlag, threads=threads)
        prepared = self._julia(job, "prepare", {"folder": str(source), "settings": settings}, 1)
        plan = {"kind": "correlations", "run_id": job.name, "source": str(source), "settings": settings,
                "prepared": prepared, "expected_version": self.version, "expected_revision": self.revision}
        return self._awaiting_approval(job, plan, "Preparation only. No correlation computation has started.")

    def prepare_fit_rna(self, data: str, name: str = "gene", G: int = 2, R: int = 0, S: int = 0,
                        insertstep: int = 1, halflife: float | None = None, samplesteps: int = 100_000,
                        nchains: int = 2, maxtime: float | None = None, threads: int = 4) -> dict:
        source = self.input_file(data)
        maxtime = self._check_fit(name, G, R, S, insertstep, halflife, samplesteps, nchains, maxtime, threads)
        job = self._new_job()
        (job / "inputs").mkdir(mode=0o700)
        snapshot = job / "inputs" / f"{name}.txt"
        sha = sha256_file(source)
        shutil.copyfile(source, snapshot)
        if sha256_file(snapshot) != sha:
            raise RuntimeError("Input changed during preparation")
        settings = dict(name=name, G=G, R=R, S=S, insertstep=insertstep, halflife=halflife,
                        samplesteps=samplesteps, nchains=nchains, maxtime=float(maxtime), threads=threads)
        prepared = self._julia(job, "prepare_fit", {"data_file": str(snapshot)}, 1)
        prepared["snapshots"] = [{"source": str(source), "snapshot": str(snapshot.relative_to(job)), "sha256": sha}]
        plan = {"kind": "fit_rna", "run_id": job.name, "source": str(source), "settings": settings,
                "prepared": prepared, "expected_version": self.version, "expected_revision": self.revision}
        return self._awaiting_approval(job, plan, "Preparation only. No fit has started.")

    def _check_fit(self, name, G, R, S, insertstep, halflife, samplesteps, nchains, maxtime, threads) -> float:
        if maxtime is None:
            maxtime = min(300.0, float(self.timeout))
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError("name must be 1-64 letters, digits, '_' or '-', starting with a letter or digit")
        for label, value, low, high in (("G", G, 1, 4), ("R", R, 0, 20), ("S", S, 0, 20),
                                        ("insertstep", insertstep, 1, 21), ("nchains", nchains, 1, 16),
                                        ("samplesteps", samplesteps, 1000, 10_000_000),
                                        ("threads", threads, 1, self.max_threads)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{label} must be an integer from {low} to {high}")
        if S > R or insertstep > max(R, 1):
            raise ValueError("S cannot exceed R and insertstep cannot exceed R (use 1 when R is 0)")
        if halflife is not None and (type(halflife) not in (int, float) or not math.isfinite(halflife) or halflife <= 0):
            raise ValueError("halflife must be a positive number of minutes")
        if type(maxtime) not in (int, float) or not math.isfinite(maxtime) or not 1 <= maxtime <= self.timeout:
            raise ValueError(f"maxtime must be from 1 to timeout_seconds ({self.timeout}) seconds")
        return float(maxtime)

    def prepare_fit_traces(self, data: str, name: str = "gene", G: int = 2, R: int = 1, S: int = 0,
                           insertstep: int = 1, interval: float = 1.0, halflife: float | None = None,
                           samplesteps: int = 10_000, nchains: int = 2, maxtime: float | None = None,
                           threads: int = 4, zeromedian: bool = True, datacol: int = 3) -> dict:
        """Prepare a fit to a folder of trace files (one frame per row, intensity in column `datacol`)."""
        if tuple(int(v) for v in self.version.split(".")) < (2, 2, 0):
            raise ValueError("Trace fits need StochasticGene 2.2.0 or newer; run: stochasticgene-mcp setup --version 2.2.0")
        source = self.input_folder(data)
        maxtime = self._check_fit(name, G, R, S, insertstep, halflife, samplesteps, nchains, maxtime, threads)
        if R < 1:
            raise ValueError("Trace fits need R of at least 1")
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval must be a positive number of minutes")
        if type(zeromedian) is not bool:
            raise ValueError("zeromedian must be Boolean")
        if type(datacol) is not int or not 1 <= datacol <= 20:
            raise ValueError("datacol must be an integer from 1 to 20")
        files = sorted(p for p in source.rglob("*") if p.is_file() and not p.is_symlink() and not p.name.startswith("."))
        if not files:
            raise ValueError("No trace files found in the folder")
        if len(files) > MAX_TRACE_FILES or sum(p.stat().st_size for p in files) > MAX_DATA_BYTES:
            raise ValueError(f"Trace folder is limited to {MAX_TRACE_FILES} files and {MAX_DATA_BYTES // 2**20} MB")
        job = self._new_job()
        folder = job / "inputs" / "traces"
        folder.mkdir(parents=True, mode=0o700)
        snapshots = []
        for index, path in enumerate(files, 1):
            target = folder / f"{index:05d}.txt"
            sha = sha256_file(path)
            shutil.copyfile(path, target)
            if sha256_file(target) != sha:
                raise RuntimeError(f"Input changed during preparation: {path}")
            snapshots.append({"source": str(path), "snapshot": str(target.relative_to(job)), "sha256": sha})
        settings = dict(name=name, G=G, R=R, S=S, insertstep=insertstep, interval=float(interval), halflife=halflife,
                        samplesteps=samplesteps, nchains=nchains, maxtime=maxtime, threads=threads,
                        zeromedian=zeromedian, datacol=datacol)
        prepared = self._julia(job, "prepare_fit_traces", {"folder": str(folder), "datacol": datacol}, 1)
        prepared["snapshots"] = snapshots
        plan = {"kind": "fit_traces", "run_id": job.name, "source": str(source), "settings": settings,
                "prepared": prepared, "expected_version": self.version, "expected_revision": self.revision}
        return self._awaiting_approval(job, plan, "Preparation only. No fit has started.")

    def describe_fit(self, target: str) -> dict:
        """Summarize fitted rates for a completed run ID or a results folder under input_roots."""
        if re.fullmatch(r"[a-f0-9]{32}", target):
            found = sorted(p for p in (self._job(target) / "fit" / "results").glob("*") if p.is_dir())
            if len(found) != 1:
                raise ValueError("This run has no completed fit results")
            folder = found[0]
        else:
            folder = self.input_folder(target)
        job = self._new_job()
        return {"description_id": job.name, **self._julia(job, "describe", {"folder": str(folder)}, 1)}

    def wait(self, run_id: str, poll: float = 2.0) -> dict:
        deadline = time.time() + self.timeout + 300
        while time.time() < deadline:
            status = self.run_status(run_id)
            if status["state"] in {"completed", "failed"}:
                return status
            time.sleep(poll)
        raise TimeoutError(f"Run {run_id} did not finish in time; check its status later")

    def _awaiting_approval(self, job: Path, plan: dict, note: str) -> dict:
        atomic_json(job / "plan.json", plan)
        atomic_json(job / "status.json", {"state": "awaiting_approval", "created": time.time()})
        return {"run_id": job.name, "state": "awaiting_approval", "plan": plan,
                "approval_command": [sys.executable, "-m", "stochasticgene_mcp.cli",
                                     "--config", str(self.config_file), "approve", job.name],
                "note": note}

    def approve(self, run_id: str) -> dict:
        # Not exposed as an MCP tool: a human runs this command locally.
        job = self._job(run_id)
        state = self.run_status(run_id)["state"]
        if state != "awaiting_approval":
            raise ValueError(f"Run is not awaiting approval: {state}")
        plan = json.loads((job / "plan.json").read_text())
        with (job / "approval.json").open("x") as stream:
            json.dump({"plan_hash": digest(plan), "approved_at": time.time()}, stream)
        atomic_json(job / "status.json", {"state": "starting", "updated": time.time()})
        with (job / "worker.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-m", "stochasticgene_mcp.worker",
                                      str(self.config_file), run_id], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return {"run_id": run_id, "state": "starting", "pid": child.pid}

    def execute_approved(self, run_id: str) -> None:
        job = self._job(run_id)
        try:
            with (job / "claimed").open("x"):
                pass
            plan = json.loads((job / "plan.json").read_text())
            approval = json.loads((job / "approval.json").read_text())
            if approval["plan_hash"] != digest(plan):
                raise ValueError("Plan changed after approval; prepare a new run")
            if (plan.get("expected_version"), plan.get("expected_revision")) != (self.version, self.revision):
                raise ValueError("Configured StochasticGene version changed; prepare a new run")
            # One compute run at a time in this local prototype.
            with (self.jobs / "compute.lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                atomic_json(job / "status.json", {"state": "running", "updated": time.time(), "pid": os.getpid()})
                result = self._julia(job, "compute", {"plan": plan}, plan["settings"]["threads"])
                atomic_json(job / "status.json", {"state": "completed", "updated": time.time(), **result})
        except Exception as error:
            atomic_json(job / "status.json", {"state": "failed", "error": str(error), "updated": time.time()})

    def run_status(self, run_id: str) -> dict:
        job = self._job(run_id)
        status = job / "status.json"
        if not status.is_file():
            raise ValueError("This ID does not identify a prepared run")
        return {"run_id": run_id, **json.loads(status.read_text()), "directory": str(job)}
