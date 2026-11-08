# Databricks notebook source
# MAGIC %md
# MAGIC # 99 · Update from GitHub (one click instead of re-importing the ZIP)
# MAGIC Downloads the branch from GitHub and overwrites this project's workspace files in place through the
# MAGIC Workspace API (no Git folder needed), then refreshes the `code` volume the pipeline imports from.
# MAGIC
# MAGIC **Run all** whenever new commits are pushed. Files deleted in the repo are not deleted here.
# MAGIC Local edits you made in the workspace to tracked files **are overwritten**. Commit them to GitHub first.

# COMMAND ----------

dbutils.widgets.text("branch", "claude/relaxed-babbage-apg311")
dbutils.widgets.text("repo", "Domtz214/databricks-challenge")

# COMMAND ----------

import io, os, posixpath, shutil, zipfile
import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat

branch, repo = dbutils.widgets.get("branch"), dbutils.widgets.get("repo")
nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
ROOT = posixpath.dirname(posixpath.dirname(nb_path))          # .../databricks-challenge
SELF = posixpath.relpath(nb_path, ROOT)                        # notebooks/99_update_from_github
SKIP_PREFIXES = (".git", ".github", "tests/", ".gitignore")
print("project root:", ROOT)

r = requests.get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", timeout=120)
r.raise_for_status()
zf = zipfile.ZipFile(io.BytesIO(r.content))
print("downloaded", len(r.content) // 1024, "KB,", len(zf.namelist()), "entries")

# COMMAND ----------

w = WorkspaceClient()
updated, skipped = [], []
made_dirs = set()
for name in zf.namelist():
    rel = name.split("/", 1)[1] if "/" in name else ""   # strip "<repo>-<branch>/" prefix
    if not rel or name.endswith("/") or rel.startswith(SKIP_PREFIXES) or "__pycache__" in rel:
        continue
    if os.path.splitext(rel)[0] == SELF:                   # don't overwrite the running notebook
        skipped.append(rel); continue
    target = posixpath.join(ROOT, rel)
    parent = posixpath.dirname(target)
    if parent not in made_dirs:
        w.workspace.mkdirs(parent); made_dirs.add(parent)
    # AUTO: .py files that start with "# Databricks notebook source" become notebooks
    # (extension dropped, same as the ZIP import); everything else becomes a plain file.
    w.workspace.upload(target, io.BytesIO(zf.read(name)), format=ImportFormat.AUTO, overwrite=True)
    updated.append(rel)
print(f"updated {len(updated)} files; skipped {skipped}")

# COMMAND ----------

# Refresh the copy of src/triage that the pipeline imports (written straight from the ZIP).
CATALOG, SCHEMA = "workspace", "triage"   # keep in sync with src/triage/config.py
dst_root = f"/Volumes/{CATALOG}/{SCHEMA}/code"
shutil.rmtree(f"{dst_root}/triage", ignore_errors=True)
n = 0
for name in zf.namelist():
    rel = name.split("/", 1)[1] if "/" in name else ""
    if rel.startswith("src/triage/") and not name.endswith("/") and "__pycache__" not in rel:
        out = posixpath.join(dst_root, rel[len("src/"):])
        os.makedirs(posixpath.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(zf.read(name))
        n += 1
print(f"synced {n} files -> {dst_root}/triage")
print("Restart Python in other open notebooks (or reattach) so they import the new code.")
