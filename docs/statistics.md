# Significance tests

Each row compares a configuration with No Daemon on per-run values. Difference = (config - no daemon) / no daemon. 95% CI and p-value from Welch's t-test (unequal variances). For time and latency, negative is better. Where a log also has the Full AI policy variants (configurations 8-11), a second table compares them with plain Full AI.

## v3_slowdisk400MBs_think10ms_ballast9GB_chunked_willneed.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 4, 4 | -0.4% | [-0.7%, -0.1%] | 0.021 |
| Full Context-Aware AI | median latency | 4, 4 | +0.8% | [-1.6%, +3.1%] | 0.455 |
| Full Context-Aware AI | p99 latency | 4, 4 | -0.2% | [-3.2%, +2.7%] | 0.854 |
| Full AI, memory throttling OFF | throughput (per run) | 4, 4 | +5.3% | [+0.1%, +10.6%] | 0.048 |
| Full AI, memory throttling OFF | median latency | 4, 4 | -7.6% | [-10.0%, -5.1%] | <0.001 |
| Full AI, memory throttling OFF | p99 latency | 4, 4 | +192.2% | [+189.2%, +195.1%] | <0.001 |

## v3_synthetic_14GB_ballast4GB_chunked_willneed.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 4, 4 | -1.2% | [-4.7%, +2.2%] | 0.397 |
| Full Context-Aware AI | median latency | 4, 4 | +4.7% | [-2.3%, +11.7%] | 0.145 |
| Full Context-Aware AI | p99 latency | 4, 4 | +7.0% | [-5.9%, +19.9%] | 0.221 |
| Full AI, memory throttling OFF | throughput (per run) | 4, 4 | +28.5% | [+24.9%, +32.0%] | <0.001 |
| Full AI, memory throttling OFF | median latency | 4, 4 | -54.0% | [-61.0%, -46.9%] | <0.001 |
| Full AI, memory throttling OFF | p99 latency | 4, 4 | -15.9% | [-30.2%, -1.7%] | 0.034 |

## v3_synthetic_noise30_ballast4GB_chunked_willneed.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 3, 3 | -1.2% | [-3.5%, +1.2%] | 0.243 |
| Full Context-Aware AI | median latency | 3, 3 | +5.4% | [+0.4%, +10.3%] | 0.043 |
| Full Context-Aware AI | p99 latency | 3, 3 | +7.4% | [-17.0%, +31.8%] | 0.449 |
| Full AI, memory throttling OFF | throughput (per run) | 3, 3 | +1.7% | [-0.8%, +4.2%] | 0.137 |
| Full AI, memory throttling OFF | median latency | 3, 3 | -10.9% | [-22.2%, +0.4%] | 0.054 |
| Full AI, memory throttling OFF | p99 latency | 3, 3 | +14.3% | [-9.4%, +38.0%] | 0.134 |

## v6_noise.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | throughput (per run) | 5, 5 | -2.5% | [-7.7%, +2.6%] | 0.289 |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | median latency | 5, 5 | +11.6% | [-6.0%, +29.1%] | 0.165 |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | p99 latency | 5, 5 | +30.5% | [+5.6%, +55.4%] | 0.026 |
| Full Context-Aware AI | throughput (per run) | 5, 5 | +1.7% | [-2.9%, +6.4%] | 0.415 |
| Full Context-Aware AI | median latency | 5, 5 | -11.3% | [-25.2%, +2.7%] | 0.098 |
| Full Context-Aware AI | p99 latency | 5, 5 | +14.6% | [-10.3%, +39.5%] | 0.194 |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | +0.2% | [-4.6%, +5.1%] | 0.923 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | -4.9% | [-19.0%, +9.3%] | 0.450 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | +13.0% | [-11.9%, +38.0%] | 0.244 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | -1.5% | [-6.2%, +3.2%] | 0.485 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | +7.2% | [-7.5%, +22.0%] | 0.290 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | -1.4% | [-12.0%, +9.2%] | 0.772 |

