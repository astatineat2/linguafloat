# Release Versioning

The user requests versioned releases from 2026-10-12 onward.

- The recorded starting baseline is v0.8.0. Previous unversioned updates must not be presented as independently numbered historical releases.
- Increment the patch version for bug fixes, minor for new compatible features, and major for incompatible changes. Read the current version before choosing the next number. Do not bump for discussion or documentation-only work.
- Keep pyproject.toml, src/linguafloat/__init__.py, README.md, CHANGELOG.md, and the installed portable VERSION.txt consistent for each delivered release.
- The single supported user launch path is exe/LinguaFloat.exe (changed by the user for v0.8.2). Update it in place after verification; avoid creating more directories called new, latest, final, or update2.
- Keep build output and recoverable backups separate from the supported launch directory. Identify backups by version and timestamp; never delete user files to simplify version management without authorization.
- Keep the version visible beside the app name, in the main window title and tray tooltip. Never claim an installed EXE shows a version until verified.
- For each release record changes, tests, known limitations, and whether the EXE, document runtime, or both changed. Verify installed code matches source, including runtime-only updates.
- Final delivery must clearly state version, main changes, verified scope, known limitations, and the one supported launch path.

See RELEASES.md for the baseline and release process.

# Repository and publication authorization

- The development repository astatineat2/linguafloat is private again at the owner's request on 2026-11-05. Keep it private unless the owner explicitly asks to open it in a subsequent instruction.
- v0.11.2, v0.12.0, v0.13.0 v0.14.0 v0.14.1 v0.15.0 v0.15.1 v0.15.2 and v0.15.3 are local-only deliveries. Do not push code, upload assets, create a GitHub release/tag, or update the public update metadata for this delivery. The public download repository and existing corresponding-source assets remain available.
- On 2026-11-12 the owner explicitly authorized synchronizing the current v0.17.0 source and preparing a draft in the existing release repository, including portable ZIP, installer and checksums. This supersedes the earlier push restriction for the current source snapshot, not historical standalone releases. Installer acceptance remains paused; do not resume it, publish the draft, change repository visibility or update stable-channel metadata without further authorization.
- Subsequently on 2026-11-12 the owner explicitly requested publication and unrestricted source visibility. Publish v0.17.0-build1 and open astatineat2/linguafloat; this supersedes draft-only and private-repository restrictions above. Installer acceptance stays paused and limitations must remain public. Preserve the old signed automatic-update channel because the existing updater cannot accept the confirmed build-suffixed tag; provide direct manual downloads for this release.
