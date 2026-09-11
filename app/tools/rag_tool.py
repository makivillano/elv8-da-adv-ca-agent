import logging
import os
import re
import time
from typing import Any
from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("PROJECT_ID", "maki-ki-agentic-da-me")
TABLE_ID = f"{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings"
SIMILARITY_THRESHOLD = 0.70


def _gcs_to_https(uri: str) -> str:
  """Converts a gs:// URI to a clickable Google Cloud Storage HTTPS link."""
  if not uri:
    return ""
  if uri.startswith("gs://"):
    return uri.replace("gs://", "https://storage.cloud.google.com/")
  return uri


def _clean_search_query(query: str) -> str:
  """Sanitizes text for BigQuery SEARCH() syntax by extracting keywords / error codes."""
  error_codes = re.findall(r"\b[A-Z0-9]+-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", query)
  if error_codes:
    return " ".join(f'"{code}"' for code in error_codes)

  words = re.findall(r"\b[A-Za-z0-9]{3,}\b", query)
  if words:
    return " ".join(f'"{w}"' for w in words[:5])
  return f'"{query.strip()}"'


def pos_troubleshooting_rag_tool(query: str) -> str:
  """Retrieves technical hardware troubleshooting runbooks, diagnostics, and field recovery protocols for in-store POS terminals.

  Searches vector chunk embeddings and runbook documentation for POS hardware
  such as:
    - Toshiba TCx 810
    - HP Engage One Pro
    - NCR Voyix RealPOS XR7
    - Clover Station Solo
    - Diebold Nixdorf BEETLE A1150

  Includes automatic context stitching (N-1 to N+1 adjacent chunks) to provide
  complete field service procedures, wiring diagrams, and certified hardware
  remediation steps.

  Args:
      query: The hardware fault, error code (e.g. ERR-PAY-4001,
        ERR-DN-PRNT-24V), or symptom description.

  Returns:
      Certified troubleshooting runbook procedure with source documentation
      citations and clickable links.
  """
  max_retries = 3
  base_delay = 1.0

  client = bigquery.Client(project=PROJECT_ID)

  vector_sql = f"""
    WITH matched_chunks AS (
      SELECT 
          base.document_filename,
          base.document_title,
          base.equipment_covered,
          base.source_pdf_uri,
          base.chunk_index,
          base.chunk_content,
          ROUND(1 - distance, 4) AS similarity_score
      FROM VECTOR_SEARCH(
          TABLE `{TABLE_ID}`,
          'embedding',
          (SELECT AI.EMBED(@query, endpoint => 'text-embedding-005').result AS embedding),
          top_k => 3,
          distance_type => 'COSINE'
      )
    )
    SELECT
        m.document_filename,
        m.document_title,
        m.equipment_covered,
        m.source_pdf_uri,
        m.chunk_index,
        m.similarity_score,
        STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_procedure
    FROM matched_chunks m
    JOIN `{TABLE_ID}` c
      ON m.document_filename = c.document_filename 
      AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
    GROUP BY 
        m.document_filename,
        m.document_title,
        m.equipment_covered,
        m.source_pdf_uri,
        m.chunk_index,
        m.similarity_score
    ORDER BY m.similarity_score DESC
    """

  # 1. Try Vector Search with Retries
  for attempt in range(1, max_retries + 1):
    try:
      job_config = bigquery.QueryJobConfig(
          query_parameters=[
              bigquery.ScalarQueryParameter("query", "STRING", query)
          ]
      )
      rows = list(client.query(vector_sql, job_config=job_config).result())

      if rows and rows[0].similarity_score >= SIMILARITY_THRESHOLD:
        best_match = rows[0]
        https_url = _gcs_to_https(best_match.source_pdf_uri)
        return (
            f"### Certified Hardware Runbook: {best_match.document_title}\n"
            f"**Equipment Covered:** {best_match.equipment_covered}\n"
            f"**Relevance Score:** {best_match.similarity_score}\n"
            f"**Documentation Source:** [{best_match.document_filename}]({https_url})\n\n"
            "#### Field Recovery & Remediation Procedure:\n"
            f"{best_match.stitched_procedure}"
        )
      break
    except Exception as e:
      logger.warning(f"Vector search attempt {attempt} failed: {e}")
      time.sleep(base_delay * (2 ** (attempt - 1)))

  # 2. Trigger Full-Text SEARCH Fallback
  search_terms = _clean_search_query(query)
  if search_terms:
    text_search_sql = f"""
        SELECT 
            m.document_filename,
            m.document_title,
            m.equipment_covered,
            m.source_pdf_uri,
            m.chunk_index,
            1.0 AS similarity_score,
            STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_procedure
        FROM `{TABLE_ID}` m
        JOIN `{TABLE_ID}` c
          ON m.document_filename = c.document_filename 
          AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
        WHERE SEARCH(m.chunk_content, @search_terms)
        GROUP BY 
            m.document_filename,
            m.document_title,
            m.equipment_covered,
            m.source_pdf_uri,
            m.chunk_index
        LIMIT 1
        """
    try:
      job_config = bigquery.QueryJobConfig(
          query_parameters=[
              bigquery.ScalarQueryParameter(
                  "search_terms", "STRING", search_terms
              )
          ]
      )
      text_rows = list(
          client.query(text_search_sql, job_config=job_config).result()
      )
      if text_rows:
        best_match = text_rows[0]
        https_url = _gcs_to_https(best_match.source_pdf_uri)
        return (
            f"### Certified Hardware Runbook: {best_match.document_title}\n"
            f"**Equipment Covered:** {best_match.equipment_covered}\n"
            f"**Search Fallback Match:** {search_terms}\n"
            f"**Documentation Source:** [{best_match.document_filename}]({https_url})\n\n"
            "#### Field Recovery & Remediation Procedure:\n"
            f"{best_match.stitched_procedure}"
        )
    except Exception as e:
      logger.warning(f"Full-text search fallback failed: {e}")

  # 3. Fallback warning for out-of-scope or uncertified queries
  return (
      "⚠️ Warning: No certified POS hardware documentation found matching this"
      f" query (relevance below {SIMILARITY_THRESHOLD} safety threshold). This"
      " inquiry appears to be out-of-scope for Cymbal POS terminal runbooks."
  )
