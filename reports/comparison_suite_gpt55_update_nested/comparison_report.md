# Text-to-Graph Workflow Comparison Report

- Created: 2026-06-04T14:11:33
- Model: `gpt-5.5`
- Base URL: `http://localhost:1455/v1`
- Output: `outputs\comparison_suite_gpt55_update_nested\run_20260604_141133`

This report compares the full iterative workflow against a single-prompt LLM baseline.

## Score Table

| Case | Workflow Score | Single-Prompt Score | Delta | Workflow Success | Single Success | Workflow Iters | Workflow Area/Node | Single Area/Node |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Nested Ring Switchboard | 6.50 | 4.20 | 2.30 | False | False | 4 | 17.92 | 9.51 |

## Nested Ring Switchboard

Prompt: Draw a complex artificial undirected graph called a nested ring switchboard. Create an outer 8-cycle O0-O1-O2-O3-O4-O5-O6-O7-O0 and an inner 8-cycle I0-I1-I2-I3-I4-I5-I6-I7-I0. Add spokes Oi-Ii for every i. Add shifted inner-to-outer chords I0-O3, I1-O4, I2-O5, I3-O6, I4-O7, I5-O0, I6-O1, I7-O2. Add four controller nodes C0,C1,C2,C3 placed as a small central diamond with cycle C0-C1-C2-C3-C0. Connect C0 to I0 and I4, C1 to I1 and I5, C2 to I2 and I6, and C3 to I3 and I7. Add two port nodes P and Q connected to O0,O1 and O4,O5 respectively. Make the nested structure symmetric, readable, and not overly spread out.

- Workflow score: `6.50`; success: `False`
- Single-prompt score: `4.20`; success: `False`

### Workflow

![nested_ring_switchboard Workflow](../../outputs/comparison_suite_gpt55_update_nested/run_20260604_141133/nested_ring_switchboard/workflow/iteration_3/nested_ring_switchboard.png)

### Single Prompt

![nested_ring_switchboard Single Prompt](../../outputs/comparison_suite_gpt55_update_nested/run_20260604_141133/nested_ring_switchboard/single_prompt/nested_ring_switchboard.png)
