# Reference

Background for the numbers in a slop report: where they come from, how to read
them, and what they cannot tell you.

## The two metrics

Both come from SlopCodeBench. Both run from 0 to 1, and lower is better.

### Verbosity: how much of the code is redundant

```
verbosity = |clone lines ∪ ast-grep flagged lines ∪ structural lines| / SLOC
```

The numerator is a union, so a line counted twice still counts once. SLOC here
means source lines of code, and the rule is specific: strip comments, then drop
lines that are blank or hold nothing but punctuation. A lone `}` or `);` is
layout, not code. A line of code with a trailing comment still counts.

Two components feed the union:

- **Clone detection.** Structurally identical subtrees, with identifiers and
  literals normalized away, so `transform(items)` and `convert(rows)` match if
  their bodies match. A block qualifies only if it holds at least two
  statements and the duplicate spans at least a few lines.
- **ast-grep rules.** 137 hand-written patterns for wasteful code: trivial
  wrappers, hand-rolled standard operations, narration comments, redundant
  renames in exception handling. These are heuristics, and the paper says so.

### Erosion: how much complexity sits in a few functions

```
mass(f) = CC(f) × √SLOC(f)
erosion = Σ mass(f) for CC(f) > 10  ÷  Σ mass(f) for all f
```

`CC` is cyclomatic complexity: 1 plus the number of branch points (each `if`,
`case`, loop, `catch`, boolean operator). The square root on SLOC keeps a long
but simple function from dominating a short but tangled one. The threshold is
strict: a function qualifies at CC 11, not CC 10.

How nested callables are attributed is the subtlest part of the metric, and the
default follows the reference implementation:

- **Default (`--functions named`).** Only named callables are units. An
  anonymous callback passed straight into a call is not a function of its own,
  and its branches fold into the nearest named function that holds it. That
  function carries the reading cost, so it carries the complexity.
- **`--functions all`.** Every callable becomes a unit, including inline
  callbacks. Then no nested function may also raise its parent's CC, or every
  branch would be counted twice.

Getting the pairing wrong is not a rounding error. It once made a single file
score 0.82 where the reference scored 0.47. A wrapper holding thirty inline
callbacks and no branches of its own is CC 1 under `all` and CC 30 under
`named`, and both are defensible as long as you know which one you ran.

Erosion is a ratio, so it answers "what share of this codebase's complexity
lives in its worst functions." A repo of 500 small clean functions and one
1200-line monster scores high even though most of the code is fine.

The paper also reports **cognitive erosion**, which swaps cyclomatic complexity
for cognitive complexity. Cognitive complexity adds nesting depth, so a deeply
nested function scores higher than a flat one with the same branch count.

### Granularity: how much of the code is single-use callables

```
granularity = callables invoked exactly once ÷ callables with at least one call
```

A use is a call site: `foo()` or `obj.foo()`. A name that is merely mentioned,
such as a callback passed by name or a local variable that shares the name, is
not a use, and neither is recursion. Callables with no call site are listed
separately and kept out of the ratio: they are entry points, public API, or dead
code, and the metric cannot tell which.

This is the lower bound erosion lacks. A high erosion reading says complexity is
concentrated; a high granularity reading says the opposite failure, complexity
scattered into helpers with one caller each. Both are shapes of the same
problem, and the healthy answer is a band, not a floor. Inlining every
single-use helper raises erosion and moves the same complexity into its caller.

The rule comes from Casey Muratori's "Semantic Compression" and "Complexity and
Granularity". His "at least two instances" rule governs reuse, not naming: pull
out shared code when a second instance appears, but naming a step is legitimate
at one call. A split is a level of granularity, and it is fine as long as "you
just don't delete the smaller pieces as you build bigger ones." A single-use
callable is a candidate to review against that test, not a defect.

The human band was derived the way SlopCodeBench derived its own. The paper
measures a panel of 48 maintained Python repositories at HEAD, grouped by GitHub
stars into Niche (<1k), Established (1k-10k), and Major (>10k), with
documentation and generated code excluded. The same panel was cloned at HEAD and
measured with the bundled analyzer for granularity. Forty-four of 48 were
measurable (two names did not resolve to a repository, and two have no Python at
HEAD):

