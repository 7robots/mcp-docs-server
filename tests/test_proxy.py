"""Tests for NoForwardMCPConfigTransport."""

from __future__ import annotations

import contextlib
import datetime

from fastmcp.client.transports.http import StreamableHttpTransport

from mcp_docs.proxy import NoForwardMCPConfigTransport


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_is_mcp_config_transport_subclass(self):
        from fastmcp.client.transports.config import MCPConfigTransport

        assert issubclass(NoForwardMCPConfigTransport, MCPConfigTransport)

    def test_single_backend_transport_does_not_forward(self):
        """Single-backend branch pre-creates `self.transport`; we force flag off."""
        config = {
            "mcpServers": {
                "only": {"url": "https://example.test/mcp", "transport": "http"},
            }
        }
        transport = NoForwardMCPConfigTransport(config)
        assert isinstance(transport.transport, StreamableHttpTransport)
        assert transport.transport.forward_incoming_headers is False

    def test_multi_backend_config_accepted(self):
        config = {
            "mcpServers": {
                "a": {"url": "https://a.test/mcp", "transport": "http"},
                "b": {"url": "https://b.test/mcp", "transport": "http"},
            }
        }
        transport = NoForwardMCPConfigTransport(config)
        assert len(transport.config.mcpServers) == 2


# ---------------------------------------------------------------------------
# _create_proxy flips forward_incoming_headers back to False
# ---------------------------------------------------------------------------


class TestCreateProxyOverride:
    async def test_flag_is_false_after_create_proxy(self, monkeypatch):
        """After super()._create_proxy, subclass flips forward_incoming_headers=False.

        We stub the base class's _create_proxy to return a transport in the
        `True` state (mimicking what StatefulProxyClient.__init__ does) and
        assert the subclass flips it.
        """
        transport_obj = StreamableHttpTransport(url="https://example.test/mcp")
        transport_obj.forward_incoming_headers = True  # mimic base behavior

        async def fake_super_create_proxy(self, name, config, timeout, stack):
            return transport_obj, object(), object()

        from fastmcp.client.transports.config import MCPConfigTransport

        monkeypatch.setattr(
            MCPConfigTransport, "_create_proxy", fake_super_create_proxy
        )

        subject = NoForwardMCPConfigTransport(
            {"mcpServers": {"x": {"url": "https://x.test/mcp", "transport": "http"}}}
        )
        # Drive the override directly; the base method signature is what
        # MCPConfigTransport.connect_session calls with.
        async with contextlib.AsyncExitStack() as stack:
            t, _c, _p = await subject._create_proxy(
                "x",
                next(iter(subject.config.mcpServers.values())),
                datetime.timedelta(seconds=5),
                stack,
            )

        assert t is transport_obj
        assert transport_obj.forward_incoming_headers is False

    async def test_non_http_transport_not_touched(self, monkeypatch):
        """Sentinel transports (e.g. stdio) without the attribute are left alone."""

        class FakeTransport:
            pass

        fake = FakeTransport()

        async def fake_super_create_proxy(self, name, config, timeout, stack):
            return fake, object(), object()

        from fastmcp.client.transports.config import MCPConfigTransport

        monkeypatch.setattr(
            MCPConfigTransport, "_create_proxy", fake_super_create_proxy
        )

        subject = NoForwardMCPConfigTransport(
            {"mcpServers": {"x": {"url": "https://x.test/mcp", "transport": "http"}}}
        )
        async with contextlib.AsyncExitStack() as stack:
            t, _c, _p = await subject._create_proxy(
                "x",
                next(iter(subject.config.mcpServers.values())),
                None,
                stack,
            )
        assert t is fake
        assert not hasattr(fake, "forward_incoming_headers")
