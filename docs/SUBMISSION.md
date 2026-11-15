# COMP4431 submission

Thursday class, 2026/27. The group roster is in the root README.

## Versions

- GitHub `main`: latest application code, Docker documentation, rehearsal report,
  demo examples and the new academic-evaluation license for covered original work.
- Local `codex/comp4431-assignment`: the same application plus this guide,
  the course README, presentation handoff and source packager. Do not push it.
- Production base: `0196a161` (includes the demo update and patched dnspython pin).
  The archive manifest records the full assignment commit and production base.

Existing Apache grants and all third-party licenses remain intact in both versions.

## Deliverables

`output/submission/COMP4431_Thursday_MatOpt_Source.zip` is the source-code archive.
Its `MatOpt/` folder contains the source, Dockerfiles/Compose, dependency manifests,
configuration example, required README, notices, synthetic demo data, rehearsed
HTML examples, test report and `output/doc/MatOpt_Presentation_Handoff.docx`.
`SUBMISSION_MANIFEST.json` lists the source commits and SHA-256 of every packaged
file. A separate `.zip.sha256` file checks the ZIP itself.

The ZIP excludes Git history, real `.env` files, API keys, local chats/databases,
installed dependencies, model downloads, build output and browser evidence.
The professor must supply a DeepSeek API key in `backend/.env` after extracting.
Follow the root README and run `docker compose up --build -d` from `MatOpt/`.
No host Python or Node.js is needed to run the application.

The presentation member must prepare the separate **PPTX**. The handoff document
supports that work; it is not the slides. Submit the ZIP and PPTX through the
course channel by 18 November 2026 at 23:59, subject to the professor's latest
announcement. No upload to the professor is performed by the packaging script.

## Rebuilding the ZIP

For the maintainer only, using Python 3 and Git on the host:

```sh
git switch codex/comp4431-assignment
git status --short
# Commit any intended source/document changes before packaging.
python3 scripts/package_submission.py
```

The packager reads **committed files only** and rejects tracked modifications,
unsafe paths, symlinks, runtime/private paths and matches for configured local
secrets. It verifies all archive entries against its generated manifest before
publishing the ZIP. It never reads files from ignored runtime directories into
the archive. Untracked files are not included.

On macOS, verify the checksum from `output/submission/` with:

```sh
shasum -a 256 -c COMP4431_Thursday_MatOpt_Source.zip.sha256
```

After extraction, the source runs independently of Git. The packager itself is
intended for the original local Git checkout, not for rebuilding from an extracted
archive without history.

## Presentation preparation

Use `docs/DEMO.md` for the exact prompts and `docs/DEMO-VERIFICATION.md` for real
model results and limitations. Fit and math passed; generated apps needed repairs,
and some publication claims were unsupported. Keep the checked HTML fallbacks
available through Workspace → Files → Upload files → Preview, clearly identified
as previously generated examples. Test the microphone on the actual presentation
machine. Keep Docker running to preserve the live Python kernel.
