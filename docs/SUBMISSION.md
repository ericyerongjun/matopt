# COMP4431 source submission — maintainer notes

This guide stays in the local checkout and is excluded from the professor's ZIP.
The Thursday group roster and Docker startup instructions are in the root README.

## Branches and licensing

GitHub `main` contains the application and the current academic-evaluation terms.
The local-only `codex/comp4431-assignment` branch contains the course README,
feature document and packaging configuration. Do not push that branch. Preserve
private Git history as an authorship/development record; never distribute it with
the source submission. Keep production licensing changes on both branches.

The root license covers the current distribution's original material owned by
Rongjun (Eric) Ye. Third-party terms and valid independent earlier grants remain
intact. The generic Apache text belongs to the identified third-party components;
it is not an alternative project-wide license for this distribution.

## Source deliverable

`output/submission/COMP4431_Thursday_MatOpt_Source.zip` contains one `MatOpt/`
folder with source, Docker configuration, dependency manifests, `.env.example`,
README, feature details, synthetic demo data, prepared HTML examples, and notices.
`SUBMISSION_MANIFEST.json` records included file sizes and SHA-256 hashes only.
The adjacent `.zip.sha256` verifies the archive itself.

The ZIP excludes `.git`, GitHub workflows, branch names and private repository
links, first-party commit references, internal plans and raw experiment traces,
the presenter handoff, credentials, chats/databases and installed dependencies.
Public upstream revision identifiers remain where needed for third-party
attribution and reproducible provenance. They are not MatOpt's Git history.

The presenter handoff remains separately available to the group at
`output/doc/MatOpt_Presentation_Handoff.docx`. It contains internal development
references and must not be added to this source ZIP. The source package needs no
PowerPoint or extra presentation document. This task does not create or submit
slides. The supplied brief lists the Thursday deadline as 18 November 2026 at
23:59; use the course's current instructions if amended.

After extraction, the assessor supplies a model API key in `backend/.env` and
runs `docker compose up --build -d` from `MatOpt/`. No Git, host Python or host
Node.js is required. First builds/model downloads and real API calls need network
access. The ZIP includes no paid-service credentials.

## Rebuild and validate

Maintainer host prerequisites: Python 3 and Git.

```sh
git switch codex/comp4431-assignment
git status --short
# Commit intended source and documentation changes first.
python3 -B -m unittest discover -s scripts/tests -p test_package_submission.py
python3 -B scripts/package_submission.py
cd output/submission
shasum -a 256 -c COMP4431_Thursday_MatOpt_Source.zip.sha256
```

The packager reads committed blobs only, applies an explicit inclusion policy,
rejects unsafe paths/symlinks, known local credential values, private keys and
private repository identifiers, then verifies entry uniqueness, CRCs and every
manifest hash. A validation failure leaves the existing ZIP untouched. Untracked
files are excluded, so add intended new files before committing. The packager
and its tests are maintainer tools and are not shipped in the ZIP.

Build from a fresh extraction before delivery. Use a separate build-only Compose
project name so validation does not replace the running demo. Do not copy live
runtime data or credentials into the archive. The source code and dependency
pins are unchanged by the licensing/package cleanup; the real-model rehearsal
and its remaining limitations are documented in `docs/DEMO-VERIFICATION.md`.
