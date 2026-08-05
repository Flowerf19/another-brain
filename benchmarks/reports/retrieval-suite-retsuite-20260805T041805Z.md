# Retrieval suite evidence — retsuite-20260805T041805Z

## 10000 rows (38.6 MB)
- sqlite-vec: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 2.81/3.69/4.60 ms | hybrid p95 12.68 ms
- numpy: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 4.38/5.33/6.54 ms | hybrid p95 14.20 ms
- fts5-lexical (backend-independent, unbudgeted): p50/p95/p99 4.90/8.78/9.83 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
thresholds: [('vector-p95-10000', True)]
OVERALL: PASS
