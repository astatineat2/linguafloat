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
