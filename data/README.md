# Data

`turkiye-ilce-sinirlari_tm36.csv` lists the 973 districts of Turkey with a center point and area for each.
It was exported from https://rodostoteknoloji.com/online-tools/turkey-boundaries.html, which derives it from
OpenStreetMap data (Geofabrik Turkey extract, September 2026).

**License:** © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Attribution is required, and a derived database that is publicly distributed must be shared under ODbL.
This applies to the files in this folder and to `src/kivilcim/static/ilceler.json`, which is generated from
the CSV. The source code of this project stays under the MIT license.

This is not official boundary data. The points are approximate district centers.

Regenerate the JSON used by the web page:

```bash
python -m kivilcim.districts data/turkiye-ilce-sinirlari_tm36.csv src/kivilcim/static/ilceler.json
```
