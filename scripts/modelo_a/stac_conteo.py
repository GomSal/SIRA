import json, collections, pystac_client, planetary_computer as pc
cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
pt = {"type": "Point", "coordinates": [-74.9621, 4.1895]}
out = {}
for col, q in [("sentinel-2-l2a", {}), ("landsat-c2-l2", {})]:
    items = list(cat.search(collections=[col], intersects=pt, datetime="1982-01-01/2026-11-18").items())
    c = collections.defaultdict(lambda: collections.Counter())
    tiles = collections.Counter()
    for it in items:
        y = it.datetime.year if it.datetime else int(it.properties["start_datetime"][:4])
        plat = it.properties.get("platform", "?")
        c[y][plat] += 1
        tiles[it.properties.get("s2:mgrs_tile") or f"{it.properties.get('landsat:wrs_path')}/{it.properties.get('landsat:wrs_row')}"] += 1
    print(col, len(items), dict(tiles))
    for y in sorted(c): print(" ", y, dict(c[y]))
    out[col] = {str(y): dict(c[y]) for y in c}
    if items:
        it = items[0]; print(" ejemplo", it.id, {k: (v.extra_fields.get("raster:bands") or [{}])[0].get("spatial_resolution") for k, v in it.assets.items()} if False else list(it.assets)[:25])
json.dump(out, open(__import__("pathlib").Path(__file__).resolve().parents[2] / "reports/modelo_a/estimacion/stac_conteo.json", "w"))
