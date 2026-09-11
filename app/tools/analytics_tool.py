import logging
import os
import time
from typing import Any
from dotenv import load_dotenv
import google.auth
from google.adk.tools.data_agent import data_agent_tool
from google.adk.tools.data_agent.config import DataAgentToolConfig

load_dotenv()
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"
os.environ["CLOUDSDK_CONTEXT_AWARE_USE_ECP_HTTP_PROXY"] = "false"

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("PROJECT_ID", "maki-ki-agentic-da-me")
DATA_AGENT_RESOURCE_NAME = os.getenv(
    "DATA_AGENT_RESOURCE_NAME",
    "projects/maki-ki-agentic-da-me/locations/global/dataAgents/agent_22cac6c0-bc15-4662-ae12-0a579930ede7",
)


def _format_table_markdown(data_retrieved: dict[str, Any]) -> str:
  """Converts Data Retrieved dictionary into a clean markdown table."""
  headers = data_retrieved.get("headers", [])
  rows = data_retrieved.get("rows", [])
  if not headers or not rows:
    return ""

  lines = [
      "| " + " | ".join(str(h) for h in headers) + " |",
      "| " + " | ".join(["---"] * len(headers)) + " |",
  ]
  # Limit to 25 rows max for readability
  for row in rows[:25]:
    lines.append("| " + " | ".join(str(val) for val in row) + " |")

  summary = data_retrieved.get("summary", "")
  table_str = "\n".join(lines)
  if summary:
    table_str += f"\n\n*{summary}*"
  return table_str


def cymbal_analytics_tool(query: str) -> str:
  """Executes analytical and natural language questions across structured BigQuery business ledgers.

  This tool connects directly to the BigQuery Conversational Data Agent for
  Cymbal retail operations.
  It supports querying:
    - Real-time intraday POS transactions (pos_transactions_gold)
    - Anomaly and promo abuse alerts (pos_anomaly_alerts)
    - Daily reconciled store inventory & burn-rate ledger
    (gold_inventory_reconciliation_ledger)
    - Historical transactional data for warranty lookup
    (historical_transactional_data)
    - Extracted product warranty policies (warranty_generic_sections_extracted)
    - Cross-cloud AWS S3 checkout logs (silver_pos_transactions)

  IMPORTANT:
    Pass inquiries referencing standardized enterprise business terms (such as
    'Net Transaction Revenue', 'Total On-Hand Inventory', 'Estimated Cover
    Hours',
    'Cashier Manual Override Rate') verbatim without stripping keywords or
    summarization.

  Args:
      query: The natural language analytical query or question to execute
        against BigQuery.

  Returns:
      The analytical findings, SQL query results, or error description.
  """
  max_retries = 3
  base_delay = 1.0

  creds, _ = google.auth.default()
  settings = DataAgentToolConfig()

  for attempt in range(1, max_retries + 1):
    try:
      res = data_agent_tool.ask_data_agent(
          data_agent_name=DATA_AGENT_RESOURCE_NAME,
          query=query,
          credentials=creds,
          settings=settings,
          tool_context=None,
      )
      if res.get("status") == "SUCCESS":
        responses = res.get("response", [])
        output_sections = []

        for step in responses:
          if (
              "text" in step
              and step["text"].get("textType") == "FINAL_RESPONSE"
          ):
            parts = step["text"].get("parts", [])
            output_sections.append("\n".join(parts))
          elif "Data Retrieved" in step:
            tbl_md = _format_table_markdown(step["Data Retrieved"])
            if tbl_md:
              output_sections.append(tbl_md)

        if output_sections:
          return "\n\n".join(output_sections)

        # Fallback if no explicit final response or table was caught
        text_outputs = []
        for step in responses:
          if "text" in step:
            text_outputs.extend(step["text"].get("parts", []))
        if text_outputs:
          return "\n".join(text_outputs)
        return str(responses)
      else:
        error_msg = res.get("error_details", "Unknown error")
        logger.warning(f"Data agent error on attempt {attempt}: {error_msg}")
        if attempt == max_retries:
          return (
              f"Store data is unreachable: {error_msg}. Please verify network"
              " connectivity."
          )
    except Exception as e:
      logger.error(f"Connectivity failure on attempt {attempt}: {e}")
      if attempt == max_retries:
        return (
            "Store data is unreachable due to connectivity failure. Please"
            " verify network connectivity."
        )
    time.sleep(base_delay * (2 ** (attempt - 1)))

  return "Store data is unreachable. Please verify network connectivity."
