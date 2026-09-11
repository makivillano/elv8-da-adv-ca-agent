"""Bigtable MCP Toolset for Cymbal Operations Agent."""

import logging
import os
import shutil
import subprocess
from typing import Any, List, Optional
from dotenv import load_dotenv
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StreamableHTTPConnectionParams

load_dotenv()

logger = logging.getLogger(__name__)

BIGTABLE_MCP_URL = (
    os.getenv("BIGTABLE_MCP_URL")
    or os.getenv("BIGTABLE_MCP_SERVICE_URL")
    or "https://mcp-toolbox-bigtable-508406462285.us-central1.run.app"
)


def get_auth_headers(readonly_context=None) -> dict[str, str]:
    """Generates an OIDC bearer token for authenticating to Cloud Run."""
    env = dict(os.environ)
    env["CLOUDSDK_CONTEXT_AWARE_USE_ECP_HTTP_PROXY"] = "false"
    gcloud_candidates = [
        "/usr/local/google/home/makisig/google-cloud-sdk/bin/gcloud",
        shutil.which("gcloud"),
        "gcloud",
    ]
    for gcloud_bin in gcloud_candidates:
        if gcloud_bin and (gcloud_bin == "gcloud" or os.path.exists(gcloud_bin)):
            try:
                tok = subprocess.check_output(
                    [gcloud_bin, "auth", "print-identity-token"],
                    env=env,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                ).decode().strip()
                if tok:
                    return {"Authorization": f"Bearer {tok}"}
            except Exception:
                continue

    # Fallback to google-auth if gcloud CLI unavailable
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        auth_req = google.auth.transport.requests.Request()
        tok = google.oauth2.id_token.fetch_id_token(auth_req, BIGTABLE_MCP_URL)
        return {"Authorization": f"Bearer {tok}"}
    except Exception as e:
        logger.warning("Unable to fetch OIDC token: %s", e)
        return {}


class ResilientMcpToolset(McpToolset):
    """McpToolset that handles startup connection or auth errors gracefully."""

    async def get_tools(
        self,
        readonly_context: Optional[ReadonlyContext] = None,
    ) -> List[BaseTool]:
        try:
            return await super().get_tools(readonly_context)
        except Exception as e:
            logger.warning(
                "Bigtable MCP server connection error in get_tools: %s. "
                "Returning empty tool list until re-authenticated.",
                e,
            )
            return []


def create_bigtable_mcp_toolset() -> McpToolset:
    """Creates and returns the McpToolset instance for Bigtable."""
    url = f"{BIGTABLE_MCP_URL.rstrip('/')}/mcp"
    return ResilientMcpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url,
            headers=get_auth_headers(),
        ),
        header_provider=get_auth_headers,
    )


bigtable_mcp_toolset = create_bigtable_mcp_toolset()
