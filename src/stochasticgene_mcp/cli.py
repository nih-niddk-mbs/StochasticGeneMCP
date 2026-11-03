import argparse
import json
from .service import Service


def main() -> None:
    parser = argparse.ArgumentParser(description="Local StochasticGene MCP prototype")
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("folder")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("folder")
    prepare.add_argument("--ratetype", default="ml", choices=["ml", "mean", "median", "last"])
    prepare.add_argument("--global-centering", action="store_true")
    prepare.add_argument("--window-length", type=int, default=216)
    prepare.add_argument("--frame-interval", type=float, default=5/3)
    prepare.add_argument("--maxlag", type=float, default=120.)
    prepare.add_argument("--threads", type=int, default=8)
    for name in ("approve", "status"):
        sub.add_parser(name).add_argument("run_id")
    args = parser.parse_args()
    service = Service(args.config)
    if args.command == "serve":
        from .server import create_server
        create_server(service).run()
        return
    if args.command == "inspect":
        result = service.inspect_results(args.folder)
    elif args.command == "prepare":
        result = service.compute_correlations(args.folder, args.ratetype, not args.global_centering,
            args.window_length, args.frame_interval, args.maxlag, args.threads)
    elif args.command == "approve":
        result = service.approve(args.run_id)
    else:
        result = service.run_status(args.run_id)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
