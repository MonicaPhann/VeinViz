# Archive: earlier experiments

Not used by the main `server.py`. Kept for reference.

| File | What it is |
|---|---|
| `server_enhanced_only.py` | Snapshot of the current main server (same as `../server.py`). |
| `server_sharp_shadowfix.py` | Sharper version: full camera resolution, edge-preserving denoise, light sharpening, and shadow removal (lighting estimated with veins filled in, then divided out). Grainier in shadowed areas. |
| `server_with_heatmap.py` + `heatmap.py` | Adds a vein **detection-confidence** heatmap stream at `/stream/heatmap` (multi-scale Hessian "vesselness"; transparent = weak, yellow = moderate, dark red = strong). An image-processing signal only, not a clinical assessment. |
| `arm.py`, `veins.py`, `pipeline.py` | First version: MediaPipe hand model to find the arm, vein detection inside it, a "suggested site" marker and vein data output. Its live server wasn't kept; run it with `tools/offline_test.py`. `arm.py` downloads the MediaPipe hand model into `models/` on first run (needs `pip install mediapipe`). |
| `tools/offline_test.py` | Runs that first version on a recorded clip or photo and reports arm detection rate, speed and flicker. |
| `PLAN.md` | Design notes and test plan for that first version. |

**Trying one of the servers:** copy it (and `heatmap.py` for the heatmap version) into the parent folder next to
`index.html`, then run it like the main server. For the heatmap, open `http://localhost:8000/stream/heatmap` directly.
