# FIKER Website

FIKER is a youth-centered nonprofit website for child protection, education, youth participation, climate action, and community development.

## Repository structure

- `assets/` — all local image assets used by the site
- `styles/` — shared CSS
- `scripts/` — shared browser JavaScript
- `data/` — content data
- `server.py` — Flask server and forum API
- `forum_db.py` — forum database and moderation logic
- `auth.py` — authentication integration
- `Dockerfile` — production container configuration
- `.github/workflows/pages.yml` — static GitHub Pages deployment

## Local development

```bash
python3 -m pip install -r requirements.txt
python3 server.py
```

Open <http://127.0.0.1:3000/>.

## GitHub publishing

The repository includes a GitHub Pages workflow. Enable **Settings → Pages → Source: GitHub Actions** after pushing the repository. The public informational pages and local image assets can be served by GitHub Pages.

GitHub Pages is static hosting, so the forum, authentication, moderation API, and database-backed features require the included Docker/Flask application to run on a Python-capable host. Use the `Dockerfile` with a host such as Render, Railway, Fly.io, or a VPS for the full experience.
