# RSVP Manager installation

Website: **https://wedding.notin.fo/**

[Setup wizard](https://wedding.notin.fo/setup.html) · [Administrator sign-in](https://wedding.notin.fo/admin/) · [Cloud setup guide](https://wedding.notin.fo/docs/StudioSetup.html)

This repository deploys the selected [RSVP Manager release](https://github.com/Paetur/RSVP-Manager/releases/tag/v0.9.0-beta.7) directly at the domain root. It contains deployment instructions; application files come from the verified release ZIP.

## Deploy

Open **Actions → Deploy RSVP → Run workflow** and select `v0.9.0-beta.7`. The workflow checks the archive and every included file, then publishes the website using GitHub Pages. It does not create or change a Supabase project, run SQL, restore a backup or publish an event.

## Existing backend and upgrades

The existing installation is configured. Keep the Actions connection variables, custom domain, root path, Auth redirects and backend secrets. Backup format 3 remains current. Apply the reviewed database and Guest Gateway updates before selecting the beta.7 website. Use the SQL installer only with an empty application schema. The Pages workflow updates only the website files.

For a separate new empty installation, create its Supabase project and use the setup wizard:

Cloud project options:

| Setting | Choose |
|---|---|
| Enable Data API | ON |
| Automatically expose new tables | OFF |
| Enable automatic RLS | ON |

Keep public exposed to the Data API and app_private unexposed. Run the complete SQL installer once on an empty application schema. Use the complete release-matching Studio files for both Edge Functions and set platform Verify JWT OFF; the application’s own validation remains active.

After the wizard verifies the connection and administrator, open **Settings → Secrets and variables → Actions → Variables** in this repository. Create:

| Variable | Wizard/downloaded configuration field |
|---|---|
| RSVP_SUPABASE_URL | supabaseUrl |
| RSVP_PUBLISHABLE_KEY | supabaseKey |

Use only the public project URL and public browser key. Rerun Deploy RSVP for the same release tag. The workflow generates js/config.js in the Pages artifact; committing a separate config file does not configure the generated website. Browser-local wizard settings apply only to that browser.

Use `https://wedding.notin.fo` for RSVP_SITE_ORIGIN and `https://wedding.notin.fo/` for RSVP_SITE_URL. Set the Auth Site URL to the latter and allow `https://wedding.notin.fo/admin/?admin-invitation=1` as an invitation redirect. Keep the event unpublished while testing.

## Domain

GitHub Pages binds wedding.notin.fo to this repository with HTTPS enforcement. DNS CNAME points to paetur.github.io. Deployment path is empty, so the app is served at the domain root.

## Verification before deployment

The deployment checker now requires successful full CI for the selected public release tag’s exact commit before it downloads/builds a Pages artifact. All three application jobs must pass; lightweight checks, skipped jobs, missing evidence or GitHub API failures stop deployment. If blocked, run the full verification workflow in the public release repository and wait for success, then retry this manual deployment workflow. The accepted CI commit and run URL are recorded in the new artifact.

Updating this checker does not run the manual Pages deployment or change the installed database/event.
