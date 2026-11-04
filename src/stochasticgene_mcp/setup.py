"""Create the pinned Julia environment and a configuration file."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

from .service import DEFAULT_ENV, DEFAULT_HOME, DEFAULT_STOCHASTICGENE_VERSION

# Values arrive through ARGS so nothing user-supplied is interpolated into code.
# EnzymeCore is capped because DiffEqBase 6.218 (final release) fails to load its Enzyme
# extension with EnzymeCore >= 0.8.27, printing an error on every Julia start.
INSTALL_SCRIPT = """
using Pkg
mode, value = ARGS
Pkg.add([PackageSpec(name="JSON"), PackageSpec(name="SHA"), PackageSpec(name="EnzymeCore")])
Pkg.compat("EnzymeCore", "0.8.18 - 0.8.26")
if mode == "dev"
    Pkg.develop(path=value)
else
    Pkg.add(name="StochasticGene", version="=" * value)
end
Pkg.instantiate()
using StochasticGene
"""

VERSION_SCRIPT = "using StochasticGene; print(pkgversion(StochasticGene))"


def toml_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))


def write_config(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key} = {toml_value(value)}\n" for key, value in values.items()))


def setup(config: Path, version: str | None, dev: str | None, julia: str | None,
          env: str | None, input_roots: list[str], jobs_root: str | None) -> dict:
    existing = tomllib.loads(config.read_text()) if config.is_file() else {}
    if version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("--version must look like 2.1.0")
    if dev is not None and version is not None:
        raise ValueError("Use either --version or --dev, not both")
    julia_path = julia or existing.get("julia") or shutil.which("julia")
    if not julia_path:
        raise ValueError("Julia was not found; install it or pass --julia")
    julia_path = str(Path(julia_path).expanduser().resolve(strict=True))
    roots = [str(Path(r).expanduser().resolve(strict=True)) for r in input_roots] or existing.get("input_roots")
    if not roots:
        raise ValueError("Pass --input-root at least once (the folder holding your data and results)")
    environment = Path(env or existing.get("julia_project") or DEFAULT_ENV).expanduser().resolve()
    environment.mkdir(parents=True, exist_ok=True)
    wanted = version or (None if dev else existing.get("stochasticgene_version", DEFAULT_STOCHASTICGENE_VERSION))
    mode, value = ("dev", str(Path(dev).expanduser().resolve(strict=True))) if dev else ("registry", wanted)
    subprocess.run([julia_path, "--startup-file=no", f"--project={environment}", "-e", INSTALL_SCRIPT, mode, value],
                   check=True, stdin=subprocess.DEVNULL)
    installed = subprocess.run([julia_path, "--startup-file=no", f"--project={environment}", "-e", VERSION_SCRIPT],
                               check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    if wanted is not None and installed != wanted:
        raise RuntimeError(f"Installed StochasticGene {installed}, expected {wanted}")
    values = dict(existing)
    values.update(input_roots=roots, julia=julia_path, julia_project=str(environment),
                  jobs_root=jobs_root or existing.get("jobs_root") or str(DEFAULT_HOME / "runs"),
                  stochasticgene_version=installed)
    values.pop("expected_revision", None)
    write_config(config, values)
    return {"config": str(config), "stochasticgene_version": installed, "julia_project": str(environment)}
