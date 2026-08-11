# Contributing

Thank you for considering a contribution. The most useful contributions make a Text-to-SQL behavior easier to reproduce, measure, or trust.

## Before opening an issue

Please search existing issues and include the smallest safe reproduction you can provide:

1. Database schema or a minimal SQLite database. Do not upload sensitive data.
2. Natural-language question and language.
3. Generated SQL, expected SQL or expected result, and actual result/error.
4. Model, embedding model, evaluation profile, and analysis mode.
5. Relevant telemetry such as selected route, retries, validation report, and node timings.

For evaluation regressions, include the command, seed, dataset version, and whether the semantic cache was disabled.

## Pull requests

Keep a pull request focused and explain the user-visible or measured effect. Add a targeted test for behavior changes, especially around SQL safety, schema grounding, retrieval isolation, cache guards, QuerySpec projection contracts, or evaluation metrics.

Run the test suite before submitting:

```bash
pytest -q
```

Do not add API keys, private databases, benchmark gold answers to runtime prompts, or generated evaluation artifacts unless they are explicitly intended for version control.

## Good first contributions

- Add a minimal failing test for a real SQL generation error.
- Improve documentation for a setup or reproducibility gap.
- Add a dataset adapter with clear train/few-shot/test separation.
- Improve schema linking or retrieval diagnostics without hardcoding an answer.
