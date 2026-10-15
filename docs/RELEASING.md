# Releasing HillClimbAnything to PyPI

Publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is
created or stored. PyPI trusts `.github/workflows/publish.yml` in `JaiS1/HillClimbAnything`, in a
named GitHub environment.

## One-time setup (the repo owner, in a browser)

1. **Create accounts.** Make (or sign in to) an account on <https://pypi.org>, and optionally one on
   <https://test.pypi.org> for a rehearsal. Turn on 2FA, which PyPI requires.
2. **Add a pending trusted publisher on PyPI.** Go to *Your account → Publishing → Add a new pending
   publisher → GitHub* and fill in:

   | Field | Value |
   |---|---|
   | PyPI project name | `hillclimbanything` |
   | Owner | `JaiS1` |
   | Repository name | `HillClimbAnything` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   "Pending" means the project doesn't exist yet. The first successful publish creates it. This
   step does not reserve the name.
3. **TestPyPI (optional rehearsal).** Do the same at test.pypi.org, with environment name
   `testpypi`.
4. **Create the matching GitHub environments.** In the repo, go to *Settings → Environments* and
   create `pypi`, plus `testpypi` if you rehearse. For `pypi`, it's sensible to add yourself as a
   required reviewer, so every publish needs one click from you.

## Each release

1. **Set the version.** Make `version` in `pyproject.toml` and `__version__` in `hca/__init__.py`
   both read `X.Y.Z`. Set the same `version` in `.claude-plugin/plugin.json`. Commit and push to
   `main`. CI must be green.
2. **Optional rehearsal.** *Actions → publish → Run workflow* uploads to TestPyPI. Then test it:

   ```bash
   uvx --index-url https://test.pypi.org/simple/ --from hillclimbanything hca --version
   ```

3. **Release.** Create a GitHub release with tag `vX.Y.Z` (*Releases → Draft a new release*). When
   you publish it, the workflow builds, checks that the tag matches the version, and uploads to
   PyPI.
4. **Verify.** Check the result:

   ```bash
   uvx hillclimbanything --version
   ```

PyPI never accepts the same version twice. A mistake means a new patch version.
