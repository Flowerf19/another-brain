# Retrieval suite evidence — retsuite-20260804T133753Z

## 1000 rows (4.2 MB)
- sqlite-vec: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 1.48/1.96/2.12 ms | hybrid p95 4.65 ms
- numpy: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 2.44/2.80/3.04 ms | hybrid p95 5.49 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
## 10000 rows (38.6 MB)
- sqlite-vec: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 2.56/3.04/3.25 ms | hybrid p95 11.41 ms
- numpy: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 4.10/5.54/5.79 ms | hybrid p95 13.04 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
thresholds: [('vector-p95-10000', True)]
OVERALL: PASS
