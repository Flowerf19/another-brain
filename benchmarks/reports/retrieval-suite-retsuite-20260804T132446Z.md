# Retrieval suite evidence — retsuite-20260804T132446Z

## 1000 rows (4.2 MB)
- sqlite-vec: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 1.86/1.98/2.30 ms | hybrid p95 862.44 ms
- numpy: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 2.89/3.39/5.35 ms | hybrid p95 866.64 ms
- parity: False (max raw diff 9.48e-07)
## 10000 rows (38.6 MB)
- sqlite-vec: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 2.81/3.42/3.80 ms | hybrid p95 1230.47 ms
- numpy: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 4.52/5.00/5.14 ms | hybrid p95 1246.88 ms
- parity: False (max raw diff 9.48e-07)
thresholds: [('vector-p95-10000', True)]
OVERALL: FAIL
