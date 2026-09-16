---
name: measure-then-fix-slop
description: "Measure how sloppy a codebase is with the SlopCodeBench metrics: verbosity (duplicated and wasteful lines) and structural erosion (complexity mass trapped in high-complexity functions), placed against published human and agent baselines, plus LOC growth from git. Use when asked how sloppy, bloated, or AI-degraded a repository is, when baselining a repo before a cleanup, when checking whether agent-written code is drifting over time, or when a codebase feels hard to extend and you want evidence. Then fix what sits above the human band and stop there, working from the tool's own duplicate and hotspot lists, and pairing every number with a quality gate so a falling score is never the only evidence. Triggers on: slop, slop score, code slop, AI slop, sloppiness, measure slop, how sloppy, is this sloppy, reduce slop, fix slop, de-slop this repo, verbosity, erosion, structural erosion, code bloat, codebase health, complexity mass, god function, cyclomatic complexity, duplicated code, copy-paste, clone detection, LOC growth, LOC explosion, code quality metric, maintainability signal, SlopCodeBench, SCBench."
---

# Measure slop, then fix it

Give a repository two numbers and a baseline to read them against, then use
them to decide what to fix.

The numbers come from SlopCodeBench. That benchmark exists to catch one
failure: code that passes its tests while getting harder to extend. Tests
cannot see it. A ratio over the whole tree can.

- **Verbosity**: what share of the code is duplicated or wasteful.
- **Erosion**: what share of the codebase's complexity mass sits in
  high-complexity functions.

Both run 0 to 1. Lower is better. Maintained open-source repos average 0.15
verbosity and 0.31 erosion. Agent-written code averages 0.33 and 0.68.

Measuring comes first: report the numbers, name the worst offenders, and stop
there. Fixing is the second phase, and it is opt-in.

## Run it

Always go through the wrapper. It reads the repo and finds which languages are
present. Then it asks `uvx` for exactly the Tree-sitter grammars those languages
need, so nothing lands in the user's project.

```bash
~/.agents/skills/measure-then-fix-slop/scripts/run.sh .
```

Useful variants:

```bash
# machine-readable, for appending to a trend file
~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --json

# one subtree, when only part of the repo is in scope
~/.agents/skills/measure-then-fix-slop/scripts/run.sh lib/

# add the paper author's own CLI, for languages it supports
~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --scb

# weight the growth line toward recent work
~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --since "7 days ago"

# more hotspots than the default ten
~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --top 25

# count every callable, including inline callbacks, instead of named ones only
~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --functions all
```

Run plain `python3 scripts/slop_measure.py` only when Tree-sitter is already
installed. Without it you get SLOC and git growth and nothing else.

The language table is verified with one fixture per language:

```bash
~/.agents/skills/measure-then-fix-slop/scripts/check_languages.sh
```

Adding or fixing a language means one `LANGS` entry plus one fixture. Follow
"Adding a language" in `REFERENCE.md`, then run the checker before trusting the
new entry.

Exit codes: `0` normal, `1` erosion above the agent band, `2` bad input. Do not
treat exit 1 as a failure of the tool.

## Read the report

```
VERBOSITY   0.474   in or above agent band   (3.16x the human baseline)
EROSION     0.534   between human and agent bands   (1.72x the human baseline)
```

Nine lines carry the meaning. Read them before anything else:

1. **The band, not the raw number.** The human and agent bands overlap, so a
   single mid-range reading proves little. What matters is which band the value
   sits in and which way it is moving.
2. **`measured` vs `excluded` SLOC.** If the split line appears, part of the repo
   is outside both metrics. Say so when reporting.
3. **`EXCLUDED <language>`.** The grammar parsed the files but found no
   functions. Either the language table is wrong for that grammar, or the files
   are not valid in the language their extension claims. Both cases would
   otherwise produce a flattering zero, so neither metric includes them. Never
   pass this on quietly. Say the language is unmeasured and offer to look. The
   fix is a `LANGS` entry plus a fixture; see "Adding a language" in
   `REFERENCE.md`, then run `scripts/check_languages.sh`.
4. **`excluded N build artifact(s)`.** Files named like build output were
   dropped, because a bundle reads as one enormous function and would own the
   erosion score. Mention it when reporting.
5. **`note: N file(s) have many long lines`.** Generated or templated code can
   inflate erosion. The tool warns instead of excluding, because a hand-written
   file with inline HTML or SQL looks identical to a generator. Open the file
   before deciding, then `--exclude` it if it is not the team's code.
6. **`NOTE: Tree-sitter is unavailable`.** Nothing was measured. Report that,
   not a score.
