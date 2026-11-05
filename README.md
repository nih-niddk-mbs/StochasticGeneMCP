# StochasticGeneMCP

Local interface to StochasticGene.jl for people and agents. Python handles the
command line, MCP, approved paths and run lifecycle. Julia reads model
specifications and calls the existing scientific functions. No LLM API key is
needed by this server; the MCP host provides the agent.

## Scope

Command line and MCP tools:

- `fit-rna` / `prepare_fit_rna`: **prepare** a G-state fit to an RNA histogram
  (frozen input, plan, resource limits); a person approves it with the CLI.
- `fit-traces` / `prepare_fit_traces`: the same for a folder of live-cell trace
  files (needs StochasticGene 2.2.0 or newer, the default pin).
- `describe` / `describe_fit`: fitted rates (per minute) for a finished run or a
  results folder.
- `inspect` / `inspect_results`: inspect trusted key-based result folders.
- `correlations` / `compute_correlations`: **prepare** a frozen ON/reporter
  correlation run for coupled models.
- `run_status`: read persisted status and output paths.

There is deliberately no MCP approval, arbitrary code, shell, package-install,
job-submission, or file-deletion tool. A separate local CLI command approves and
starts computation. Configuring an MCP host with shell access does not exempt
it from obtaining the user's approval before running that command.

The server supports local macOS/Linux execution and at most one computation at
a time. Fits cover steady-state RNA histograms; correlations cover key-based
coupled models, including hierarchical and hidden-unit models. It does not yet
fit traces, compute shared-dataset correlations, submit HPC jobs, or expose
arbitrary observable pairs. Inspection is limited to 256 info files per request.
No existing source results are rewritten.

## Install

Use Python 3.11 or newer and Julia 1.11.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/stochasticgene-mcp setup --input-root /path/to/your/data
```

`setup` creates a Julia environment in `~/.stochasticgene-mcp/julia-env` with
StochasticGene pinned to an exact version (default 2.2.0) and writes
`~/.stochasticgene-mcp/config.toml`. `input_roots` limit readable data and
result folders; run records go to `jobs_root`, which must be outside them.

To change the pinned version, run `setup` again:

```sh
.venv/bin/stochasticgene-mcp setup --version 2.2.0
```

For a development checkout, use `setup --dev /path/to/StochasticGene.jl`; the
configuration then records the checkout's version. Plans prepared under one
version are rejected after the pin changes; prepare them again.
`requirements.lock` records the Python dependency versions tested on the
development Mac; use it as pip constraints when reproducing that environment.

This is an editable-source installation. `julia/runner.jl` must remain beside
the Python source tree. A standalone wheel is not supported yet. Use
`--config FILE` or `STOCHASTICGENE_MCP_CONFIG` for a configuration elsewhere;
`config.example.toml` lists every field.

## Fit an RNA Histogram

The data file's first column is the number of cells at each RNA copy number
(0, 1, 2, ...). It must be inside an input root.

```sh
.venv/bin/stochasticgene-mcp fit-rna hist.txt --name MYC --G 2 --halflife 600 --wait
```

`--halflife` is the mRNA half-life in minutes; without it the decay rate is 1.0
per minute and rates are meaningful only relative to decay. `--wait` approves
the run, waits and prints the rates. Without `--yes` or `--wait` the command only
prepares the run and prints the plan; approve it later with `approve RUN_ID`.
Show results again with `describe RUN_ID`. The default 100,000 samples is a
starting point: check R-hat and ESS in the run's `fit/` output and raise
`--samplesteps` if needed.

## Fit Live-Cell Traces

Point `fit-traces` at a folder of trace files (inside an input root): one file per
trace, one frame per row, intensity in column 3 by default (`--datacol` to change).
This needs StochasticGene 2.2.0 or newer (`setup --version 2.2.0`).

```sh
.venv/bin/stochasticgene-mcp fit-traces traces/ --name MYC --G 2 --R 1 --interval 1.0 --halflife 600 --wait
```

`--interval` is the frame interval in minutes and `R` must be at least 1. By default
each trace is median-subtracted and scaled; use `--raw-intensity` for raw values
(then the default noise priors do not apply, so use the Julia API to set them).
Trace fits are slower than histogram fits and need many more samples than the
default 10,000 for real data; check R-hat in the run's `fit/` output. See
[Choose a model and data](https://nih-niddk-mbs.github.io/StochasticGene.jl/dev/choosing_a_model.html)
for model choice, units and file formats.

## Analyze Existing Results

```sh
.venv/bin/stochasticgene-mcp inspect RESULTS_FOLDER
.venv/bin/stochasticgene-mcp correlations RESULTS_FOLDER --threads 8
```

`RESULTS_FOLDER` may be absolute or relative to the first input root. Preparation
copies only the selected info/rate files into a new run directory, records their
SHA-256 checksums, and returns `awaiting_approval`. Review `plan.json`, including
the channel order, selected rate row, centering, frame interval and resource limits.
Then use the returned run ID:

```sh
.venv/bin/stochasticgene-mcp approve RUN_ID
.venv/bin/stochasticgene-mcp status RUN_ID
```

Approval starts a bounded local worker and returns immediately. Julia logs,
requests, responses, approval and status are saved in the run directory. The
worker checks the approved plan, frozen inputs and pinned StochasticGene
version before computing. Altered plans/inputs are rejected. Completed outputs
are standard StochasticGene files under that run's directory.

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
      "args": ["--config", "/absolute/path/to/config.toml", "serve"]
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
- The StochasticGene version is checked before preparation and execution.
  Updating it requires deliberate revalidation: run `setup --version`.
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
