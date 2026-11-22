import time, json, urllib.request, numpy as np, rasterio, pystac_client, planetary_computer as pc
from rasterio.windows import from_bounds
from pyproj import Transformer
cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
pt = {"type": "Point", "coordinates": [-74.9621, 4.1895]}
s2 = next(cat.search(collections=["sentinel-2-l2a"], intersects=pt, datetime="2024-01-01/2024-03-01", query={"eo:cloud_cover": {"lt": 30}}).items())
ls = next(cat.search(collections=["landsat-c2-l2"], intersects=pt, datetime="2024-01-01/2024-04-01", query={"platform": {"eq": "landsat-8"}}).items())
def size(u):
    r = urllib.request.Request(u, method="HEAD"); return int(urllib.request.urlopen(r, timeout=60).headers["Content-Length"])
res = {}
for it, bands in [(s2, ["B04", "B08", "B8A", "B11", "SCL"]), (ls, ["red", "nir08", "swir16", "qa_pixel"])]:
    print(it.id)
    for b in bands:
        u = it.assets[b].href
        with rasterio.open(u) as src:
            info = dict(shape=src.shape, res=src.res[0], dtype=src.dtypes[0], block=src.block_shapes[0], bytes_archivo=size(u))
            tr = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x, y = tr.transform(-74.9621, 4.1895)
            for lado in (100, 1000, 5000):
                t = time.perf_counter()
                a = src.read(1, window=from_bounds(x - lado/2, y - lado/2, x + lado/2, y + lado/2, src.transform))
                info[f"seg_{lado}m"] = round(time.perf_counter() - t, 2); info[f"px_{lado}m"] = a.size
        print(" ", b, info); res[f"{it.id[:3]}_{b}"] = info
json.dump(res, open(__import__("pathlib").Path(__file__).resolve().parents[2] / "reports/modelo_a/estimacion/bench_lectura.json", "w"))
