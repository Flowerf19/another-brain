# Retrieval suite evidence — retsuite-20260804T133909Z

## 1000 rows (4.2 MB)
- sqlite-vec: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 1.46/1.91/2.46 ms | hybrid p95 4.73 ms
- numpy: Recall@5 0.9800, MRR 0.9958, nDCG@10 0.8843 | vector p50/p95/p99 2.48/3.08/3.63 ms | hybrid p95 5.90 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
## 10000 rows (38.6 MB)
- sqlite-vec: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 2.54/3.32/4.06 ms | hybrid p95 11.65 ms
- numpy: Recall@5 0.9700, MRR 0.9958, nDCG@10 0.8837 | vector p50/p95/p99 4.29/5.62/7.77 ms | hybrid p95 14.16 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
## 50000 rows (192.1 MB)
- sqlite-vec: Recall@5 0.9717, MRR 0.9958, nDCG@10 0.8787 | vector p50/p95/p99 9.01/13.79/15.48 ms | hybrid p95 59.11 ms
- numpy: Recall@5 0.9717, MRR 0.9958, nDCG@10 0.8787 | vector p50/p95/p99 14.30/20.89/24.60 ms | hybrid p95 60.29 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
## 100000 rows (382.7 MB)
- sqlite-vec: Recall@5 0.8567, MRR 0.9958, nDCG@10 0.7129 | vector p50/p95/p99 18.52/25.49/34.99 ms | hybrid p95 116.92 ms
- numpy: Recall@5 0.8567, MRR 0.9958, nDCG@10 0.7129 | vector p50/p95/p99 22.88/35.84/40.09 ms | hybrid p95 108.86 ms
- parity: raw<=1e-6 True (max 9.48e-07), exact-canonical False (120 mismatches)
thresholds: [('vector-p95-10000', True), ('vector-p95-50000', True), ('vector-p95-100000', True)]
OVERALL: PASS