| Tier | n | Granularity | Erosion (this run) |
|---|---|---|---|
| Niche | 6 | 0.47 ± 0.19 | 0.40 ± 0.36 |
| Established | 11 | 0.24 ± 0.09 | 0.29 ± 0.14 |
| Major | 27 | 0.24 ± 0.08 | 0.32 ± 0.15 |
| All | 44 | 0.27 ± 0.13 | 0.32 ± 0.19 |

The erosion column reproduces the paper's panel closely (paper: Niche 0.39,
Established 0.26, Major 0.31, all 0.31), which is the check that the pipeline
matches. Granularity has no agent band: SlopCodeBench did not measure it.

Two checks on this table, run later and reproducible:

- **The analyzer reproduces the paper's per-repo erosion.** On 12 panel repos
  re-measured independently, the bundled analyzer matches Table 7 within 0.01
  on 11 of them. `click` is the one outlier (this run 0.28, paper 0.34).
- **The Major tier figure re-measured.** Those 12 repos are all Major tier, and
  they gave granularity 0.25 ± 0.08 against the 0.24 ± 0.08 recorded above.

Treat the band as approximate to about ±0.05. It is a reference for placing a
number, not a threshold to certify against.

## Published baselines

These are population means, not pass/fail lines. Use them to place a number,
not to grade a repo.

| Signal | Maintained open-source repos | Agent-written code |
|---|---|---|
| Verbosity | 0.15 ± 0.06 | 0.33 ± 0.10 |
| Erosion | 0.31 ± 0.17 | 0.68 ± 0.20 |
| Granularity | 0.27 ± 0.13 | not measured |

The author of the linked post also measured his own vibe-coded projects: up to
0.4 verbosity and 0.75 erosion. Since the two bands overlap, a single reading
near the middle is weak evidence. A reading above the agent mean, or a series
of readings that climb, is not.

A cleanup turns the human row into a stopping rule rather than a grade: match it
or sit below it, then stop. `SKILL.md` states the target and the reasoning. The
numbers still say nothing about whether code is good, and a scope below the
baseline is a reason to stop working, not a score to defend.

Granularity is the exception. Its row is a band in both directions: below it is
not an improvement. Read it as "inside the human range", not "under the line".

## What the benchmark found

From 20 problems, 93 checkpoints, and 11 models:

- No model solved a problem end to end. The best strict checkpoint solve rate
  was 17.2%.
- Erosion rose in 80% of trajectories. Verbosity rose in 89.8%.
- Agent code came out roughly 2.2x more verbose and 2x more eroded than
  maintained repositories.
- Tracked over time, human repositories stayed flat while agent code got worse
  with each iteration.
- "Anti-slop" and "plan-first" prompts lowered the starting point but did not
  flatten the curve. Quality still drifted down.

This is why a passing test suite hides the problem. An agent that only has to
satisfy the current tests will bolt another branch onto the function that
already exists, because that is cheaper than reopening the design. Each choice
is locally reasonable. The aggregate is a codebase nobody wants to extend.

## Three engines, in order of authority

The report always names which one produced the numbers.

1. **`scb-check`** (`--scb`). The paper author's own CLI. Authoritative,
   because it carries the 137 ast-grep rules and the exact tree-sitter
   vocabulary used in the paper. Supports Python, Rust, JavaScript,
   TypeScript, Zig, Haskell, and C++. Run with `--scb`; it needs network
   access the first time because `uvx` fetches it from git.
2. **The bundled analyzer.** Same formulas, independent implementation, 18
   languages. This one covers Ruby, Go, Java, C#, PHP, Kotlin, Swift, Scala,
   Lua, and Bash, which `scb-check` cannot parse. It measures the clone
   component of verbosity only, so its verbosity is a **lower bound**: a
   codebase with lots of ast-grep-style waste and little duplication will read
   lower here than under `scb-check`.
