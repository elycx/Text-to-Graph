# Text-to-Graph Prompt Suite Report

- Created: 2026-06-01T12:28:27
- Model: `gpt-5.5`
- Base URL: `http://localhost:1455/v1`

This report records each challenging prompt, the generated graph structure summary, validation history, and every rendered iteration image.

## Summary

- Output: `outputs\prompt_suite_macro_test\run_20260601_122827`

| Case | Success | Iterations | Visual Score | Nodes | Edges | Min Node Dist | Min Edge Clearance | Errors Seen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hubbed Grid With Teleports | False | 0 | n/a | 0 | 0 | n/a | n/a | 0 |
| Ring of Switches With Local Gates | False | 0 | n/a | 0 | 0 | n/a | n/a | 0 |

## Hubbed Grid With Teleports

Prompt: Draw an artificial undirected graph made from a 3 by 4 grid plus teleports. Grid vertices are g00,g01,g02,g03 on the top row, g10,g11,g12,g13 in the middle row, and g20,g21,g22,g23 on the bottom row. Add all horizontal and vertical grid edges. Add hub node H connected to g01,g02,g11,g12,g21,g22. Add teleport edges g00-g23, g03-g20, g10-g13, and g01-g22. Use bends or an outer route for long teleports so the grid remains legible.

Success: `False`
Error: `GPT-5 macro relayout request failed: HTTPConnectionPool(host='localhost', port=1455): Read timed out. (read timeout=120)`

Iterations:

## Ring of Switches With Local Gates

Prompt: Draw an artificial mixed-looking directed graph, but represent every edge as directed. There are six switch nodes s0,s1,s2,s3,s4,s5 in a directed cycle s0->s1->s2->s3->s4->s5->s0. Each switch si has a local gate gi and output oi, with edges si->gi, gi->oi, and oi->s(i+1 mod 6). Add skip edges g0->s3, g1->s4, g2->s5, g3->s0, g4->s1, g5->s2. Use a circular layout with gates and outputs near their switch, and route skip edges around the outside.

Success: `False`
Error: `GPT-5 request failed: HTTPConnectionPool(host='localhost', port=1455): Read timed out. (read timeout=120)`

Iterations:
