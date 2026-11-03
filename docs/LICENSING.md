# Licensing notes (not legal advice)

| Component | Licence | What it means here |
|---|---|---|
| Webots | Apache-2.0 | free to use and build on, including commercially |
| RF-DETR Nano / Small / Medium / Large (detection) | Apache-2.0 | the default learned detector; no strings attached |
| RF-DETR XL / 2XL, segmentation, "plus" models | Roboflow Platform Model Licence | **not** used by this project |
| Ultralytics YOLO (v5/v8/11/12/26) | **AGPL-3.0** | optional backend only; `ultralytics` is deliberately **not** installed. Using it means open-sourcing your *whole* project under AGPL-3.0, or buying an Ultralytics Enterprise licence |
| OpenCV, NumPy, FastAPI, PyTorch | BSD / MIT-style | fine |
| Generated textures and defects | created procedurally by this repo | no third-party assets |

**Public sewer datasets** (Sewer-ML, Roboflow "Pipe Defects", Istanbul ISWDS, Co-UDlabs sample images) were *not* downloaded or used. Check each one's licence before mixing it into training: some are research-only and Sewer-ML is classification-only and needs a request.

If this project may become a commercial or municipal product: keep the default RF-DETR backend, leave `ultralytics` out, and review dataset licences first.
