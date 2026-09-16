"""sloptrack: measure the slop in a codebase.

Three signals, all from a Tree-sitter parse when the grammars are available:

    verbosity    = shared lines with the clone detector / SLOC
    erosion      = complexity mass in functions over CC 10 / total mass
    granularity  = callables invoked exactly once / callables with a call site

The package also ships the `measure-then-fix-slop` agent skill, which turns the
numbers into a report and a set of fix rules. Install it with
`sloptrack install-skill`.
"""

__version__ = "0.1.0"
