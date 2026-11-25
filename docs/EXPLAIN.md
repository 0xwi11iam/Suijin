# Finding Explain

The finding detail has an **Explain** button. It opens two panes:

1. A copy-ready prompt for another AI: plain explanation, business impact,
   payability, chance of acceptance, and missing information.
2. **Analyse for me** — calls the configured **main leader model** (`model_1`)
   and writes the answer to `~/Desktop/YYYY-MM-DD_explain_<slug>.md`.

There is no alternate model and no heuristic fallback in this path. If the
leader provider is rate-limited, unavailable, or returns no text, the UI shows
the provider's error. The prompt pane can still be copied to another AI.
