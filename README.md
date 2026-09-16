# sloptrack

Measures three signals of structural decay in a codebase. It also ships the
agent skill that says what to fix.

```
verbosity    = |clone lines U ast-grep lines| / SLOC
erosion      = complexity mass in functions with CC > 10 / total complexity mass
granularity  = callables invoked exactly once / callables with a call site
```

Verbosity and erosion come from [SlopCodeBench](https://arxiv.org/abs/2603.24755).
Granularity comes from Casey Muratori's writing on compression-oriented
programming. A callable with a single caller is premature reuse, not a saving.

Granularity is a band, not a floor. Above it is over-decomposition; below it is
too few named steps. Inlining a helper to lower the number moves the same
complexity into its caller and raises erosion.

## Run it

```bash
scripts/run.sh .                    # the current repo
scripts/run.sh . --json             # machine-readable, for a diff or a gate
scripts/run.sh . --top 25           # list more offenders
scripts/run.sh . --functions all    # count anonymous callables too
scripts/run.sh . --scb              # also run scb-check, the reference implementation
```

`run.sh` builds a throwaway `uvx` environment holding only the grammars the
target needs. Plain `python3 scripts/slop_measure.py` works when Tree-sitter is
already installed.

## The skill

`skill/measure-then-fix-slop/` is the agent-facing half. `SKILL.md` is the
workflow. It runs measure, reads the ten lines that matter, fixes by pairs, then
re-measures. `REFERENCE.md` carries the method, the published bands, and the
limits.

Install it by symlinking the directory, so the documented script paths keep
working:

```bash
ln -sfn ~/work/sloptrack/skill/measure-then-fix-slop ~/.agents/skills/measure-then-fix-slop
```

## Verify the grammars

```bash
scripts/check_languages.sh
```

Every language in the `LANGS` table runs against a fixture whose answer is known
ahead of time. That answer covers the callable count, the worst complexity, the
clone span, and the call counts. The check fails when a grammar drifts, and 18
languages pass today.

If you add a language, copy an entry in `scripts/slop_measure.py` and its
fixture in `scripts/check_languages.py`, then run the check. Without that check,
a wrong vocabulary reports a confident, wrong number.

## Bands

| Signal | Human | Agent |
|---|---|---|
| Verbosity | 0.15 ± 0.06 | 0.33 ± 0.10 |
| Erosion | 0.31 ± 0.17 | 0.68 ± 0.20 |
| Granularity | 0.27 ± 0.13 | not measured |

The human figures are the SlopCodeBench panel of 48 maintained Python
repositories at HEAD, re-measured here for granularity. There is no agent band
for granularity because the paper never measured it. As a check on the pipeline,
the analyzer matched the paper's published erosion to within 0.01 on 11 of 12
panel repositories.

Two limits matter before you trust a number.

- Verbosity is the clone component only. The published metric also counts
  ast-grep drops, so treat this reading as a lower bound.
- Some languages parse but yield no callable. Those are left out of the
  aggregate rather than reported, because the vocabulary is probably wrong for
  that grammar.

## Layout

```
skill/measure-then-fix-slop/
  SKILL.md              the workflow
  REFERENCE.md          the method, bands, and limits
  scripts/
    slop_measure.py     the analyzer
    run.sh              uvx wrapper that installs the needed grammars
    check_languages.py  per-language fixture corpus
    check_languages.sh  the checker
```
