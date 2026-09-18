# Task runner: https://github.com/casey/just
# Requires: `node` >= 22.12 (see .node-version), `npm`, and `just`.

# List all the justfile recipes.
help:
    just --list --list-prefix 'just '

# Install the dependencies.
install:
    npm ci
    npx biome check --write .
    npm run format:css
    prek run --all-files

# Lint the code with Biome, Stylelint, and prek.
lint:
    npm run lint

# Run all formatters.
format:
    npm run format

# Run the Astro type checker.
check:
    npm run check

# Build the production site to `dist/`.
build:
    npm run build

# Run the development server at localhost:4321.
serve:
    npm run dev --background

# Run the submission pipeline tests.
test-submissions:
    uv run --with pytest --with pydantic --with pyyaml --with python-slugify --with httpx --with pillow pytest tests/submissions

# Refresh site listings (screenshots, technologies, canonical URLs).
refresh-sites:
    uv run scripts/submissions/refresh_sites.py sites

# Refresh developer profiles (logos, online profile links).
refresh-profiles:
    uv run scripts/submissions/refresh_sites.py profiles
