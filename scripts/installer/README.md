# LinguaFloat Windows Distribution

The payload version must match the current application version. Distribution
build numbers identify packaging changes separately. The distribution snapshot
does not prune dependencies or compile application code.

1. Run `build_distribution.py build --output <new-directory>` with an existing
   Python 3.13 build environment and Pillow. The user does not need Python.
2. The script snapshots the exact reviewed `exe` directory, inventories every file
   with SHA256, and prepares Setup inputs. Do not distribute the optional internal test ZIP.
3. Run `build_installer.ps1 -DistributionDirectory <new-directory> -Compiler <ISCC.exe>`.
   This build uses the user's Inno Setup 7.1.1 and its supplied Simplified Chinese
   language file. The compiler is a build tool, not an application dependency.
   No installer compiler is downloaded automatically.
4. Verify fresh installation, uninstall and data retention in an isolated profile. After the first Setup release, also test a real Setup-to-Setup update and restart. Record the results and installer hash in `SETUP-ACCEPTANCE.json` in the release folder. `release_tools.py prepare --distribution <new-directory>` signs the Setup metadata; `github_publish.py publish` rejects ZIP binaries and missing acceptance. Neither building nor signing publishes a release.

Installed defaults: current-user LocalAppData/Programs/LinguaFloat, no elevation,
Start menu shortcut, optional desktop shortcut. Existing AppData and Credential
Manager data are not bundled or deleted. No recursive uninstall cleanup exists.
Files added by users to the installation directory are not removed by a wildcard.
Running processes inside the selected installation directory block replacement;
the installer does not automatically kill an application or document worker.

The wizard uses the existing app icon, light background, white fields, purple
headings and progress. Native buttons, Browse dialog, window borders and error
dialogs retain Windows styling for keyboard/accessibility compatibility.
The installer has no new UI framework or post-install component downloader.
Existing document-engine first-use resources may still require downloads.

The sequence is Welcome -> Directory -> Shortcuts -> Installing -> Finished.
The directory browser explicitly enables native new-folder creation and editable
folder naming. The last pre-install action says Install. FinishedLabel supplies
only the installed version; the native ClickFinish supplies the single exit hint.
The wizard bitmap has no accent stripe. First-run configuration is implemented
in the application and uses its existing first_run_complete marker and provider
credential transaction, not installer-written profile data.

Public documentation for the selected installer tool:
- https://jrsoftware.org/isdl.php
- https://jrsoftware.org/ishelp/topic_setup_wizardstyle.htm
- https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
- https://jrsoftware.org/ishelp/topic_filessection.htm
- https://jrsoftware.org/ishelp/topic_uninstalldeletesection.htm

An isolated test can pass `/DIR=<sandbox>` without changing the AppId or producing
a different installer. Native shell-folder shortcuts still use the real user's
Desktop and Start menu; do not assume APPDATA or a hidden group-page override
isolates them. Guard those locations and the AppId registry key before testing.
Do not overwrite an existing production installation. No user credentials or live
translation calls are needed.
