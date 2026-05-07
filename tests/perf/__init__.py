"""Performance benchmark suite (Phase Beta gate #1, v0.15).

Marked ``@pytest.mark.slow`` so it doesn't run in the default ``pytest``
invocation. To run:

    pytest tests/perf -m slow -v

Or the standalone CLI:

    python -m tests.perf.bench_lineage --corpus 1000

Each benchmark prints a table comparing baseline vs. optimized paths,
median wall-clock per call, and the speedup factor. Results are also
written to ``tests/perf/results/{benchmark}-{timestamp}.json`` for
historical tracking.

The benchmarks are deliberately self-contained: they don't touch the
DB, so they're not measuring SQLite/IO costs (which would dominate at
small N). They measure ONLY the candidate-selection algorithm.
"""
