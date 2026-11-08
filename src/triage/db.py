"""One tiny SQL interface with two backends, so agent tools and app code run
both in a notebook (Spark) and in a Databricks App (SQL warehouse)."""

from __future__ import annotations

import os
from typing import Any, Protocol


class Sql(Protocol):
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None: ...


class SparkSql:
    """Use inside notebooks / jobs: SparkSql(spark)."""

    def __init__(self, spark):
        self.spark = spark

    def query(self, sql, params=None):
        return [r.asDict(recursive=True) for r in self.spark.sql(sql, args=params or {}).collect()]

    def execute(self, sql, params=None):
        self.spark.sql(sql, args=params or {})


class WarehouseSql:
    """Use inside the Databricks App. Auth comes from the app's service
    principal; DATABRICKS_WAREHOUSE_ID is injected via app.yaml resources."""

    def __init__(self, warehouse_id: str | None = None):
        from databricks.sdk.core import Config

        self.cfg = Config()
        self.http_path = f"/sql/1.0/warehouses/{warehouse_id or os.environ['DATABRICKS_WAREHOUSE_ID']}"

    def _connect(self):
        from databricks import sql

        return sql.connect(
            server_hostname=self.cfg.host,
            http_path=self.http_path,
            credentials_provider=lambda: self.cfg.authenticate,
        )

    def query(self, sql_text, params=None):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql_text, params or {})
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def execute(self, sql_text, params=None):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql_text, params or {})