## v6_pressure.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | throughput (per run) | 5, 5 | -0.1% | [-2.2%, +2.0%] | 0.915 |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | median latency | 5, 5 | -3.1% | [-6.7%, +0.6%] | 0.086 |
| No daemon, kernel readahead tuned (read_ahead_kb=8192) | p99 latency | 5, 5 | +28.1% | [+15.4%, +40.9%] | 0.003 |
| Full Context-Aware AI | throughput (per run) | 5, 5 | +29.1% | [+27.2%, +31.1%] | <0.001 |
| Full Context-Aware AI | median latency | 5, 5 | -55.7% | [-59.5%, -52.0%] | <0.001 |
| Full Context-Aware AI | p99 latency | 5, 5 | -8.4% | [-21.1%, +4.4%] | 0.148 |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | +30.2% | [+28.3%, +32.0%] | <0.001 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | -57.8% | [-60.2%, -55.4%] | <0.001 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | -8.6% | [-21.5%, +4.2%] | 0.151 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | +0.8% | [-0.2%, +1.9%] | 0.104 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | -4.7% | [-13.0%, +3.5%] | 0.196 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | -0.3% | [-7.5%, +7.0%] | 0.934 |

## v7_apps_mem300.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | -41.0% | [-44.1%, -37.8%] | <0.001 |
| Full Context-Aware AI | median latency | 5, 5 | +48.3% | [+43.5%, +53.2%] | <0.001 |
| Full Context-Aware AI | p99 latency | 5, 5 | +119.7% | [+101.4%, +138.0%] | <0.001 |
| Full Context-Aware AI | compile time per op | 5, 5 | +23.2% | [+20.3%, +26.1%] | <0.001 |
| Full Context-Aware AI | python time per op | 5, 5 | +44.5% | [+40.4%, +48.7%] | <0.001 |
| Full Context-Aware AI | search time per op | 5, 5 | +109.2% | [+101.0%, +117.4%] | <0.001 |

## v7_apps_mem400.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | -17.4% | [-21.3%, -13.4%] | <0.001 |
| Full Context-Aware AI | median latency | 5, 5 | +14.8% | [+10.5%, +19.1%] | <0.001 |
| Full Context-Aware AI | p99 latency | 5, 5 | +77.4% | [+50.9%, +103.9%] | <0.001 |
| Full Context-Aware AI | compile time per op | 5, 5 | +11.4% | [+8.5%, +14.2%] | <0.001 |
| Full Context-Aware AI | python time per op | 5, 5 | +20.4% | [+16.1%, +24.7%] | <0.001 |
| Full Context-Aware AI | search time per op | 5, 5 | +21.4% | [+18.0%, +24.9%] | <0.001 |

## v7_apps_unlimited.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | -13.7% | [-21.3%, -6.0%] | 0.007 |
| Full Context-Aware AI | median latency | 5, 5 | +12.5% | [+8.3%, +16.8%] | <0.001 |
| Full Context-Aware AI | p99 latency | 5, 5 | +56.3% | [-34.1%, +146.8%] | 0.161 |
| Full Context-Aware AI | compile time per op | 5, 5 | +9.7% | [-0.2%, +19.7%] | 0.053 |
| Full Context-Aware AI | python time per op | 5, 5 | +21.6% | [+7.0%, +36.2%] | 0.014 |
| Full Context-Aware AI | search time per op | 5, 5 | +11.1% | [+2.9%, +19.3%] | 0.018 |