7. **The scb-check coverage warning.** `scb-check` skips languages it cannot
   parse and prints a score anyway. Below 60% coverage the number describes a
   subset, not the codebase. Lead with the warning when it fires.
8. **`clone component only`.** Under the bundled analyzer, verbosity omits the
   ast-grep rules, so it is a lower bound. Real verbosity is this value or
   higher. Never present it as if it were the published composite.
9. **`N anonymous callable(s) not counted`.** By default only named callables
   are units, and inline callbacks fold into the function that holds them. This
   line tells you how many such callbacks were folded. Mention it when the
   number is large; `--functions all` counts them separately instead. That mode
   also sees units named mode misses. A command handler written as an object
   literal is not a named callable, and no one is charged for its branches. A
   scope can read clean while a real hotspot sits in that handler. Re-run with
   `--functions all` before you call a scope clean.

## Report it

Lead with the two numbers and their bands, then the evidence behind them. Keep
it short. Something close to this:

> **Slop: verbosity 0.47, erosion 0.53** over 76 SLOC of Python and Ruby.
> Verbosity is above the agent mean (0.33); erosion sits between the human
> (0.31) and agent (0.68) bands. Growth is flat over 30 days.
>
> The erosion comes from one function, `wide` in `simple.py:21`
> (CC 12, 24 SLOC). Three duplicate blocks total 36 lines, the largest pair
> being `app.rb:4` and `app.rb:14` at 9 lines each.

Rules for the writeup:

- Give the band and the comparison, because a bare 0.53 means nothing to a
  reader who does not know the scale.
- Give the count beside the ratio. Erosion is a share, so on a small scope it
  snaps to 0.000 as soon as nothing above the threshold is left. "0 of 380
  functions above CC 10" is the claim the number supports; "0.000, therefore
  clean" is not. A repo-wide reading over the same code can still sit above
  0.03.
- Name files and line numbers for the top two or three offenders. Vague
  findings get ignored.
- State the engine. "Measured with the clone detector only, so verbosity is a
  lower bound" is a different claim from "measured with scb-check."
- State the counting mode when it changes the number. Erosion over named
  callables is a different figure from erosion over every callable. Say which
  one produced the value you are quoting.
- Say what was not measured: excluded languages, unavailable grammars, the
  clone-only caveat, the agent-trailer floor.
- Cite the rule you applied to each fix, by number, from "The fix rules".
- State the target and whether the scope matches it, plus the rule that refused
  any finding still above the baseline. A report that lists only the fixes reads
  as if the number were the goal.
- List what you left alone and the rule that spared it. A run that fixed
  everything it found is a run that skipped the rules.
- Do not call it good or bad. Report the position and let the user judge. If
  asked whether a number is concerning, the honest answer is that a single
  reading is weak evidence and the trend is the signal.

## Comparing over time

One reading is a snapshot. The useful question is whether the number climbs.
When a repo has earlier readings, compare against them; when it does not, offer
to write a baseline the next run can compare against.

```bash
mkdir -p .slop && ~/.agents/skills/measure-then-fix-slop/scripts/run.sh . --json \
  > ".slop/$(date +%F).json"
```

When both a baseline and a current reading exist, report the delta, not just
the level. Erosion rising from 0.31 to 0.42 across two months matters more than
the level itself.

## Scoping

- Default to the repository root. A subtree measurement answers a different
  question, so say which one you ran.
- `--exclude` globs and the `--lang` filter are available for excluding
  vendored code, generated code, fixtures, or migrations that would skew the
  numbers. Excluding things quietly is not acceptable: say what was excluded.
- Exclude generated files and vendored dependencies. They inflate both metrics
  and say nothing about the code the team wrote. Check whether they are
  gitignored first, since the walker already skips ignored files.
- A clone pair that spans a frozen fixture and live source is two instances on
  purpose. A golden master, a backtest corpus, or a characterization snapshot is
  the contract the tests compare against. Sharing code with it removes the
  independence that makes it evidence. Leave the pair and say why.
- The same helper duplicated across independent packages is a real finding with
  an expensive fix. Unifying it means a new shared package and a new dependency
  edge from each one. Name the trade-off in the report and let the user decide,
  rather than coupling packages inside a slop pass.

## Then fix it

Work the report's two lists in order, because they carry different evidence.

The target is to match the human baseline or sit below it, on the scope you
measured: erosion 0.31, verbosity 0.15. A reading at or under those numbers
means the work is done. Both metrics are population means. A scope a little
above one of them is not a finding to hunt down, so judge the band and the
trend, as the report section says.

