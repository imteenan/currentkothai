# Privacy

No account, no cookie, no analytics, no tracking pixel, no server of ours in the request path.
The site is static files; all reasoning happens in the visitor's browser.

## What leaves the browser

| Data | Goes to | Why |
|---|---|---|
| Coordinates | Nominatim / Photon (OpenStreetMap) | To turn a point into an address, which is what makes feeder text matching work |
| Nothing else | — | — |

Schedule and geometry files are fetched from the same static host that serves the page. There is
no request that identifies a visitor.

## What stays local

- Saved locations (`currentkothai.savedLocations`) — browser local storage only, never transmitted
- Last used location (`currentkothai.lastLocation`)
- Theme preference (`currentkothai.theme`)

Clearing site data deletes all of it.

## Sharing

"Copy link" puts coordinates in a URL **fragment** (`#at=…`), which browsers do not send to
servers. The visitor chooses who sees it.

## Bill and account numbers

**Never collected, never stored, never asked for.** AGENTS.md rule 5. Alert registration is
handled by linking people to their distributor's own flow and telling them what they will need
there. This project has no way to register anyone for anything and should never acquire one just
to enable a feature.

## Third-party services

Map tiles (OpenFreeMap, CARTO) and geocoding (Nominatim, Photon) are volunteer- or
community-operated and will see the visitor's IP as any web request would. Calls are debounced and
cached to keep volume low, and requests identify the project honestly rather than impersonating a
browser. No identifier is attached to them.
