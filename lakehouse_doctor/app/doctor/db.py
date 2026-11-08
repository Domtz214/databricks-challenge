import os


class SparkSql:
    def __init__(self, spark):
        self.spark = spark

    def query(self, sql, params=None):
        return [r.asDict(recursive=True) for r in self.spark.sql(sql, args=params or {}).collect()]

    def execute(self, sql, params=None):
        self.spark.sql(sql, args=params or {})


class WarehouseSql:
    def __init__(self, warehouse_id=None):
        from databricks.sdk.core import Config

        self.cfg = Config()
        self.http_path = f"/sql/1.0/warehouses/{warehouse_id or os.environ['DATABRICKS_WAREHOUSE_ID']}"

    def _conn(self):
        from databricks import sql

        return sql.connect(server_hostname=self.cfg.host, http_path=self.http_path,
                           credentials_provider=lambda: self.cfg.authenticate)

    def query(self, sql_text, params=None):
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql_text, params or {})
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def execute(self, sql_text, params=None):
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql_text, params or {})
