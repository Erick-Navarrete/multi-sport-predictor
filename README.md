# Prediction App Template

A blank, reusable template for building prediction web apps with ML models.

## What's Included

- **Flask backend** (`web/app.py`) with API endpoints for predictions, historical data, model info, and on-demand refresh
- **Single-page frontend** (`web/templates/index.html`) with 5 tabs: Predictions, Historical, Standings, Modeling, Analytics
- **Dark mode** toggle with system preference detection
- **Sortable tables** with full-text headers (no abbreviations)
- **Mobile-responsive** layout
- **Blank data files** in `data/` — seed with your own data
- **Render-ready** deployment config

## Quick Start

1. Copy this template folder to your new project
2. Replace domain-specific names:
   - `Entity A` / `Entity B` → your competitors/teams/players
   - `A Win` / `Draw` / `B Win` → your outcome labels
   - `Rating` → your rating system name
   - Column headers in `index.html` `<th>` elements
3. Implement `refresh_data.py` to fetch and process your data source
4. Add your ML model to `outputs/` and update the `/api/model-info` endpoint
5. Seed `data/*.json` with your initial data
6. Run: `pip install -r requirements.txt && cd web && python app.py`

## Customization Checklist

- [ ] Rename app title and badge in `index.html` header
- [ ] Update column headers (`<th>` elements) for your domain
- [ ] Update `resultTag()` function with your outcome labels
- [ ] Implement `refresh_data.py` for your data source
- [ ] Add model files to `outputs/` and wire up `/api/model-info`
- [ ] Update `renderModeling()` with your model's details
- [ ] Set env vars (API keys, etc.) in Render dashboard
- [ ] Update `render.yaml` with your service name
