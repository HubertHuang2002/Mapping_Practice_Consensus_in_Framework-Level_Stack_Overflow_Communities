"""pipeline — the per-answer / per-query compute stages (extract, gate, cluster, narrative) plus the
LLM seam, prompts and schemas. Moved from the `probe_d/` scripts; 2a refactors them into callable,
query_id-parameterized stages that the orchestration driver runs."""
