# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Guidance knowledge base (embeddings + retrieval)
# MAGIC Chunks Red Cross / FEMA / NWS PDFs from `reference/guidance/`, embeds them with
# MAGIC `databricks-gte-large-en` into `guidance_chunks.embedding`. The agent's `search_guidance` tool does
# MAGIC cosine similarity in SQL over that column. If Vector Search is available
# MAGIC (`TRIAGE_USE_VECTOR_SEARCH=true`), the optional cell also builds a delta-sync index.
# MAGIC
# MAGIC Alternative with less code: **Agents → Knowledge Assistant** over the same volume folder.
# MAGIC If you use that, point `search_guidance` at its endpoint instead.

# COMMAND ----------

# MAGIC %pip install -q pypdf
# MAGIC %restart_python

# COMMAND ----------

import glob, hashlib, os, sys
sys.path.append(os.path.abspath("../src"))
from pypdf import PdfReader
from triage import config
from triage.config import fq

CHUNK, OVERLAP = 1200, 200
rows = []
for path in glob.glob(f"{config.REFERENCE_DIR}/guidance/*.pdf"):
    src = os.path.basename(path)
    for page_no, page in enumerate(PdfReader(path).pages, start=1):
        text = " ".join((page.extract_text() or "").split())
        for i in range(0, max(1, len(text)), CHUNK - OVERLAP):
            chunk = text[i:i + CHUNK]
            if len(chunk) > 200:
                cid = hashlib.md5(f"{src}:{page_no}:{i}".encode()).hexdigest()
                rows.append((cid, src, page_no, chunk))
print(len(rows), "chunks")

# COMMAND ----------

spark.createDataFrame(rows, "chunk_id STRING, source STRING, page INT, text STRING").createOrReplaceTempView("chunks")
spark.sql(f"""
MERGE INTO {fq('guidance_chunks')} t
USING (SELECT *, ai_query('{config.EMBEDDING_ENDPOINT}', text) AS embedding FROM chunks) s
ON t.chunk_id = s.chunk_id
WHEN NOT MATCHED THEN INSERT *
""")

# COMMAND ----------

# Optional: only if your workspace has Vector Search (Compute -> Vector Search).
if config.USE_VECTOR_SEARCH:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "databricks-vectorsearch"])
    from databricks.vector_search.client import VectorSearchClient
    vsc = VectorSearchClient(disable_notice=True)
    try:
        vsc.create_endpoint(name=config.VS_ENDPOINT, endpoint_type="STANDARD")
    except Exception as e:
        print("endpoint:", e)
    try:
        vsc.create_delta_sync_index(
            endpoint_name=config.VS_ENDPOINT, index_name=config.VS_INDEX,
            source_table_name=fq("guidance_chunks"), pipeline_type="TRIGGERED",
            primary_key="chunk_id", embedding_source_column="text",
            embedding_model_endpoint_name=config.EMBEDDING_ENDPOINT,
        )
    except Exception as e:
        print("index:", e)
else:
    print("Vector Search off: search_guidance uses SQL cosine similarity over guidance_chunks.embedding")

# COMMAND ----------

from triage.db import SparkSql
from triage.agent.tools import search_guidance
for r in search_guidance(SparkSql(spark), "elderly person trapped in attic with rising water"):
    print(r["source"], r["page"], r["text"][:160], "\n")