Past that line, more fixes buy less and cost more. Each one adds a unit to read,
a seam to verify, and a chance to change behavior. The number it moves is already
inside the healthy band. The fix rules refuse most of what is left anyway: a
5-line duplicate pair, a function at CC 11, a clone that spans two independent
packages. Stop and write those down.

Compare like with like. A single package reads higher than the repository that
holds it, because a small corpus concentrates its worst function. Set the
target on the scope you measured, and do not use a repo-wide figure to justify
more work inside one package.

1. **Duplicates first.** A duplicate block is proof that the same thing is
   written in more than one place, so compressing it is safe by construction.
   Rank by `saves`, the line count you get back. This order also pays twice:
   when two eroded functions are near-copies of each other and one concept
   names both, merging them deletes a hotspot. A split would only move that
   complexity. Rule 2 still decides whether the concept is really one.
2. **Eroded functions second, and only with a reason.** Erosion says complexity
   is concentrated; it does not say how to take the function apart. Split it
   when the extracted piece names a step in the problem's own language. Do not
   split it to make the number fall. A helper with six parameters that only
   shortens its caller is worse than the original. The report will still call
   it an improvement.

### The fix rules

The order of operations comes first, because it is the part that gets skipped.
Unit 2 of the course this borrows from teaches one loop: reverse-engineer the old
code into a plain English description at the design level, improve that
description, then forward-engineer it back into code. The metric lists only
nominate candidates for that description. Starting from a hotspot and naming the
pieces afterwards is forward-engineering with the first step missing.

These rules bind every fix. Cite the rule you applied when you report a change,
so a run cannot quietly substitute "the number fell" for one of them.

1. **Write the description first, then make the code match.** The Plain English
   Test is not a check you run on finished units; it is the step that produces
   them. Say what the code is doing in plain English, improve that description,
   then reshape the code until it says the same thing. Put the description in
   the report before the diff.
2. **Extraction from similarity is the named anti-pattern.** Koppel calls it
   anti-unification: finding two lines that look alike and breaking them out
   into a function. A second instance is evidence that a shared concept may
   exist, not a licence to extract. Extract only what the description names, and
   when it names nothing, leave the code alone and record it as left as is. A
   unit is a kind of knowledge (his worked example separates physics, vector
   arithmetic, solar-system data, and graphics), not a phase of one process. One
   verb phrase per unit, no "and", and never a name taken from its position in
   the old function.
3. **Never split to move the number.** A split that only lowers erosion is a
   regression with a better score, and the report will still call it an
   improvement. The rule has to hold before the edit, not after.
4. **Prefer data over a flag.** When two sites differ only in a mode or variant,
   make the variant a table entry or a field. A merged helper that needs a
   boolean, an enum, or a `kind` parameter to tell its callers apart is a failed
   compression, not a finished one.
5. **Measure the helper, not only the caller.** If the new unit sits above the
   threshold, the mass moved instead of leaving.
6. **Keep the lower level reachable.** After adding a unit, name the route a
   caller with an unusual case would take. If there is none, the unit is not
   finished. Write the reason down when you collapse a level on purpose.
7. **State the state.** When a change touches a record whose boolean fields can
   combine into nonsense, give those states one-to-one labels, or write down why
   the looser model is deliberate. An unrepresentable illegal state beats a
   documented one, and a documented one beats a silent one.
8. **Prove the change inert.** Capture behavior before editing, replay the
   capture, and pass the project's own tests after every seam. One seam per
   commit.
9. **Pair every number with its gate.** No figure stands alone, and no design
   claim rests on a figure.
10. **Preserve other actors' work.** `git status` first, stage only your own
    paths by explicit path, and do not commit unless asked.

`REFERENCE.md` has the two techniques that make a stubborn function tractable,
the rule for what makes a split legitimate, and the test for when to stop
instead of splitting. It also records four methods from the course, with the
unit that teaches each. When this list and `REFERENCE.md` disagree, this list
wins and `REFERENCE.md` is the file to fix.

### Which lessons belong here

Only add a lesson when you can name the complexity it removes. Say which, and
cite the unit that teaches it:

- **Work in the rewriting space.** A hotspot comes apart through a chain of small
  steps that keep the same behavior, not one leap. Verify each step, then pick
  the best of the equivalents with the design principles. Unit 5.
- **Collapse the four bad state types.** Junk, missing, redundant, and overloaded
  states all cost branches. Say which one you found on a state-shaped hotspot.
  Unit 3.
- **Make the illegal value impossible, or obvious.** A value that can exist only
  after the required step replaces a guard and a branch with a fact. Unit 3.
- **Name the secret the code failed to hide.** Branch pressure comes from a data
  structure that should be private. Hiding it removes branches at every call
  site. Unit 4.

