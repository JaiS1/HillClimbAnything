# Contributing and maintenance

Dream Loop is a focused research release. It is provided as-is, and maintenance
is best-effort. Fork it and adapt it for your experiments. There is no support
SLA, hosted service, provider-integration roadmap or promise to accept feature
requests.

Changes most likely to be merged:

- Bug fixes with a regression test.
- Corrections to documentation that disagrees with the code.
- Small, tested changes to the core loop (live search, recorded worlds, dream,
  next cycle) that keep the standard-library-only runtime.

Changes likely to be declined: provider SDKs, hosted services, daemons, plugin
systems and large new subsystems. Those belong in a fork or a separate package
that calls the command interface.

Before opening a pull request, run from the repository root:

```bash
python3 -m unittest discover -s tools/dream_rsi/tests -v
python3 -m tools.dream_rsi.demo_evolution --output .runs/check-demo
```

Never include credentials, datasets, private paths or generated `.runs/` output.
Report security issues privately through the repository's GitHub security
advisory page ("Report a vulnerability"), not in a public issue.
