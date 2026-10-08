# Publishing v0.5.4 on GitHub

1. Create a GitHub repository named `DROPS-Hydro-MX` without pre-generating a README, .gitignore or license.
2. In a terminal, navigate to this repository folder.
3. Run:

```bash
git init
git branch -M main
git add .
git commit -m "Initial public release of DROPS-Hydro MX v0.5.4"
git remote add origin https://github.com/YOUR_USERNAME/DROPS-Hydro-MX.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username. Log in to GitHub via the supported authentication flow (for example, GitHub CLI or credential manager); do not paste a token into the Git remote URL.

4. Create a release under **Releases → Draft a new release** with tag `v0.5.4` and title `DROPS-Hydro MX v0.5.4`.
5. Attach the **original plugin ZIP** (`DROPS-Hydro_MX_v0.5.4.zip`) to the release as the user-installable artifact. Do not offer GitHub's auto-generated repository ZIP as the plugin ZIP, since the repository has extra top-level files.
6. In QGIS, test **Plugins → Manage and Install Plugins → Install from ZIP** using the uploaded distribution archive before sharing.

Before opening the repository to the public, review source code, attribution, license and any external code/data terms. The preparatory packaging performed here is a static check only, not a runtime QGIS validation.