### Definition of done

A fix is finished only when every line below holds. Put a check or a reason
against each line in the report.

- [ ] Each extraction is named by a plain English description that appears in
      the report, and that description came before the diff.
- [ ] Every new unit passes the Plain English Test.
- [ ] Each new unit was measured; none sits above the threshold.
- [ ] The reading on the measured scope matches the human baseline or sits
      below it, or every finding that remains above it was refused by a named
      rule.
- [ ] The scope was re-measured with `--functions all` before it was called
      clean. Named mode counts only named callables; an object-literal command
      handler at CC 11 hid from one run and showed up in the other.
- [ ] No new duplicate block appeared, and a caller with an unusual case still
      has a route.
- [ ] The behavior capture replays identically and the project's own suite
      passes.
- [ ] Every figure in the report carries its engine, its counting mode, and its
      gate.
- [ ] The report lists what was left alone, with the rule that spared it.
- [ ] Each lesson cited names the complexity it removes, and a state-shaped
      hotspot says which of the four bad state types it had.

### Pair each number with its gate

A falling number is not evidence that the code got better. Three failure modes
lower both metrics while making the codebase worse, so treat each number as half
of a pair: it counts only when its gate also holds.

| Move | Number that falls | Gate that must hold |
|---|---|---|
| Compress a duplicate | verbosity | The two sites really are one thing. If they are two, the merged helper soon needs a flag to tell them apart. |
| Take a function apart | erosion | The extracted piece names a step in the problem's own language, not a local that happened to sit nearby. |
| Delete code | both | You proved it is unreachable, or that the next branch overwrites it. "Nothing calls it" needs a grep, not a hunch. |

Three checks catch most of the damage:

- **Re-measure the helper you created, not only the function you shrank.**
  Complexity counts every guard and every `&&` / `||`. A flattened lookup written
  as eight early returns reads cleanly and scores CC 15, so it needs one more
  split. A helper above the threshold means the mass moved rather than left.
- **Check that the level below still exists.** A new helper can swallow the only
  fine-grained route. That is a granularity hole. Both numbers improve, and the
  next caller who does not fit the helper has to rewrite the call site.
- **Re-measure a suspect hotspot with `--functions all` before touching it.** A
  module wrapper is charged every branch inside it while each inner function is
  also counted, so a file of small private helpers can top the list. When the
  wrapper drops to CC 1 in that mode, there is nothing to fix.

The behavior gate is the one below. Without a capture, an extraction that
reorders two guards passes the tests and changes the program.

### Prove the refactor inert

A slop fix is a refactor, so it must not change behavior. Capture behavior
before you edit anything, then replay the capture after every step.

- Write a throwaway script that drives the target across its branches and dumps
  stdout, stderr, and exit codes to a file. For a server, dump status, headers,
  and body with volatile fields masked.
- Run it against the untouched code first. That file becomes the contract.
- Refactor one seam at a time. After each seam, run the project's full test
  suite and diff the capture. Both must pass before the next seam.
- When the capture fails, work out whether the code or the capture is wrong.
  Never loosen the comparison to make it pass.
- **Snapshot the whole subtree when the baseline is old code.** Comparing one
  file at `HEAD` against your edited copy fails as soon as a sibling module also
  moved: the stale file imports a name the rewritten sibling no longer exports.
  Copy the directory, or the package's `src`, and run both trees side by side. A
  parity run that cannot load the old code is not evidence, and an error there
  looks like a behavior difference until you read it.
- **Diff an extracted helper against the old code, over generated inputs.** Two
  look-alike sites often differ by an argument order or by which value the
  accumulator starts from. A randomized differential between the old and new
  implementations catches that; a before/after capture of the whole program
  catches it only if the corpus happens to reach both orders.

The capture earns its place when the tests are thin. A command-line file can
carry hundreds of behavior-bearing lines behind a handful of tests. Most of its
contract lives in message text and exit codes that no test asserts on.

### Before you edit a shared worktree

- Run `git status` first. Uncommitted changes may belong to someone else.
  Preserve their lines, and verify behavior against the tree as it stands rather
  than against `HEAD`.
- Stage only your own edits, by explicit path. Do not commit unless asked.
- Re-measure when you finish. Confirm the number fell and that no new duplicate
  blocks appeared. Swapping erosion for duplication moves the problem instead
  of fixing it.

For stripping the local tells of agent-written code inside a diff, such as
narrated comments and defensive try-catch, `emil-unslop-code` covers that pass.

For the metric definitions, the derivation of the baselines, the language
table, and the known blind spots, read `REFERENCE.md` beside this file.