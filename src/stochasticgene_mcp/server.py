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

    return server
