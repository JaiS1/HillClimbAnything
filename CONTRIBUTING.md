# Contributing and maintenance

HillClimbAnything is a focused research release. It is provided as-is, and maintenance
is best-effort. Fork it and adapt it for your experiments. There is no support
SLA, hosted service, provider-integration roadmap or promise to accept feature
requests.

Changes most likely to be merged:

- Bug fixes with a regression test.
- Corrections to documentation that disagrees with the code.
- Small, tested changes to the referee (measurement, statistics, keep/revert,
  confirm) that keep the standard-library-only runtime.
- Calibration evidence: A/A and A/B runs on new kinds of targets, with raw data.

Changes likely to be declined: provider SDKs, hosted services, daemons, plugin
systems and large new subsystems. Those belong in a fork or a separate package
that calls the command interface.

Before opening a pull request, run from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Never include credentials, datasets, private paths or generated `.hca/` output.
Report security issues privately through the repository's GitHub security
advisory page ("Report a vulnerability"), not in a public issue.
