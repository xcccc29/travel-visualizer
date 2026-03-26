# Travel Visualizer

Generate a portable HTML itinerary viewer from a markdown travel plan.

## Features

- Split-screen layout with itinerary text on the left and an interactive map on the right
- Day filter tabs to focus the route and stops for a single day
- Clickable place names in the text that pan the map to the matching marker
- Numbered markers and route lines for each day
- Dashed inter-city travel lines for travel days such as `Munich → Salzburg`
- Google Maps markers and popups for each stop
- "View on Google Maps" links in popups for direct access to reviews, photos, and place details
- Geocoding cache so repeated runs do not keep hitting the geocoder

## Install

```bash
python -m pip install -r requirements.txt
```

## Google Maps API Key Setup

1. Go to Google Cloud Console -> APIs & Services -> Credentials.
2. Create a new API key.
3. Enable the `Maps JavaScript API`.
4. Recommended: restrict the key so it can only be used with `Maps JavaScript API`.

Note: The API key is embedded in the generated HTML file. If you share the file publicly, consider using tighter restrictions or a separate low-quota key. Google includes $200/month in free credit (about 28,000 map loads).

## Usage

```bash
python generate.py "D:\obsidian\raw_thoughts\2026 Austria & Germany Travel Plan.md" --api-key YOUR_KEY -o itinerary.html
```

You can also set `GOOGLE_MAPS_API_KEY` instead of passing `--api-key`.

Optional flags:

- `--cache geocache.json` to choose a cache file
- `--api-key YOUR_KEY` to provide a Google Maps API key
- `--no-geocode` to render using cache/manual coordinates only

## Notes

- The generated HTML is a single file.
- The itinerary content works offline.
- Google Maps requires internet access for both the map tiles and the API script.
- You can manually fix a bad geocode by editing `geocache.json` and re-running with `--no-geocode`.