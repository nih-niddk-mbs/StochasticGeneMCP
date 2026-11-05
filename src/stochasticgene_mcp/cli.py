import argparse
import json
import os
from pathlib import Path
import sys

from .service import DEFAULT_HOME, Service

DEFAULT_CONFIG = os.environ.get("STOCHASTICGENE_MCP_CONFIG", str(DEFAULT_HOME / "config.toml"))


def rate_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    first = [c for c in ("parameter", "ml", "mean", "median") if c in rows[0]]
    columns = first + [c for c in rows[0] if c not in first]
    cells = [[str(c) for c in columns]] + [[f"{r[c]:.5g}" if isinstance(r[c], float) else str(r[c]) for c in columns]
                                           for r in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(columns))]
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(line, widths)) for line in cells)


def show(result: dict, as_json: bool) -> None:
    rates = result.get("rates")
    if as_json or not rates:
        print(json.dumps(result, indent=2, allow_nan=False))
        return
    print(f"Results: {result.get('results_directory')}")
    print("Rates are per minute; median is the recommended summary.\n")
    print(rate_table(rates))
    if result.get("burst_files"):
        print(f"\nBurst size files: {', '.join(result['burst_files'])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stochasticgene-mcp",
                                     description="Fit and analyze with StochasticGene from the command line or an MCP host")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"configuration file (default {DEFAULT_CONFIG})")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="install a pinned StochasticGene and write the configuration")
    setup.add_argument("--version", help="StochasticGene version to pin, e.g. 2.1.0 (default: the configured or built-in one)")
    setup.add_argument("--dev", metavar="PATH", help="use a local StochasticGene checkout instead of a release")
    setup.add_argument("--julia", help="path to the Julia executable")
    setup.add_argument("--env", help="Julia environment folder (default ~/.stochasticgene-mcp/julia-env)")
    setup.add_argument("--input-root", action="append", default=[], help="folder holding your data and results (repeatable)")
    setup.add_argument("--jobs-root", help="folder for run records (default ~/.stochasticgene-mcp/runs)")

    sub.add_parser("serve", help="run the MCP server over stdio")

    fit = sub.add_parser("fit-rna", help="fit a G-state model to an RNA histogram")
    fit.add_argument("data", help="histogram file; first column is the number of cells per RNA copy number")
    fit.add_argument("--name", default="gene")
    fit.add_argument("--G", type=int, default=2, help="gene states")
    fit.add_argument("--R", type=int, default=0, help="pre-RNA steps")
    fit.add_argument("--S", type=int, default=0, help="splice sites")
    fit.add_argument("--insertstep", type=int, default=1)
    fit.add_argument("--halflife", type=float, help="mRNA half-life in minutes (default: decay rate 1.0 per minute)")
    fit.add_argument("--samplesteps", type=int, default=100_000)
    fit.add_argument("--nchains", type=int, default=2)
    fit.add_argument("--maxtime", type=float, help="wall-clock limit in seconds (default: 300 or timeout_seconds, whichever is smaller)")
    fit.add_argument("--threads", type=int, default=4)
    fit.add_argument("--yes", action="store_true", help="approve and start immediately")
    fit.add_argument("--wait", action="store_true", help="wait for the fit to finish and print the rates (implies --yes)")
    fit.add_argument("--json", action="store_true", help="print JSON instead of a table")

    traces = sub.add_parser("fit-traces", help="fit a model with a reporter to a folder of live-cell traces")
    traces.add_argument("folder", help="folder of trace files, one frame per row")
    traces.add_argument("--name", default="gene")
    traces.add_argument("--G", type=int, default=2, help="gene states")
    traces.add_argument("--R", type=int, default=1, help="pre-RNA steps (at least 1)")
    traces.add_argument("--S", type=int, default=0, help="splice sites")
    traces.add_argument("--insertstep", type=int, default=1)
    traces.add_argument("--interval", type=float, default=1.0, help="frame interval in minutes")
    traces.add_argument("--halflife", type=float, help="mRNA half-life in minutes (default: decay rate 1.0 per minute)")
    traces.add_argument("--datacol", type=int, default=3, help="column holding intensity (default 3)")
    traces.add_argument("--raw-intensity", action="store_true", help="do not median-subtract and scale each trace")
    traces.add_argument("--samplesteps", type=int, default=10_000)
    traces.add_argument("--nchains", type=int, default=2)
    traces.add_argument("--maxtime", type=float, help="wall-clock limit in seconds (default: 300 or timeout_seconds, whichever is smaller)")
    traces.add_argument("--threads", type=int, default=4)
    traces.add_argument("--yes", action="store_true", help="approve and start immediately")
    traces.add_argument("--wait", action="store_true", help="wait for the fit to finish and print the rates (implies --yes)")
    traces.add_argument("--json", action="store_true", help="print JSON instead of a table")

    describe = sub.add_parser("describe", help="show fitted rates for a run ID or a results folder")
    describe.add_argument("target")
    describe.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="list StochasticGene runs in a results folder")
    inspect.add_argument("folder")

    for name, help_text in (("correlations", "prepare ON/reporter correlations for a coupled results folder"),
                            ("prepare", "same as correlations")):
        prepare = sub.add_parser(name, help=help_text)
        prepare.add_argument("folder")
        prepare.add_argument("--ratetype", default="ml", choices=["ml", "mean", "median", "last"])
        prepare.add_argument("--global-centering", action="store_true")
        prepare.add_argument("--window-length", type=int, default=216)
        prepare.add_argument("--frame-interval", type=float, default=5/3)
        prepare.add_argument("--maxlag", type=float, default=120.)
        prepare.add_argument("--threads", type=int, default=8)
    for name in ("approve", "status"):
        sub.add_parser(name).add_argument("run_id")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "setup":
        from .setup import setup
        result = setup(Path(args.config).expanduser(), args.version, args.dev, args.julia, args.env,
                       args.input_root, args.jobs_root)
        print(json.dumps(result, indent=2))
        return
    if not Path(args.config).expanduser().is_file():
        sys.exit(f"No configuration at {args.config}. Run: stochasticgene-mcp setup --input-root DATA_FOLDER")
    service = Service(args.config)
    if args.command == "serve":
        from .server import create_server
        create_server(service).run()
        return
    if args.command in ("fit-rna", "fit-traces"):
        if args.command == "fit-rna":
            result = service.prepare_fit_rna(args.data, args.name, args.G, args.R, args.S, args.insertstep, args.halflife,
                                             args.samplesteps, args.nchains, args.maxtime, args.threads)
        else:
            result = service.prepare_fit_traces(args.folder, args.name, args.G, args.R, args.S, args.insertstep,
                                                args.interval, args.halflife, args.samplesteps, args.nchains,
                                                args.maxtime, args.threads, not args.raw_intensity, args.datacol)
        if args.yes or args.wait:
            service.approve(result["run_id"])
            result = service.wait(result["run_id"]) if args.wait else service.run_status(result["run_id"])
        show(result, args.json)
        if result.get("state") == "failed":
            sys.exit(1)
        return
    if args.command == "describe":
        show(service.describe_fit(args.target), args.json)
        return
    if args.command == "inspect":
        result = service.inspect_results(args.folder)
    elif args.command in ("prepare", "correlations"):
        result = service.compute_correlations(args.folder, args.ratetype, not args.global_centering,
            args.window_length, args.frame_interval, args.maxlag, args.threads)
    elif args.command == "approve":
        result = service.approve(args.run_id)
    else:
        result = service.run_status(args.run_id)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
