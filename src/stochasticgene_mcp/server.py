import asyncio
from typing import Any
from mcp.server import MCPServer
from .service import Service


def create_server(service: Service) -> MCPServer:
    server = MCPServer("StochasticGeneMCP")

    @server.tool(structured_output=True, annotations={"readOnlyHint": True, "openWorldHint": False})
    async def inspect_results(folder: str) -> dict[str, Any]:
        """Inspect trusted StochasticGene results under configured input roots. No fitting or source changes."""
        return await asyncio.to_thread(service.inspect_results, folder)

    @server.tool(structured_output=True, annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def compute_correlations(folder: str, ratetype: str = "ml", trace_center: bool = True,
                                   window_length: int = 216, frame_interval: float = 5/3,
                                   maxlag: float = 120.0, threads: int = 8) -> dict[str, Any]:
        """Prepare ON/reporter correlation inputs and a reproducible plan. Requires separate human CLI approval to execute."""
        return await asyncio.to_thread(service.compute_correlations, folder, ratetype, trace_center,
                                       window_length, frame_interval, maxlag, threads)

    @server.tool(structured_output=True, annotations={"readOnlyHint": True, "openWorldHint": False})
    async def run_status(run_id: str) -> dict[str, Any]:
        """Read persisted local run status and artifact paths. Does not poll a scheduler or start work."""
        return service.run_status(run_id)

    @server.tool(structured_output=True, annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def prepare_fit_rna(data: str, name: str = "gene", G: int = 2, R: int = 0, S: int = 0,
                              insertstep: int = 1, halflife: float | None = None, samplesteps: int = 100000,
                              nchains: int = 2, maxtime: float | None = None, threads: int = 4) -> dict[str, Any]:
        """Prepare a G-state fit to an RNA histogram file under the configured input roots. Halflife is in
        minutes. Preparation only; a person must approve the run with the CLI before it starts."""
        return await asyncio.to_thread(service.prepare_fit_rna, data, name, G, R, S, insertstep, halflife,
                                       samplesteps, nchains, maxtime, threads)

    @server.tool(structured_output=True, annotations={"readOnlyHint": True, "openWorldHint": False})
    async def describe_fit(target: str) -> dict[str, Any]:
        """Return fitted rates (per minute) for a completed run ID or a results folder under the input roots."""
        return await asyncio.to_thread(service.describe_fit, target)

    return server
