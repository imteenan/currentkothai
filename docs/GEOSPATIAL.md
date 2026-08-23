# Geospatial layers

All layers are built by `python -m workers.geospatial.build_geo` and consumed directly by the
browser — there is no PostGIS. Point-in-polygon runs in `apps/web/src/geo.js`.

| File | Features | Size | Source | Trust |
|---|---|---|---|---|
| `utility-territories.geojson` | 5 | ~143 KB | OSM + geoBoundaries | **Estimated.** See below |
| `desco-offices.geojson` | 23 | ~25 KB | OSM via Nominatim | Approximate reference points |
| `dhaka-neighbourhoods.geojson` | 897 | ~533 KB | OSM via Overpass | Good — real OSM place nodes |
| `bangladesh-admin.geojson` | 64 | ~636 KB | geoBoundaries ADM2 | Official, simplified for display |

`bangladesh-admin.geojson` is lazy-loaded only when the Districts map layer is selected.

## Known wrong / do not trust

**No distributor publishes service-territory GIS data.** Every utility polygon here is our own
construction and carries `status: "estimated"`. They render dashed for that reason.

**OpenStreetMap has no DNCC/DSCC boundaries.** Inside Dhaka the finest admin relation available is
a single "Dhaka Metropolitan" polygon. So the DESCO/DPDC line is built by geocoding the zone names
each utility prints on its own schedules, computing a Voronoi partition of the combined point set,
and clipping to the admin area. It is reliable in the middle of each territory and a guess along
the join.

**Nominatim free-text search is unsafe for boundaries.** Querying "Narayanganj City Corporation"
returns a business called "Setu Corporation". Named-area lookups here go through Overpass admin
relations, and geocoded points are bbox-checked against greater Dhaka before being accepted.

**Outside Dhaka the territories are administrative divisions, not service areas.** NESCO, WZPDCL
and BPDB serve only the *urban* parts of the divisions coloured for them; rural addresses inside
those shapes belong to a local Palli Bidyut Samity under BREB. Those features carry
`confidence: "low"` and a `caveat` the UI displays.

**BREB has no polygon at all.** It would be the rural complement of every urban territory, which
we cannot compute honestly without urban/rural boundaries we do not have.

**Gazipur Sadar failed to load** during the last build (all three Overpass mirrors errored on that
relation), so DESCO's Tongi fringe may be clipped short. Re-running the build will pick it up if
Overpass recovers.

**`desco-divisions.geojson` does not exist.** Only the office/reference *points* do. The app
handles its absence and ranks divisions by proximity instead, which is the more defensible signal
anyway. Division ranking degrades gracefully rather than failing.
