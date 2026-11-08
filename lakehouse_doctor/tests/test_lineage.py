import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from doctor import lineage  # noqa: E402


def test_extract_python_and_sql():
    code = '''
df = spark.read.table("workspace.doctor.orders_clean")
spark.table("workspace.doctor.customers")
df.write.mode("overwrite").saveAsTable("workspace.doctor.daily_revenue_v2")
spark.sql("""CREATE OR REPLACE TABLE workspace.doctor.ltv AS
             SELECT * FROM workspace.doctor.orders_clean o JOIN `workspace`.`doctor`.`customers` c""")
'''
    io = lineage.extract_tables(code)
    assert io["writes"] == {"workspace.doctor.daily_revenue_v2", "workspace.doctor.ltv"}
    assert io["reads"] == {"workspace.doctor.orders_clean", "workspace.doctor.customers"}


def test_neighborhood_blast_radius():
    edges = [
        {"source": "w.d.orders", "target": "w.d.orders_clean", "job_id": 1, "job_name": "ingest", "notebook_path": "a"},
        {"source": "w.d.orders_clean", "target": "w.d.revenue", "job_id": 2, "job_name": "revenue", "notebook_path": "b"},
        {"source": "w.d.orders_clean", "target": "w.d.ltv", "job_id": 3, "job_name": "ltv", "notebook_path": "c"},
        {"source": "w.d.revenue", "target": None, "job_id": 4, "job_name": "dashboard_feed", "notebook_path": "d"},
    ]
    n = lineage.neighborhood(edges, "w.d.orders_clean")
    assert n["written_by_jobs"] == [(1, "ingest")]
    assert n["upstream_tables"] == ["w.d.orders"]
    assert {b["via_job"] for b in n["downstream_blast_radius"]} == {"revenue", "ltv", "dashboard_feed"}


def test_diff_schemas():
    d = lineage.diff_schemas({"a": "decimal(18,2)", "b": "int"}, {"a": "string", "c": "int"})
    assert d == {"added": ["c"], "removed": ["b"], "type_changed": {"a": "decimal(18,2) -> string"}}
