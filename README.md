# Text-to-Graph Workflow

This project converts graph descriptions into TikZ figures using only the local
GPT-5-compatible API at `http://localhost:1455/v1`. There is no rule-based
extractor, no automatic fallback mode, and no hand-coded special case parser.
If GPT-5 cannot return a valid graph JSON object, generation fails.

## Pipeline

1. Send the input text and optional integer parameters to GPT-5 through the
   OpenAI Python client.
2. Require GPT-5 to return a finite graph JSON object with nodes, edges,
   labels, coordinates, layout hints, and warnings.
3. Validate and lightly repair the graph structure locally.
4. Generate TikZ from the structured graph.
5. Check layout quality with general geometric tests, including node overlap
   and sampled straight or curved edges passing through unrelated nodes.
6. Apply local geometry-aware node-position nudging to separate nodes from
   nearby edges before rendering.
7. Compile the TikZ with `pdflatex` and optionally export PNG with `pdftoppm`.
8. If structure, layout, code generation, or rendering fails, send the concrete
   issues back to GPT-5 and ask for a complete corrected graph JSON object.
9. Save the structured graph, validation result, render result, and iteration
   history as JSON.

## Local API

The default configuration matches the required local service:

```text
base_url = http://localhost:1455/v1
api_key  = sk-
model    = gpt-5.5
```

Health check:

```powershell
python -m text_to_graph.cli health
```

List models:

```powershell
python -m text_to_graph.cli models
```

## Generate

Simple description:

```powershell
python -m text_to_graph.cli generate "There are four nodes A, B, C, and D. A is connected to B and C. C points to D." --output outputs/demo --name proposal_demo
```

LaTeX/math description with a parameter:

```powershell
python -m text_to_graph.cli generate --text-file examples/latex_formula.txt --param m=4 --output outputs/gpt5_formula_m4 --name gpt5_formula_m4
```

## Evaluate

```powershell
python -m text_to_graph.cli evaluate --benchmark examples/benchmark.json --output outputs/eval
```

## Tests

```powershell
python -m unittest discover -s tests
```

## Output Files

For generation, the output folder contains:

- `<name>.tex`: standalone LaTeX source.
- `<name>.pdf`: compiled figure, if `pdflatex` succeeds.
- `<name>.png`: rendered preview, if `pdftoppm` is available.
- `<name>.json`: graph JSON, validation result, render result, and workflow
  history.

## Graph JSON Contract

GPT-5 must return a JSON object shaped like this:

```json
{
  "nodes": [
    {"id": "u_0", "label": "$u_{0}$"},
    {"id": "p", "label": "$p$"}
  ],
  "edges": [
    {"source": "u_0", "target": "p", "directed": false}
  ],
  "layout_hints": {
    "style": "explicit",
    "positions": {
      "u_0": [0, 0],
      "p": [0, 2]
    },
    "edge_options": {}
  },
  "warnings": []
}
```
