# Sloptrack

A CLI that measures the slop in your codebase using three metrics:

- **Verbosity** (SlopCodeBench): the share of SLOC that the clone detector finds
  written in more than one place. Comments and punctuation-only lines are not
  SLOC, and the detector normalizes identifiers away. Two blocks match when their
  shape matches, so `transform(items)` and `convert(rows)` count as one.
- **Erosion** (SlopCodeBench): how much of the codebase's complexity lives in its
  worst functions. A function counts as a hotspot when its cyclomatic complexity
  is above 10. Mass is `CC x sqrt(SLOC)`, so a long simple function cannot
  outweigh a short tangled one.
- **Granularity**: how many callables run from exactly one call site. The idea
  comes from Casey Muratori's writing on *Semantic Compression*. A callable with
  a single caller is premature reuse, not a saving. Granularity is a **band, not
  a floor**. Above the band is over-decomposition; below it is too few named
  steps. Inline every single-use helper and the complexity just moves into its
  caller, which raises erosion.

All three run from 0 to 1. Human-maintained and agent-written code sit in
different bands, so a reading only means something next to a baseline:

| Signal | Human | Agent |
|---|---|---|
| Verbosity | 0.15 ± 0.06 | 0.33 ± 0.10 |
| Erosion | 0.31 ± 0.17 | 0.68 ± 0.20 |
| Granularity | 0.27 ± 0.13 | not measured |

The human figures are the SlopCodeBench panel of 48 maintained Python
repositories at HEAD, re-measured for granularity (44 were measurable). There is
no agent band for granularity because the paper never measured it. The paper is
[arXiv:2603.24755](https://arxiv.org/abs/2603.24755); the reference
implementation is [SprocketLab/slop-code-bench](https://github.com/SprocketLab/slop-code-bench).

# Install

```bash
pipx install git+https://github.com/kennyfrc/sloptrack
# or run it without installing anything
uvx --from git+https://github.com/kennyfrc/sloptrack sloptrack measure .
```

Requires Python 3.10 or newer. Grammar resolution also wants `uvx`, which ships
with [uv](https://docs.astral.sh/uv/). `sloptrack measure` reads the target and
finds the languages in it. It then asks uvx for those Tree-sitter grammars only,
inside a throwaway environment, so nothing lands in your project. Without uvx it
falls back to SLOC and git growth, and says which numbers it could not measure.

# Usage

```bash
sloptrack measure .                      # the current repository
sloptrack .                              # measure is the default command
sloptrack measure . --json               # machine-readable, for a trend file or a gate
sloptrack measure . --top 25             # list more duplicate blocks and hotspots
sloptrack measure . --functions all      # count anonymous callables too
sloptrack measure lib/ --since "7 days ago"   # weight the growth line toward recent work
sloptrack measure . --scb                # also run scb-check, the reference implementation
sloptrack langs                          # supported languages and their grammar packages
sloptrack check-languages                # verify every grammar against a known-answer fixture
```

The report places each number against its band and names the offenders:

```
VERBOSITY   0.474   in or above agent band   (3.16x the human baseline)
            clone lines 36 of 76 SLOC   [human 0.15 +/- 0.06 | agent 0.33 +/- 0.10]
EROSION     0.534   between human and agent bands   (1.72x the human baseline)
            1 of 12 functions have CC > 10   [human 0.31 +/- 0.17 | agent 0.68 +/- 0.20]
GRANULARITY 0.240   within human band   (human 0.27 +/- 0.13)
            6 of 25 used callables invoked once; 19 invoked twice or more
```

Exit `0` means a normal run. Exit `1` means erosion above the agent band, which
is a reading and not a tool failure. Exit `2` means bad input.

There is also an agent skill. Installed, the skill turns a measurement into a
report and a set of fixes. Invoke it as a slash command:

```
/measure-then-fix-slop
```

Measuring comes first and stops there. Report the numbers and name the worst
offenders, then leave the code alone. Fixing is opt-in. It works the duplicate
and hotspot lists in order, and pairs every number with a quality check. That way
a falling score is never the only evidence.

# Install as a Skill

```bash
curl -fsSL https://raw.githubusercontent.com/kennyfrc/sloptrack/main/install.sh | bash
```

From a clone, the same installer takes flags:

```bash
./install.sh                    # copy into ~/.agents/skills
./install.sh --link             # symlink this checkout, so edits are live
./install.sh --dest DIR         # install somewhere other than ~/.agents/skills
./install.sh --force            # replace an existing install
```

With the CLI installed, the installer is a subcommand:

```bash
sloptrack install-skill --force
```

The copy is self-contained. It carries its own analyzer and language checker.
The paths written in `SKILL.md` work without the `sloptrack` command. The
installer prints the path and the command that verifies the install.

# Development

```bash
pip install -e ".[dev]"
pytest -q                       # the grammar tests skip without grammars
sloptrack check-languages       # installs all 18 grammars through uvx and checks them
```

Adding a language means one `LANGS` entry, one fixture in `check_languages.py`,
and a passing `sloptrack check-languages`. Write the `LANGS` entry against the
grammar's `node-types.json`. A wrong vocabulary reports a confident and wrong
number, with no error to warn you. See "Adding a language" in
[REFERENCE.md](src/sloptrack/skill/REFERENCE.md).

# License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Kenn Costales.

Verbosity and erosion are SlopCodeBench metrics (MIT). Granularity follows Casey
Muratori's writing on semantic compression. SlopCodeBench never measured it, so
its band comes from re-measuring the paper's human panel instead.