3. **Fallback.** When no grammar loads, the report still gives SLOC, file
   inventory, and git growth. Both quality metrics come back as `n/a`. That is
   deliberate. A number nobody measured is worse than no number.

Verification is two-way and exact. On a Python fixture the two engines agree on
every field (erosion 0.327, 8 functions, total mass 179.79, 18 clone lines, 102
SLOC). On a 150-line hand-written JavaScript file from a real repo they agree
function by function: all 23 callables with identical CC, identical SLOC, and
therefore identical mass and erosion (0.4686). Re-run both after touching the
language table.

One place the engines deliberately differ. `scb-check`'s JavaScript config lists
`function` among its function node types, but `function` is always an anonymous
keyword token in that grammar, never a named node. The real node is
`function_expression`. The reference therefore never counts `const f = function
() {}` as a function. This tool counts it, so expect a small count difference on
code that uses function expressions rather than arrow functions.

## Languages

The analyzer reads the repo, finds which languages are present, and asks `uvx`
for only the grammars it needs.

| Language | Grammar package | Verbosity | Erosion |
|---|---|---|---|
| Python | `tree-sitter-python` | clone only | yes |
| JavaScript / TypeScript | `tree-sitter-javascript`, `tree-sitter-typescript` | clone only | yes |
| Ruby | `tree-sitter-ruby` | clone only | yes |
| Go | `tree-sitter-go` | clone only | yes |
| Rust | `tree-sitter-rust` | clone only | yes |
| Java | `tree-sitter-java` | clone only | yes |
| C / C++ | `tree-sitter-c`, `tree-sitter-cpp` | clone only | yes |
| C# | `tree-sitter-c-sharp` | clone only | yes |
| PHP | `tree-sitter-php` | clone only | yes |
| Kotlin, Swift, Scala, Lua | `tree-sitter-kotlin`, `-swift`, `-scala`, `-lua` | clone only | yes |
| Haskell, Zig | `tree-sitter-haskell`, `tree-sitter-zig` | clone only | yes |
| Bash | `tree-sitter-bash` | clone only | yes |

The script refuses to report a metric when a language parses but yields zero
functions. That combination means the node types in the table are wrong for
that grammar, and a wrong table would otherwise show up as a confident,
flattering zero. It reports `EXCLUDED <language>` instead. Do not silence that
warning without fixing the table.

### Adding a language

One entry in the `LANGS` table in `scripts/slop_measure.py`, plus one fixture in
`scripts/check_languages.py`. The reference CLI carries a parser config per
language and states the same duties for a new one: config, dispatch mapping,
language value, discovery, clone config, tests, and docs. Read its configs when
a grammar is unclear. They are short and use the same node vocabulary this table
carries.

| Field | What it decides | How to get it right |
|---|---|---|
| `exts`, `filenames` | which files are measured | Extensions only. A file that matches nothing is skipped, not reported. |
| `grammar`, `grammar_fn` | which module is imported | The package must exist on PyPI. `run.sh` installs grammars by name, and a name that cannot resolve fails the whole run. `tree-sitter-elm` is not published there, so Elm is skipped rather than measured. |
| `functions`, `function_parents` | what counts as a callable | Names are read from the `name`, `declarator`, or `pattern` field. Check the fixture output: a name that looks like a line of code means the field is wrong. Nodes with no body are declarations and are dropped. |
| `decisions`, `binary_like`, `bool_tokens` | what adds a branch point | Branch and loop nodes both live in `decisions`. A chain of `elif` clauses counts per clause; a `switch` counts per case label. |
| `clone_types` | which nodes the clone detector hashes | Defaults to `functions \| decisions`. Set it when that merge enrolls a node that is part of a clone, not a clone unit (`elif_clause`, `case_item`). |
| `identifiers`, `literals` | which leaves the hash normalizes away | Two same-shaped blocks must hash equal when their names and constants differ. Copy node names from the grammar's `node-types.json`, then prove it on the fixture. |

Then run the checker:

```bash
~/.agents/skills/measure-then-fix-slop/scripts/check_languages.sh
~/.agents/skills/measure-then-fix-slop/scripts/check_languages.sh --lang bash --verbose
```

