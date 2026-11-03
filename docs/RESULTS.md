# Measured results (simulator data only)

All numbers come from the simulator on an RTX 5050 Laptop (8 GB). **They say nothing about real concrete**: see "What this does not show".

## Detector (RF-DETR Nano, 25 epochs, 55 min)
Dataset `sewer_v1`: 4,300 train / 650 valid / 450 test images (3 maps × 3 rovers × 4 random-defect seeds), ~49 % of images contain no defect.
Test split, confidence ≥ 0.4, IoU ≥ 0.3 (`tools.eval_detector`):

| class | precision | recall | boxes |
|---|---|---|---|
| crack | 91.6 % | 84.5 % | 142 |
| leak | 92.8 % | 85.8 % | 120 |

Validation mAP@50:95 = 0.70. Inference 28 ms/frame (≈36 fps) on the GPU. The training-free OpenCV fallback scored 1-3 % precision on the same kind of frames (it fires on mortar lines, silt and sensor noise).

## Whole missions (defects tracked into a map, then scored against the planted ones)
* `star_five_goals`, `mid_4wd`, *inspect all*: visited all 5 goal posts, found **16 / 16** planted defects (8 cracks, 8 leaks), 5 false alarms (precision 76 %).
* `straight_t`, `mid_4wd`, *go to GOAL_EAST*, motion blur 60 ms, same 14 random defects each time:

| max speed (m/s) | found / known | map precision | false alarms |
|---|---|---|---|
| 0.2 | 12 / 14 | 67 % | 6 |
| 0.4 | 12 / 14 | 86 % | 2 |
| 0.7 | 12 / 14 | 92 % | 1 |
| 1.0 | 12 / 14 | 92 % | 1 |
| 1.5 | 12 / 14 | 100 % | 0 |

Speed did not reduce recall up to 1.5 m/s in this setup (15 fps camera, the two misses are the same marginal defects every time). Slower runs see each spot in more frames, so a stray detection has more chances to be confirmed, hence the lower precision. Tightening the tracker (confidence ≥ 0.4, ≥ 4 sightings, duplicate-track merging) cut false alarms from 21 to 6 at the slowest speed, at the cost of one marginal defect.

## Rover physics checks (`tools.webots_runner check`)
All four presets drive straight with < 1.5 cm lateral drift over 6 s and distance within ~5 % of prediction. In-place turn rate is below ideal for skid-steer types; the measured yaw gains stored in the presets are 0.87 (2WD), 0.67 (4WD), 0.67 (tracked), 0.66 (6WD).

## What this does not show
* Real-world accuracy. The cracks and leaks are procedural textures; real ones are messier. Fine-tune on your own ESP32-CAM frames before trusting any number on real pipes (`docs/TRAINING.md`).
* One map family, one random seed per sweep point, 450 test images: expect ±several points of noise.
* Hardware behaviour (traction, battery life, Wi-Fi range, ESP32-CAM image quality) was not measured; the firmware compiles and the protocol is tested against an emulator only.
