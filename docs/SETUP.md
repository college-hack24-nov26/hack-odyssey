# Setup (Windows 10/11)

Tested on: Windows 11, Intel Core 7 240H, RTX 5050 Laptop (8 GB), 24 GB RAM, Python 3.11, Webots R2025a.

## 1. Python environment
```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe numpy opencv-python pillow pyyaml fastapi "uvicorn[standard]" websockets requests pytest httpx
.venv\Scripts\python.exe -m pytest -q          # 350+ tests, no Webots or hardware needed
```
Python 3.11 is used for everything (Webots' R2025a Python API is pure Python over `Controller.dll`, so any 3.8+ works, but 3.11 is the safest match for PyTorch + RF-DETR).

## 2. Webots R2025a (simulator)
Install the stable **R2025a** installer from <https://github.com/cyberbotics/webots/releases/tag/R2025a> ("Install for me only"). On first launch Webots shows a welcome dialog with an optional telemetry checkbox: untick it if you prefer. The dashboard and tools find `webots.exe` under `%LOCALAPPDATA%\Programs\Webots`; set `WEBOTS_EXE` if it lives elsewhere.

`sim/webots/controllers/sewerbot_rover/runtime.ini` points Webots at this project's venv Python. If you move the project, edit the path in that file.

Webots uses the NVIDIA GPU automatically on this laptop (verified with `nvidia-smi`). If yours picks the Intel iGPU: Windows Settings → System → Display → Graphics → add `webots-bin.exe` → *High performance*.

## 3. GPU stack for the learned detector (RTX 50-series needs CUDA 12.8+)
Blackwell GPUs (RTX 50xx, compute capability 12.0) only work with PyTorch builds made for **CUDA 12.8 or newer**; older `cu121`/`cu124` builds fail with "no kernel image".
```powershell
uv pip install --python .venv\Scripts\python.exe torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv\Scripts\python.exe rfdetr supervision
.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
The CUDA wheel is ~2.5-3 GB. On a slow connection `uv` can hit its stall timeout; download the wheel with a resumable `curl -C -` and install the local file instead (see the PyTorch index page for the URL and SHA-256).

## 3b. simlab (MuJoCo simulator, reinforcement learning)
```powershell
uv pip install --python .venv\Scripts\python.exe mujoco gymnasium stable-baselines3 tensorboard
```
MuJoCo (Apache-2.0) has a Windows wheel and renders offscreen through your normal OpenGL driver; no extra setup. Reinforcement learning runs on the CPU (one MuJoCo process per simulator, 8 by default); the GPU is used by the camera-detector training. Optional: `config/plugins.txt` lists the plug-ins that load at start (the boat); remove a line to disable one.

## 4. Disk budget
Webots ≈ 1.7 GB, venv with CUDA PyTorch ≈ 6 GB, a 5,400-image dataset ≈ 2 GB (raw + COCO + YOLO copies). Remove `datasets/raw` after building the splits to save ~0.7 GB. A simlab detector run is ≈ 125 MB (it keeps only the best weights; RF-DETR itself writes ~1 GB of checkpoints while it trains, which the pipeline then deletes), an RL run ≈ 1 MB.

## 5. Run
```powershell
.venv\Scripts\python.exe -m sewerbot                 # dashboard at http://127.0.0.1:8000
.venv\Scripts\python.exe -m tools.gen_world maps\straight_t.yaml --rover mid_4wd    # just build a world
.venv\Scripts\python.exe -m tools.webots_runner check --map straight_t               # drive/turn checks for every rover
```
