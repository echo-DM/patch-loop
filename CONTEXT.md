# PatchLoop

PatchLoop turns an explicitly authorized GitHub Issue into bounded, reviewable
repository work while keeping task data, execution authority, and publication
authority separate.

## Language

**PatchLoop source repository**:
The public repository that releases PatchLoop and its reusable workflow. It is
the product being installed, not the repository whose Issue is being solved.
_Avoid_: Smoke repository, target repository

**Target repository**:
The adopter-owned repository containing the authorized Issue and receiving any
PatchLoop branch, feedback, or Draft PR.
_Avoid_: PatchLoop repository, source repository

**PatchLoop installation**:
A target repository's version-pinned adoption of the reusable workflow and
versioned repository configuration, with the local CLI as its reproduction path.
It does not imply a PyPI release.
_Avoid_: PyPI installation, hosted service installation

**Smoke repository**:
A dedicated target repository used to exercise PatchLoop against live provider
and GitHub boundaries without making the PatchLoop source repository its own
test target.
_Avoid_: PatchLoop source repository, fixture repository

**Offline validation**:
Credential-free, deterministic validation using fixtures and local acceptance
boundaries without live Gemini calls or real GitHub writes.
_Avoid_: Live smoke, production verification

**Live smoke**:
An explicitly enabled, bounded end-to-end run using a real Gemini credential and
a dedicated smoke repository. It is release evidence, not part of default CI.
_Avoid_: Default test suite, offline validation
