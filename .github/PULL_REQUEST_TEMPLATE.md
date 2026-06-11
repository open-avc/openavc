## Description

<!-- What does this change? For bug fixes, what was broken? Link the related issue if there is one. -->

## How has this been tested?

<!-- Commands run, OS, real hardware or the simulator. -->

## Checklist

- [ ] `ruff check server/ tests/` passes
- [ ] `pytest tests/ --ignore=tests/perf` passes
- [ ] Frontend rebuilt with `npm run build` (if anything under `web/` changed)
- [ ] Docs updated (if user-visible behavior changed)
