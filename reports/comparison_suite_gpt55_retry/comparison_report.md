# Text-to-Graph Workflow Comparison Report

- Created: 2026-06-04T07:58:36
- Model: `gpt-5.5`
- Base URL: `http://localhost:1455/v1`
- Output: `outputs\comparison_suite_gpt55_retry\run_20260604_075836`

This report compares the full iterative workflow against a single-prompt LLM baseline.

## Score Table

| Case | Workflow Score | Single-Prompt Score | Delta | Workflow Success | Single Success | Workflow Iters | Workflow Area/Node | Single Area/Node |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Nested Ring Switchboard | n/a | n/a | n/a | False | False | 0 | n/a | n/a |
| Layered Bus Crossbar | n/a | n/a | n/a | False | False | 0 | n/a | n/a |

## Nested Ring Switchboard

Prompt: Draw a complex artificial undirected graph called a nested ring switchboard. Create an outer 8-cycle O0-O1-O2-O3-O4-O5-O6-O7-O0 and an inner 8-cycle I0-I1-I2-I3-I4-I5-I6-I7-I0. Add spokes Oi-Ii for every i. Add shifted inner-to-outer chords I0-O3, I1-O4, I2-O5, I3-O6, I4-O7, I5-O0, I6-O1, I7-O2. Add four controller nodes C0,C1,C2,C3 placed as a small central diamond with cycle C0-C1-C2-C3-C0. Connect C0 to I0 and I4, C1 to I1 and I5, C2 to I2 and I6, and C3 to I3 and I7. Add two port nodes P and Q connected to O0,O1 and O4,O5 respectively. Make the nested structure symmetric, readable, and not overly spread out.

- Workflow score: `n/a`; success: `False`
- Single-prompt score: `n/a`; success: `False`


## Layered Bus Crossbar

Prompt: Draw a directed artificial crossbar graph with three horizontal buses. The top bus has nodes T0,T1,T2,T3,T4, the middle bus has M0,M1,M2,M3,M4, and the bottom bus has B0,B1,B2,B3,B4. Add directed bus edges Ti->T(i+1), Mi->M(i+1), and Bi->B(i+1) for i=0..3. Add vertical relay edges T0->M0->B0, T2->M2->B2, and T4->M4->B4. Add crisscross transfer edges T0->M2, T1->M3, T2->M4, M0->B2, M1->B3, M2->B4, plus feedback edges B1->M0, B3->M2, and M4->T3 routed outside the buses. Add source S connected to T0,M0,B0 and sink R reached from T4,M4,B4. Keep the buses aligned and compact while routing transfers clearly.

- Workflow score: `n/a`; success: `False`
- Single-prompt score: `n/a`; success: `False`