The fixture is two copies of one function with a branch and a loop inside. It
must yield a named function, complexity above the base, a function-level clone
group, and the same group after one copy's numbers change. The last check is
what proves `literals` is right; a passing name check alone does not. Do not add
a table entry without a fixture: the checker fails the run for any `LANGS` entry
with no sample.

All 18 grammars install together under `tree-sitter==0.25.2` and parse their
fixtures. Treat the checker as load-bearing. Its first run found three table
bugs: Zig spelled its decision nodes `if_expression` and `for_expression` where
the grammar says `if_statement` and `for_statement`, so every Zig function read
as CC 1; Kotlin declared `simple_identifier` and `integer_literal` where the
PyPI grammar says `identifier` and `number_literal`, so nothing normalized and
no clone matched; and Go nests `block` inside `statement_list`, which hid every
clone candidate until the statement scan unwrapped a chain of body nodes instead
of a single one.

Sources: the paper is <https://arxiv.org/abs/2603.24755>, the benchmark runner
and its metric docs are <https://github.com/SprocketLab/slop-code-bench>, and
the reference CLI with the per-language configs is
<https://github.com/gabeorlanski/scb-check> (`docs/tree-walking.md`,
`src/scb_check/tree_walking/languages/`,
`tests/tree_walking/test_multilanguage.py`).

## Reading a report

- A metric is `n/a` when nothing could be parsed. Check the `NOTE` and
  `EXCLUDED` lines before reading anything else.
- `excluded N build artifact(s)` lists files named like build output
  (`app.min.js`, `vendor.bundle.js`). They are dropped by default because a
  bundle reads as one enormous function and would own the erosion number. Only
  the filename decides this, never the content.
- `note: N file(s) have many long lines` lists files where over 5% of lines
  exceed 300 characters. That suggests generated or templated code, but it is
  not proof: hand-written files with inline HTML, SVG, or SQL trip it too. The
  tool warns instead of excluding, because guessing wrong here silently removes
  real source. Decide by looking at the file, then `--exclude` it if it is not
  yours.
- `total SLOC` shows a measured/excluded split when the repo mixes languages
  the analyzer can and cannot parse. The excluded part is not in either metric.
- The `--scb` coverage line matters. `scb-check` silently skips files whose
  language it does not support and still prints a score. On a Ruby repo it
  measures whatever JavaScript and Python it finds and reports that as the
  codebase. The analyzer prints a warning below 60% coverage and says so in
  plain words: the score describes the files it can parse, not the codebase.
- The `AGENT TRAILER` line counts commits whose message carries an agent
  co-author or generator trailer. It is a floor, not a census: plenty of agent
  work commits under a human's name.
- `DUPLICATE BLOCKS` and `WORST FUNCTIONS BY MASS` are the actionable part,
  since they name a file and a line.
- A module wrapper can own the score. `const M = (() => { ...functions... })()`
  is one named callable, and the fold charges every nested branch to it even
  though each inner function is also counted on its own. An IIFE that holds
  private helpers can therefore top the list at CC 40+ while every piece inside
  it is small. Re-run that file with `--functions all`: if the wrapper drops to
  CC 1 and nothing else crosses the threshold, the number describes the wrapper,
  not the code. Do not flatten the wrapper to fix it. Keeping helpers private is
  the point of the pattern, and the reference implementation scores it the same
  way (verified: identical `total_mass` on a nested-function fixture).

## From numbers to fixes

The two lists point at different work, and they carry different evidence.

Verbosity arrives with its evidence attached. Clone detection only fires when a
block appears in at least two places, so every duplicate is a diagnosed case of
the same thing written twice. Compressing it is safe: you already have two real
examples of what the code has to do. Rank those blocks by `saves`, which is the
line count you get back.

Erosion arrives without that evidence. A high-CC function says complexity is
concentrated; it does not say how to take the function apart. Casey Muratori's
rule from "Semantic Compression" is the test worth applying:

> Like a good compressor, I don't reuse anything until I have at least two
> instances of it occurring.

So a split is worth making when either of these holds:

