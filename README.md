# StochasticGeneMCP

Local prototype of an MCP interface to StochasticGene.jl. Python handles MCP,
approved paths and run lifecycle. Julia reads model specifications and calls
the existing scientific functions. No LLM API key is needed by this server;
the MCP host provides the agent.

## Scope

- `inspect_results`: inspect trusted key-based result folders.
- `compute_correlations`: **prepare only** a frozen ON/reporter correlation run.
- `run_status`: read persisted status and output paths.

There is deliberately no MCP approval, arbitrary code, shell, package-install,
job-submission, or file-deletion tool. A separate local CLI command approves and
starts computation. Configuring an MCP host with shell access does not exempt
it from obtaining the user's approval before running that command.

The first version supports local macOS/Linux execution, at most one computation
at a time, and key-based coupled models, including hierarchical and hidden-unit
models. It does not yet compute shared-dataset correlations, fit models, submit
HPC jobs, or expose arbitrary observable pairs. Inspection is limited to 256
info files per request. No existing source results are rewritten.

## Install

Use Python 3.11 or newer and Julia 1.11. Use a clean StochasticGene checkout at
commit `b3d94c9d307708b2085f3978b5492f2391c87814` for this prototype. These features
are newer than the registered release; installing the release alone is not enough.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
cp config.example.toml config.local.toml
```

Edit `config.local.toml` with absolute paths. `input_roots` limit readable result
folders. `jobs_root` must be outside those roots. The configured Julia project
is the pinned StochasticGene checkout, with its dependencies already installed.
The companion `julia/Project.toml` records dependencies and the source pin;
the first prototype verifies a clean Git checkout at runtime.
`requirements.lock` records the dependency versions tested on the development
Mac; use it as pip constraints when reproducing that environment.

This is an editable-source installation. `julia/runner.jl` must remain beside
the Python source tree. A standalone wheel is not supported yet.

## Try It Without an Agent

```sh
.venv/bin/stochasticgene-mcp --config config.local.toml inspect RESULTS_FOLDER
.venv/bin/stochasticgene-mcp --config config.local.toml prepare RESULTS_FOLDER --threads 8
```

`RESULTS_FOLDER` may be absolute or relative to the first input root. Preparation
copies only the selected info/rate files into a new run directory, records their
SHA-256 checksums, and returns `awaiting_approval`. Review `plan.json`, including
the channel order, selected rate row, centering, frame interval and resource limits.
Then use the returned run ID:

```sh
.venv/bin/stochasticgene-mcp --config config.local.toml approve RUN_ID
.venv/bin/stochasticgene-mcp --config config.local.toml status RUN_ID
```

Approval starts a bounded local worker and returns immediately. Julia logs,
requests, responses, approval and status are saved in the run directory. The
worker checks the approved plan, frozen inputs and pinned revision before
computing. Altered plans/inputs are rejected. Completed outputs are standard
StochasticGene CSVs under that run's `results/` directory.

Only one compute job runs at once. An approval attempted during another run
fails rather than queuing. There is no automatic retry or cancellation tool.
An interrupted machine/session can leave status stale: inspect the local worker
log and process before retrying; prepare a new run rather than reusing an approval.

## MCP Host Configuration

Launch the server over stdio; no network listener is needed:

```json
{
  "mcpServers": {
    "stochasticgene": {
      "command": "/absolute/path/to/StochasticGeneMCP/.venv/bin/stochasticgene-mcp",
      "args": ["--config", "/absolute/path/to/config.local.toml", "serve"]
    }
  }
}
```

The host's configuration format can differ. The underlying executable and
arguments are the same. Julia stdout/stderr is captured in run logs, not sent
over the MCP protocol stream. Require host confirmation for run preparation,
which creates snapshots, and keep actual execution behind human CLI approval.

## Scientific and Security Boundaries

- Use **trusted** local JLD2 files. Loading Julia objects is not a sandbox for
  untrusted uploads. Do not use this prototype with PII/PHI.
- All parameter/model interpretation stays in Julia. The inspector reports
  declared fitted indices without pretending to expand every hierarchical copy.
- Lags and frame intervals are minutes; window lengths are frames. Defaults are
  ML rates, 216 frames, 100-second intervals, trace centering, +/-120 minutes.
- Outputs are unnormalized covariances in `_centered` columns; raw-moment
  columns retain their original meaning. This does not claim equivalence with
  an empirical per-trace normalized IDL estimator.
- The source revision is checked before preparation and execution. Updating
  StochasticGene requires deliberate revalidation and changing the pin.
- Source input files are never replaced. Output paths are generated per run,
  not chosen by the model. Symlink escapes, invalid paths and excessive threads
  are rejected. The server has ordinary user privileges, not an OS sandbox.
- This prototype is **not an authorized remote-access service for Biowulf**.
  There is no SSH, tunneling or scheduler integration. Before any HPC deployment,
  review https://hpc.nih.gov/agents and https://hpc.nih.gov/AGENTS.md, use an
  allocated compute session, and obtain any required administrative approval.

## Tests

```sh
.venv/bin/python -m pytest -q
```

Tests exercise validation, path restrictions, approval semantics and actual MCP
roundtrips, including stdio. Private Nadya data is not included in this repository.
The initial real-data acceptance check used the November 1 coupled result set;
its validation report is recorded separately without raw traces.
