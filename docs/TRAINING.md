# Training the detector

The simulator labels itself: every crack/leak patch has a unique segmentation colour, so ground-truth boxes are pixel-exact and occlusion-aware, at no labelling cost.

## 1. Capture
```powershell
# 3 maps x 3 rovers x 4 random-defect seeds x 150 views  ->  ~5,400 images (~7 min with Webots minimised)
.venv\Scripts\python.exe -m tools.make_dataset capture --maps straight_t,star_five_goals,large_loop --rovers micro_2wd,mid_4wd,tracked_crawler --per-run 150 --seeds 4
.venv\Scripts\python.exe -m tools.make_dataset build --name sewer_v1      # COCO + YOLO train/valid/test
```
What varies: pipe shape/size, wall material, rover camera (height, tilt, FOV), headlamp 0.6-1.8×, exposure, chamber light, defect type/size/position, plus **hard negatives** that must *not* be flagged: pipe-joint rings, brick chambers, silt patches, goal posts. Views are 70 % aimed at a defect, 30 % random; labels are dropped when the defect is too dark to see or smaller than 10 px.

## 2. Train (RTX 5050, 8 GB; close Webots first)
```powershell
.venv\Scripts\python.exe -m tools.train_rfdetr --data datasets\sewer_v1 --size nano --epochs 30 --out models\rfdetr_nano_v1
```
The dashboard lists any `models\*\checkpoint*.pth` automatically and defaults to the newest.

## 3. Evaluate
```powershell
.venv\Scripts\python.exe -m tools.eval_detector --detector rfdetr --weights models\rfdetr_nano_v1 --split datasets\sewer_v1\test --sheet out.jpg
.venv\Scripts\python.exe -m tools.eval_detector --detector classical --split datasets\sewer_v1\test      # the baseline to beat
```
In the dashboard, a finished mission also scores itself against the planted defects (recall/precision of the *tracked* defects).

## 4. Close the sim-to-real gap (needed for real accuracy)
A model trained only on synthetic cracks will miss real ones. Plan:
1. Hardware tab → **Save frame** / auto-capture while driving a test pipe with printed or real crack/leak images → `datasets/real/*.jpg` (+ `.json` with the model's pre-labels).
2. Label in CVAT / Label Studio / Roboflow (check each tool's data terms) → COCO export.
3. Fine-tune: add the real images to a copy of `datasets/sewer_v1/train` (oversample them 3-5×) and resume from the sim checkpoint with a lower learning rate (`--lr 3e-5 --resume models\...`).
4. Keep a **real-only test split** and report that number, not the simulator's.