- The same block already appears a second time somewhere, which verbosity found.
- The extracted piece names a step in the problem's own language, the way
  `window_title`, `row`, and `complete` name steps of laying out a panel.

When neither holds, leave the function alone and say so in the report. A score
that improves while the names get worse is not progress. The numbered hard rules
for every fix live in `SKILL.md` under "The fix rules"; this section is the
reasoning behind them.

Two techniques from the same series do most of the work on a function that
resists splitting:

- **Build the shared state holder first.** When a dozen locals interact across a
  long function, move them into a small object, then move the steps onto that
  object as methods. Muratori calls this a shared stack frame. The object is
  what makes the separate functions possible.
- **Keep every level reachable.** When you add a higher-level helper, leave the
  lower-level path in place, so a caller with an unusual case can still drop
  down to it. A helper that swallows the only fine-grained route leaves what he
  calls a granularity hole. The next caller who does not fit has to rewrite the
  call site.

That second point matters more than it looks. Erosion has no lower bound, so
nothing in the metric can tell you when granularity has become discontinuous.
Worse, both numbers move the right way when you create a hole. Delete the
lower-level call: verbosity falls, because the duplicated lines went with it.
Erosion falls as well, because mass left the enclosing function. The report will
describe the result as an improvement.

### Methods that lower erosion

The fix rules list four lessons worth adding. Each one earns its place by
removing complexity, and each comes from a unit of the course.

**The four bad state types.** A state model is right when it matches the world
one to one. Unit 3 names the four ways it misses:

- junk states, which represent nothing
- missing states, which the model cannot express
- redundant states, which mean the same as another
- overloaded states, which mean two things at once

Each one costs branches, because the code has to rule the extra states out. A
record of four booleans carried 16 representable combinations. The engine
intended three. The two real states were a lifetime and a turn, and naming them
removed the rest along with the branches that policed them.

**Proof by token.** When the bug is a call in the wrong order, a value that can
exist only after the earlier call removes the check. Unit 3 calls an empty token
a proof that the step happened, and the technique scales to arbitrary sequences.
A guard is an instruction to the caller. A token is a fact about the program,
which is why these rules prefer the stronger of the two.

**Secrets.** Unit 4 defines a secret as anything a module can change without
changing any possible program linked to it. That is stronger than a private
field. The practical form is short: read code by following the data, and write
code by finding the secret and hiding it. Erosion often marks a missing secret,
because a visible data structure forces every caller to branch on its shape.

**Algebraic refactoring.** Unit 5 treats a large cleanup as a search over
equivalent programs. Each step is small, and you verify each one. The unit also
shows that extract method and add parameter are two cases of one move,
unsubstitution. A hotspot comes apart through verified steps, not insight. That
is what makes capture and replay practical.

Reading these lessons again takes one extra step: each is a Wistia video, and
its caption JSON is public. Take the media id from the lesson's network traffic,
then read https://fast.wistia.com/embed/captions/<id>.json.

### Know when to stop

The target is the total human effort the code costs over its life, not the
ratio. Muratori's test for whether a compression was worth making is whether
that total fell. If the only thing that improved is the number, it did not.

So it is fine to leave something that looks wrong. In the same article he
declines to remove a hardcoded column count, even though he can, because every
route to removing it adds more complexity than the constant does:

> It's OK to accept a solution that's a little ugly if the alternatives are
> uglier.

Write those down rather than fixing them. "Left as is; removing it would cost
more than it saves" is a better line in a report than a split that lowers the
score. It is also the answer to a reviewer who asks why a known wart survived a
cleanup.

## Paired metrics

Every figure above is half of a pair. The other half is a check on the code,
because three failure modes move both metrics the right way while making the
codebase worse. All three turned up during the same first run of this skill on a
real repository.

**Extraction moves complexity; it does not remove it. Cyclomatic complexity
counts every decision, and each `&&` and `||` is a decision. A guard chain can
therefore read well and score badly. A flattened currency lookup written as early
returns scored CC 15 in its first version and landed straight back in the eroded
list, so it needed a second split. The gate: measure the helper you created, not
only the function you shrank. If the helper sits above the threshold, the mass
moved rather than left.