## v8_apps_mem400.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | -10.6% | [-19.2%, -2.0%] | 0.025 |
| Full Context-Aware AI | median latency | 5, 5 | +8.7% | [-0.3%, +17.7%] | 0.056 |
| Full Context-Aware AI | p99 latency | 5, 5 | +27.5% | [+11.0%, +43.9%] | 0.005 |
| Full Context-Aware AI | compile time per op | 5, 5 | +4.3% | [-3.0%, +11.6%] | 0.201 |
| Full Context-Aware AI | python time per op | 5, 5 | +10.5% | [-1.2%, +22.3%] | 0.070 |
| Full Context-Aware AI | search time per op | 5, 5 | +12.3% | [+5.7%, +19.0%] | 0.005 |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | -13.4% | [-22.0%, -4.8%] | 0.010 |
| Full AI + dead-file eviction | median latency | 5, 5 | +12.2% | [+3.2%, +21.2%] | 0.017 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | +28.7% | [+14.4%, +43.0%] | 0.002 |
| Full AI + dead-file eviction | compile time per op | 5, 5 | +7.3% | [+0.0%, +14.5%] | 0.050 |
| Full AI + dead-file eviction | python time per op | 5, 5 | +15.8% | [+4.0%, +27.6%] | 0.017 |
| Full AI + dead-file eviction | search time per op | 5, 5 | +14.2% | [+7.6%, +20.9%] | 0.002 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | -3.1% | [-7.3%, +1.1%] | 0.123 |
| Full AI + dead-file eviction | median latency | 5, 5 | +3.2% | [-0.7%, +7.1%] | 0.096 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | +1.0% | [-10.6%, +12.5%] | 0.851 |
| Full AI + dead-file eviction | compile time per op | 5, 5 | +2.9% | [-0.8%, +6.6%] | 0.108 |
| Full AI + dead-file eviction | python time per op | 5, 5 | +4.8% | [-1.2%, +10.7%] | 0.102 |
| Full AI + dead-file eviction | search time per op | 5, 5 | +1.7% | [-1.4%, +4.7%] | 0.238 |

## v8_pressure.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | +31.6% | [+28.5%, +34.7%] | <0.001 |
| Full Context-Aware AI | median latency | 5, 5 | -59.4% | [-64.9%, -54.0%] | <0.001 |
| Full Context-Aware AI | p99 latency | 5, 5 | -25.5% | [-38.3%, -12.7%] | 0.004 |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | +38.3% | [+34.6%, +41.9%] | <0.001 |
| Full AI + dead-file eviction | median latency | 5, 5 | -60.2% | [-66.2%, -54.2%] | <0.001 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | -33.6% | [-46.5%, -20.8%] | <0.001 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 5, 5 | +30.5% | [+27.5%, +33.6%] | <0.001 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 5, 5 | -57.5% | [-62.9%, -52.1%] | <0.001 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 5, 5 | -25.4% | [-38.3%, -12.5%] | 0.005 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | +5.1% | [+2.7%, +7.5%] | 0.004 |
| Full AI + dead-file eviction | median latency | 5, 5 | -1.9% | [-13.3%, +9.4%] | 0.668 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | -10.9% | [-19.2%, -2.6%] | 0.017 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 5, 5 | -0.8% | [-1.4%, -0.2%] | 0.018 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 5, 5 | +4.8% | [-0.7%, +10.3%] | 0.074 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 5, 5 | +0.2% | [-5.5%, +5.9%] | 0.937 |

## v8_slowdisk_think10.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 4, 4 | -0.1% | [-1.8%, +1.6%] | 0.862 |
| Full Context-Aware AI | median latency | 4, 4 | -37.8% | [-48.4%, -27.1%] | 0.001 |
| Full Context-Aware AI | p99 latency | 4, 4 | +130.9% | [+71.9%, +189.9%] | 0.006 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 4, 4 | +0.6% | [-1.2%, +2.4%] | 0.364 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 4, 4 | -1.8% | [-11.9%, +8.4%] | 0.658 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 4, 4 | +0.2% | [-1.1%, +1.6%] | 0.653 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 4, 4 | +0.7% | [-0.0%, +1.5%] | 0.054 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 4, 4 | +57.8% | [+50.4%, +65.3%] | <0.001 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 4, 4 | -56.6% | [-82.1%, -31.0%] | 0.006 |

