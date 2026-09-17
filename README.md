# Openings in the Netherlands

A personal page that lists suitable job postings from the official careers sites of
logistics companies on the IND recognised sponsor list. It refreshes itself every
morning at 07:00 Istanbul time.

## How it works

1. `collector/` runs every day on GitHub Actions (free for public repositories).
2. For each company in `config/companies.json` it finds the careers system from the
   company website, reads the Dutch postings, and writes `docs/data/`.
3. `docs/` is the web page, published with GitHub Pages.

A company whose site cannot be read on a given day keeps its previous postings, so a
bad day never empties the list. The Sources tab shows what happened to every company.

## Everyday use

- **New**: postings that appeared in the last 7 days.
- **All open**: every current posting that matches the keywords.
- **Sources**: which careers sites were read and which need attention.
- Applied and hidden marks, and keyword edits, are saved in the browser you use.

## Changing things

- Run the collection now: Actions tab, "Daily job collection", "Run workflow".
- Default keywords: `docs/data/filters.json`.
- Companies, website addresses and careers-system settings: `config/companies.json`.
- `setup/daily-workflow.yml` is a copy of `.github/workflows/daily.yml`, kept for
  first-time setup. Edit the file under `.github/` if the schedule ever changes.

## Tests

    python -m unittest discover -s tests -t .
    node tests/filters.test.mjs