**A granularity hole reads as progress. The mechanism is described above: both
numbers fall when a new helper swallows the level below it. The gate: after
adding a helper, name the route a caller with an unusual case would take. If
there is none, the helper is not finished. When collapsing a lower level on
purpose, write the reason down.

**A loop wrapper is charged for everything inside it. This is the reading trap
under "Reading a report". An IIFE holding private helpers can top the hotspot
list while every piece inside it is small. The gate comes before the fix, not
after: re-run that one file with `--functions all`. If the wrapper drops to CC 1
and nothing else crosses the threshold, leave it alone. Flattening it to move the
number costs encapsulation and risks a name collision in whatever scope the file
shares.

Not every falling number is suspect. Three moves lower a number and lower the
cost of the code at the same time:

- Deleting unreachable code.
- Deleting an assignment that the next branch overwrites.
- Compressing a duplicate that both sites truly share.

What the gate asks for in those cases is proof rather than a hunch. Read the two
paths, or grep for the callers.

None of the three traps is visible in the report itself. Two of them make the
score look better, and a number never says why it fell. That is the reason the fix
phase runs on pairs: a number, and a check that the code underneath it is
genuinely easier to change.

## Limits

- **Static only.** No tests, no runtime behavior, no correctness. Code can
  score well here and still be broken.
- **Duplication is structural, not semantic.** Two blocks that do the same
  thing in different shapes will not match.
- **Function counting is a choice.** The default counts named callables and
  folds anonymous callbacks into their enclosing function, matching the
  reference. `--functions all` counts every callable instead. The two modes
  will not produce the same erosion, and neither is wrong.
- **Granularity is name-based and call-site based.** The use count resolves no
  bindings, so two callables that share a name share their count, and a callback
  passed by name lands in the zero-use bucket. Treat the single-use list as
  candidates, not a call graph.
- **Granularity has no agent band.** SlopCodeBench measured verbosity and
  erosion, not granularity, so only the human band is grounded in the panel.
- **Plugin and registry code reads high by construction.** A factory, hook, or
  command handler is normally wired up at exactly one place, so it is
  single-use no matter how good the code is. Measured on one extension
  monorepo, granularity read 0.38 over the whole tree and 0.58 with tests
  excluded, and most of the rise was wiring plus legitimately named steps.
  Read a high number on a plugin repo as architecture, and check the names
  before calling any of it premature.
- **Granularity counts callables, not types.** Muratori's warning about
  segregation applies to types too: a base type with one subtype, or a class
  with one instance, is the same over-abstraction. That is not measured here.
- **Erosion depends on a threshold.** Moving the CC cutoff moves the score.
  Comparisons are only fair at the same cutoff, which is why 10 is fixed here.
- **Cross-language averages dilute.** A repo with a handful of enormous files
  among thousands of small ones can score low on both metrics anyway. The
  linked post notes exactly this case for one well-known project.
- **A single reading is weak evidence.** The useful signal is a trend: measure
  again after the next few merges and compare. The `--json` output is meant to
  be appended to a file over time for that reason.
- **Do not optimize these numbers directly.** The post says plainly that LOC
  stops being a meaningful measure the moment you start optimizing for it, and
  the same applies here. "From numbers to fixes" above has the test for when a
  split is legitimate. Splitting until the ratio looks good produces a worse
  codebase that scores better.

## Sources

- Earendil, "Measuring the sloppiness of code": https://earendil.com/posts/measuring-code-sloppiness/
- SlopCodeBench, arXiv:2603.24755: https://arxiv.org/html/2603.24755v1
- `scb-check`, the reference implementation: https://github.com/gabeorlanski/scb-check
- Benchmark site and leaderboard: https://www.scbench.ai/
- Casey Muratori, "Semantic Compression" (2014): https://caseymuratori.com/blog_0015
- Casey Muratori, "Complexity and Granularity" (2014): https://caseymuratori.com/blog_0016
- Casey Muratori, "Defining a Single Enumerant" (2014): https://caseymuratori.com/blog_0017