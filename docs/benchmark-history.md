# Benchmark run history

These are local development runs on the released recordings. Reusing this public set for development does not establish performance on unseen requests or an organizer ranking.

| Full run | Exact passes | Infrastructure failures | Infrastructure retries |
| --- | ---: | ---: | ---: |
| [released-v4](../evidence/released-v4/report.json) | 55/100 | 0 | not recorded |
| [released-v6](../evidence/released-v6/report.json) | 72/100 | 0 | not recorded |
| [released-v7](../evidence/released-v7/report.json) | 45/100 | 38 | not recorded |
| [released-v8](../evidence/released-v8/report.json) | 74/100 | 0 | 0 |
| [released-v9 — published code](../evidence/released-v9/report.json) | 77/100 | 2 | 5 |

The selected release's source digest is checked against its manifest before packaging. Infrastructure failures stay in the denominator. Retries are allowed only after infrastructure errors, not after a normal failed task.

## Focused checks

The five-recording `targeted-v10` check passed 5/5 after apartment-search and order-ID fixes. It deliberately selected four failures and one control case, so it is not an estimate of a full benchmark score. Earlier focused checks passed 6/10 (`targeted-v9a`) and 3/10 (`targeted-v9b`).

`released-v10` was stopped after 14 of 100 cases (9 passes) when review found that an earlier order ID could override a later O-to-zero correction. Its partial result is not used as a full score. The correction was covered by a new test before `released-v11` started.

`released-v11` was stopped at the participant's request to publish promptly: 14/100 recordings completed, 11 passed. Its experimental code is not the published release. The published code is the fully measured `released-v9` version.
