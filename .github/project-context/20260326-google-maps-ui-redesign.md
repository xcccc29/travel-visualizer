# Approved Plan — Google Maps Migration & UI Redesign (2026-03-26)

## Summary

Replace the Leaflet/OpenStreetMap mapping layer with Google Maps JavaScript API and redesign the UI to match a sophisticated European travel aesthetic. Each marker popup will include a "View on Google Maps" link that opens the place in Google Maps — giving your friend direct access to reviews, photos, and place details. The output remains a single portable HTML file. The UI gets a refined typographic and color overhaul evoking the cultured tone of the Austria & Germany itinerary.

## Implementation Steps

1. Add `--api-key` CLI argument (and `GOOGLE_MAPS_API_KEY` env var fallback) to `generate.py`
2. Replace Leaflet CDN references with Google Maps JS API `<script>` tag (with API key template variable)
3. Rewrite the map initialization JS: `L.map` → `google.maps.Map`, configure map style, `gestureHandling: "greedy"` for scroll zoom
4. Rewrite marker creation: `L.marker` + `L.divIcon` → `google.maps.Marker` with custom numbered SVG icons, color-coded per day
5. Rewrite polylines: `L.polyline` → `google.maps.Polyline` (solid for day routes, dashed for inter-city segments)
6. Rewrite popups: `L.popup` → `google.maps.InfoWindow`, add "View on Google Maps" link (`https://www.google.com/maps/search/?api=1&query=NAME,CITY`) in each popup
7. Rewrite layer management: `L.layerGroup` → arrays of markers/polylines with `.setMap(map)` / `.setMap(null)` toggling
8. Rewrite map camera controls: `map.flyTo` → `map.panTo`/`setZoom`, `map.fitBounds` using `google.maps.LatLngBounds`
9. Add `toLatLng([lat, lng])` helper to convert existing `[lat, lng]` arrays to `{lat, lng}` objects
10. Redesign CSS: new warm color palette, refined typography, polished card/filter bar design
11. Pass `api_key` into `render_template` Jinja2 context
12. Update README with Google Maps API key setup instructions and security notes

## Phases

### Phase 1: CLI & Template Plumbing
- Steps 1, 11: Add `--api-key` arg, env var fallback, pass into template → **Coder**
  Files: `generate.py`

### Phase 2: Google Maps JS Migration
- Steps 2–9: Full Leaflet→Google Maps rewrite in template JS → **Coder**
  Files: `template.html`

### Phase 3: UI Redesign (can run PARALLEL with Phase 2 — CSS is independent of JS logic)
- Step 10: Overhaul `<style>` block → **Designer**
  Files: `template.html`

### Phase 4: Documentation (after Phase 1–3)
- Step 12: Update README with API key setup → **Coder**
  Files: `README.md`

## Edge Cases

- **API key security**: The key is embedded in plaintext in the shared HTML. Mitigation: document that the user should restrict the key to "Maps JavaScript API" only in Google Cloud Console, and note $200/month free credit covers ~28,000 map loads.
- **API key missing**: `generate.py` exits with a clear error if neither `--api-key` nor `GOOGLE_MAPS_API_KEY` env var is set.
- **Offline behavior**: Google Maps requires internet for both tiles and the API script. The text itinerary still works offline. The map note overlay will be updated to reflect this.
- **Coordinate format**: Existing `[lat, lng]` arrays remain in the JSON payload — conversion to `{lat, lng}` happens client-side via `toLatLng` helper. No changes to `generate.py` or `geocache.json`.
- **Legacy Marker vs. AdvancedMarkerElement**: Plan uses legacy `google.maps.Marker` with custom SVG icons — no Map ID required, simpler setup. Can upgrade to AdvancedMarkerElement later.
- **Google Maps search URL quality**: Using `query=PLACE+NAME,CITY` works well for known landmarks. City context (already in the data) improves accuracy.
- **Google Fonts CDN**: Adding Inter font — falls back gracefully to Segoe UI / system-ui offline.
- **Existing geocache**: No changes needed — Nominatim coordinates work with Google Maps.

## Decisions

- Use legacy `google.maps.Marker` (not AdvancedMarkerElement) — avoids Map ID requirement, simpler setup, upgradeable later.
- API key via CLI arg + env var fallback — no config file needed for now.
