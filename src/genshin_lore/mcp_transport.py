"""MCP 2.1.1 transport compatibility, isolated from evidence and legacy providers.

The public stdio server timed out at initialize in the execution environment;
retain the proven pipe transport until a replacement passes protocol tests.
SDK private structures are confined to this module and tested under the lock.
"""
from __future__ import annotations

import asyncio
import functools
from importlib.metadata import version
import sys
from typing import Any

import anyio
from mcp import types as mcp_types
from mcp.server import MCPServer
from mcp.server.stdio import _claim_fd, _open_stdin_diversion, _open_stdout_diversion
from mcp.shared.message import SessionMessage


def _check_sdk():
    if version("mcp") != "2.1.1":
        raise RuntimeError("stdio compatibility requires mcp==2.1.1; requalify before upgrading")


async def _run_pipe_stdio(server: MCPServer) -> None:
    """Use asyncio pipe I/O while retaining MCP's fd claim/diversion rules."""
    _check_sdk()
    stdin_buffer, restore_stdin = _claim_fd(0, sys.stdin, "rb", _open_stdin_diversion)
    stdout_buffer, restore_stdout = _claim_fd(1, sys.stdout, "wb", _open_stdout_diversion)
    try:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, stdin_buffer)
        write_protocol = asyncio.streams.FlowControlMixin(loop=loop)
        write_transport, _ = await loop.connect_write_pipe(lambda: write_protocol, stdout_buffer)
        writer = asyncio.StreamWriter(write_transport, write_protocol, None, loop)
        incoming_send, incoming = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        outgoing, outgoing_receive = anyio.create_memory_object_stream[SessionMessage](0)

        async def read_wire() -> None:
            async with incoming_send:
                while line := await reader.readline():
                    try:
                        message = mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                        await incoming_send.send(SessionMessage(message))
                    except Exception as exc:
                        await incoming_send.send(exc)

        async def write_wire() -> None:
            async with outgoing_receive:
                async for message in outgoing_receive:
                    wire = message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    writer.write((wire + "\n").encode("utf-8"))
                    await writer.drain()

        async with anyio.create_task_group() as group:
            group.start_soon(read_wire)
            group.start_soon(write_wire)
            await server._lowlevel_server.run(incoming,outgoing,server._lowlevel_server.create_initialization_options())
            group.cancel_scope.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except NotImplementedError:
            # asyncio's write-pipe protocol has no close waiter on Python 3.12.
            pass
    finally:
        if restore_stdout is not None:
            restore_stdout()
        if restore_stdin is not None:
            restore_stdin()


def _avoid_broken_anyio_worker_threads(server: MCPServer) -> None:
    """Keep sync MCP handlers responsive in the supported AnyIO environment."""
    _check_sdk()
    for tool in server._tool_manager._tools.values():
        if tool.is_async:
            continue
        original = tool.fn

        @functools.wraps(original)
        async def call_inline(_original=original, **kwargs: Any) -> Any:
            return _original(**kwargs)

        tool.fn = call_inline
        tool.is_async = True

