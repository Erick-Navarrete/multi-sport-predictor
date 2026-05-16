# Multi-Sport Predictor Project

## Project Overview
AI-powered multi-sport prediction web app. Extends the PremPredictor architecture
to support multiple sports and leagues via ESPN API. Flask backend, ELO + sport-specific
ML models, shared SPA frontend with sport/league switcher.

## Tech Stack
- **Backend**: Flask (Python), ELO rating system per sport, sport-specific ML models
- **Frontend**: Vanilla JS, Chart.js, Font Awesome icons
- **Data**: JSON files per sport in `data/{sport}/{league}/`
- **APIs**: ESPN (primary, free, real-time), sport-specific APIs as supplements
- **Deploy**: Render

## Architecture
- Single-page app with sport/league switcher in header
- Shared tab structure per sport: Predictions, Historical, Standings, Analytics
- Each sport has its own refresh pipeline, ELO config, and ML model
- ESPN API provides both results and fixtures across all sports

## Key Principles
- **Reuse PremPredictor patterns**: same JSON shapes, ELO engine, prediction locking, blend logic
- **Sport adapters**: each sport implements a common interface (fetch, parse, predict)
- **ESPN-first**: ESPN scoreboard is the primary data source for all sports
- **Clean separation**: data/{sport}/{league}/ isolates sport data, models per sport

## Sport/API Reference
- ESPN Site API: `https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard`
- ESPN Core API: `https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/`
- All endpoints are free, no auth required

## Workflow
- Plan first for any non-trivial task
- Commit to git after each meaningful step
- Never change the visual structure of the report without explicit approval
- Subagents for research, direct tools for implementation