## v8_slowdisk_think100.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 4, 4 | +0.7% | [-3.3%, +4.7%] | 0.688 |
| Full Context-Aware AI | median latency | 4, 4 | -9.2% | [-53.8%, +35.4%] | 0.628 |
| Full Context-Aware AI | p99 latency | 4, 4 | -2.1% | [-33.0%, +28.7%] | 0.852 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 4, 4 | -0.1% | [-3.8%, +3.6%] | 0.967 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 4, 4 | -0.5% | [-43.7%, +42.6%] | 0.977 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 4, 4 | +1.7% | [-30.3%, +33.8%] | 0.876 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 4, 4 | -0.8% | [-4.1%, +2.6%] | 0.595 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 4, 4 | +9.6% | [-30.7%, +49.9%] | 0.579 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 4, 4 | +4.0% | [-6.4%, +14.3%] | 0.338 |

## vm2_noise.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | -1.4% | [-3.1%, +0.3%] | 0.091 |
| Full Context-Aware AI | median latency | 5, 5 | +3.0% | [-3.0%, +8.9%] | 0.281 |
| Full Context-Aware AI | p99 latency | 5, 5 | +8.7% | [-0.0%, +17.4%] | 0.051 |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | -1.3% | [-3.2%, +0.6%] | 0.156 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | +3.5% | [-3.2%, +10.2%] | 0.263 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | +5.1% | [-4.8%, +15.0%] | 0.266 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | +0.1% | [-1.6%, +1.8%] | 0.898 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | +0.5% | [-5.3%, +6.4%] | 0.834 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | -3.3% | [-12.4%, +5.7%] | 0.421 |

## vm2_pressure.log

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full Context-Aware AI | throughput (per run) | 5, 5 | +0.6% | [-1.0%, +2.2%] | 0.413 |
| Full Context-Aware AI | median latency | 5, 5 | -4.9% | [-10.7%, +0.9%] | 0.082 |
| Full Context-Aware AI | p99 latency | 5, 5 | -0.6% | [-15.9%, +14.8%] | 0.930 |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | -0.4% | [-2.0%, +1.2%] | 0.576 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | -1.7% | [-8.0%, +4.5%] | 0.535 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | +0.7% | [-14.9%, +16.2%] | 0.922 |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | +6.5% | [+3.4%, +9.6%] | 0.002 |
| Full AI + dead-file eviction | median latency | 5, 5 | -13.8% | [-19.6%, -7.9%] | 0.001 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | +6.5% | [-19.6%, +32.6%] | 0.572 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 5, 5 | +1.0% | [-0.6%, +2.6%] | 0.192 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 5, 5 | -4.3% | [-10.1%, +1.6%] | 0.123 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 5, 5 | -3.3% | [-23.5%, +16.9%] | 0.715 |

Against Full AI:

| Configuration | Metric | n (base, config) | Difference | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| Full AI + precision-aware gate (min precision 0.6) | throughput (per run) | 5, 5 | -1.0% | [-2.4%, +0.4%] | 0.139 |
| Full AI + precision-aware gate (min precision 0.6) | median latency | 5, 5 | +3.3% | [-1.5%, +8.1%] | 0.143 |
| Full AI + precision-aware gate (min precision 0.6) | p99 latency | 5, 5 | +1.2% | [-9.0%, +11.5%] | 0.786 |
| Full AI + dead-file eviction | throughput (per run) | 5, 5 | +5.9% | [+2.8%, +8.9%] | 0.004 |
| Full AI + dead-file eviction | median latency | 5, 5 | -9.3% | [-12.9%, -5.7%] | <0.001 |
| Full AI + dead-file eviction | p99 latency | 5, 5 | +7.1% | [-18.6%, +32.8%] | 0.505 |
| Full AI + adaptive I/O limit (target stall 0.3) | throughput (per run) | 5, 5 | +0.4% | [-1.0%, +1.7%] | 0.532 |
| Full AI + adaptive I/O limit (target stall 0.3) | median latency | 5, 5 | +0.6% | [-3.0%, +4.3%] | 0.697 |
| Full AI + adaptive I/O limit (target stall 0.3) | p99 latency | 5, 5 | -2.7% | [-21.2%, +15.7%] | 0.723 |

