# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Report classifier: keywords vs ML vs LLM
# MAGIC Trains a TF-IDF + logistic regression classifier on HumAID Harvey, compares it with the bilingual
# MAGIC keyword baseline and `ai_classify`, logs everything to MLflow (Experiments), registers the model
# MAGIC in Unity Catalog (Models), and writes `harvey_predictions` for the back-test.
# MAGIC
# MAGIC **Video number:** macro-F1 and life-threat recall for each approach.

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))
import mlflow, numpy as np, pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score, recall_score
from sklearn.pipeline import make_pipeline
from triage import config, taxonomy
from triage.config import fq

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/{spark.sql('select current_user()').first()[0]}/triage")

# COMMAND ----------

df = spark.table(fq("harvey_tweets")).toPandas()
try:  # hand relabels from notebook 02 override HumAID's coarse label
    rel = pd.read_csv(f"{config.REFERENCE_DIR}/humaid/relabels.csv", dtype={"tweet_id": str})
    rel = rel[rel.relabel.isin(taxonomy.CATEGORIES)]
    df = df.merge(rel[["tweet_id", "relabel"]], on="tweet_id", how="left")
    df["label"] = df.relabel.fillna(df.label)
    print(f"applied {rel.shape[0]} relabels")
except FileNotFoundError:
    print("no relabels.csv yet; using mapped HumAID labels")

train = df[df.split.isin(["train", "dev"])]
test = df[df.split == "test"]
LIFE = sorted(taxonomy.LIFE_THREATENING)

def life_recall(y_true, y_pred):
    yt = np.isin(y_true, LIFE); yp = np.isin(y_pred, LIFE)
    return recall_score(yt, yp)

# COMMAND ----------

with mlflow.start_run(run_name="keyword_baseline"):
    pred = [taxonomy.classify_keywords(t).category for t in test.text]
    mlflow.log_metrics({"macro_f1": f1_score(test.label, pred, average="macro"),
                        "life_threat_recall": life_recall(test.label, pred)})

# COMMAND ----------

with mlflow.start_run(run_name="tfidf_logreg") as run:
    model = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                          LogisticRegression(max_iter=2000, class_weight="balanced"))
    model.fit(train.text, train.label)
    pred = model.predict(test.text)
    mlflow.log_metrics({"macro_f1": f1_score(test.label, pred, average="macro"),
                        "life_threat_recall": life_recall(test.label, pred)})
    mlflow.log_text(classification_report(test.label, pred), "classification_report.txt")
    info = mlflow.sklearn.log_model(model, "model", input_example=test.text.head(3).to_frame(),
                                    registered_model_name=fq("urgency_classifier"))
print(classification_report(test.label, pred))

from mlflow import MlflowClient
MlflowClient().set_registered_model_alias(fq("urgency_classifier"), "champion", info.registered_model_version)

# COMMAND ----------

# MAGIC %md ### LLM: `ai_classify` on a test sample (costs tokens; keep the sample modest)

# COMMAND ----------

labels_sql = ", ".join(f"'{c}'" for c in taxonomy.CATEGORIES)
sample = spark.createDataFrame(test.sample(n=min(500, len(test)), random_state=0)[["tweet_id", "text", "label"]])
sample.createOrReplaceTempView("test_sample")
llm = spark.sql(f"SELECT label, ai_classify(text, array({labels_sql})) AS pred FROM test_sample").toPandas()
with mlflow.start_run(run_name="ai_classify"):
    mlflow.log_metrics({"macro_f1": f1_score(llm.label, llm.pred, average="macro"),
                        "life_threat_recall": life_recall(llm.label, llm.pred)})
# TODO(you): compare the three runs in Experiments (select runs -> Compare) and screenshot for the video.

# COMMAND ----------

# MAGIC %md ### Predictions for every Harvey tweet → `harvey_predictions`

# COMMAND ----------

proba = model.predict_proba(df.text)
classes = list(model.classes_)
life_idx = [classes.index(c) for c in LIFE if c in classes]
out = pd.DataFrame({"tweet_id": df.tweet_id, "pred_label": model.predict(df.text),
                    "p_life_threat": proba[:, life_idx].sum(axis=1), "label": df.label})
spark.createDataFrame(out).write.mode("overwrite").saveAsTable(fq("harvey_predictions"))
