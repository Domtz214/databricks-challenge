"""Triage: predictive disaster-response triage on Databricks.

Pure-Python core shared by the Lakeflow pipeline, notebooks, agent and app.
Nothing in this package imports pyspark at module level, so it is unit-testable
locally (`pytest`) and importable from a Databricks App.
"""

__version__ = "0.1.0"
