# Policy RAG retrieval calibration (Phase 3, workstream 3.3, offline)

**Status: PROPOSAL ONLY.** `AI_RAG_MIN_SCORE` is **unchanged** at 0.35. Acceptance targets are **PENDING**: the dataset must first be reviewed by HR and the targets approved.
**Live-model evaluation: NOT RUN.** This measures retrieval only, with real local embeddings. No LLM was called.

## 1. What was run

| Item | Value |
|---|---|
| Date | 2026-10-14 |
| Harness | `backend/evals/rag_calibration.py` (`python -m evals.rag_calibration`) |
| Dataset | `backend/evals/datasets/policy_retrieval_v1.json`, version 1.0.0, review status `PENDING_HR_REVIEW` |
| Corpus | The 24 policies the seed loads: 5 demo files (checksums verified) and 19 one-line policies. Version `seed-2026-10-14` |
| Cases | 80 = 56 answerable + 18 clear negatives + 6 hard negatives, split 40 calibration / 40 holdout |
| Embeddings | Local fastembed 0.8.1, `BAAI/bge-small-en-v1.5` (ONNX, quantised), from the existing model cache |
| Retriever | Production `PolicyRetriever`: hybrid score 0.5 semantic + 0.5 lexical, `top_k=4`, relative cutoff 0.6, chunking 800/120 |
| Isolation | Docker container with `--network none`, read-only repo mount, no database, `HF_HUB_OFFLINE=1` |

Safety rails, all covered by `tests/ai/rag/test_rag_calibration.py`:
- The harness **refuses the fake/hash embedding provider** (`NOT_RUN: real embeddings required`).
- It refuses a changed corpus file (checksum mismatch).
- It writes nothing when refused.
- The threshold is chosen on the calibration split only.

When the model files were missing, the harness also refused instead of downloading (observed with an incomplete cache copy).

## 2. Results (real embeddings)

### Threshold-free ranking

| Metric | Calibration | Holdout |
|---|---|---|
| Recall@4 (expected policy among top 4) | 1.000 | 1.000 |
| Recall@1 | 0.964 | 0.929 |
| MRR | 0.976 | 0.964 |
| Top score, answerable: min / median | 0.488 / 0.779 | 0.443 / 0.725 |
| Top score, clear negative: median / max | 0.331 / 0.447 | 0.303 / 0.411 |
| Top score, hard negative: min / median / max | 0.449 / 0.478 / 0.682 | 0.334 / 0.439 / 0.609 |
| AUROC, answerable vs clear negative | 1.000 | 1.000 |

### Holdout at the current and proposed thresholds

The proposed threshold of **0.475** is the highest balanced score on the calibration split, with ties broken towards abstaining.

| Holdout metric | Current 0.35 | Proposed 0.475 |
|---|---|---|
| Answerable hit rate (expected policy retrieved) | 1.000 | 0.964 |
| False abstention rate | 0.000 | 0.036 (1 of 28: "What happens if I arrive after nine in the morning?", top score 0.443) |
| Wrong-source rate | 0.000 | 0.000 |
| Correct abstention, clear negatives | 0.889 | 1.000 |
| False acceptance, clear negatives | 0.111 (1 of 9) | 0.000 |
| Hard-negative abstention | 0.333 | 0.667 |

### Sweep, both splits

| min_score | Calib hit | Calib correct-abstain | Holdout hit | Holdout correct-abstain |
|---|---|---|---|---|
| 0.350 | 1.000 | 0.556 | 1.000 | 0.889 |
| 0.400 | 1.000 | 0.667 | 1.000 | 0.889 |
| 0.425 | 1.000 | 0.778 | 1.000 | 1.000 |
| 0.450 | 1.000 | 1.000 | 0.964 | 1.000 |
| 0.475 | 1.000 | 1.000 | 0.964 | 1.000 |
| 0.500 | 0.964 | 1.000 | 0.929 | 1.000 |

## 3. Interpretation and limitations

- At the current 0.35, retrieval returns policy context for about 1 in 2 clear off-topic questions in the calibration split (1 in 9 on holdout). The answer layer then has to refuse; it is instructed to answer only from context.
- The data suggest a threshold in the **0.425 to 0.475** range. Each step trades about one false abstention on a paraphrased answerable question for refusing off-topic ones.
- **Hard negatives can't be fixed with a threshold.** These are questions next to a real policy that the policy doesn't answer, such as maternity or bereavement leave, notice period, or WFH days per week. They score like answerable questions (up to 0.68). The answer layer must refuse them ("insufficient evidence"), and that is **NOT verified** without a live model.
- **Small sample:** 28 answerable and 9 clear negatives per split. One case moves a rate by 3.6 or 11 points, so these are not statistically robust estimates.
- The corpus is demo data: 19 of the 24 policies are one sentence long. Real policies will be longer and more overlapping. **Recalibrate on the real corpus** before relying on any threshold.
- Questions were written by engineering, not collected from employees. Review by HR is required (`review_status: PENDING_HR_REVIEW`).

## 4. Decisions needed (no change made)

1. Approve or extend the dataset. HR should review and add real-world phrasings, de-identified.
2. Approve acceptance targets, for example a minimum answerable hit rate and maximum false acceptance on holdout. Targets are deliberately **not** proposed from this run.
3. Decide whether to change `AI_RAG_MIN_SCORE`. The evidence points to 0.425 to 0.475, but only after steps 1 and 2 and a recalibration on the production corpus.

## 5. Reproduce

```bash
cd HRMS-main/backend
AI_EMBEDDING_PROVIDER=fastembed AI_EMBEDDING_CACHE_DIR=/path/to/model-cache python -m evals.rag_calibration
# writes evals/results/rag_calibration.{json,md} (git-ignored). Add --allow-download only to fetch the model
# (a one-time download of model files from Hugging Face; no HR data is sent).
```
