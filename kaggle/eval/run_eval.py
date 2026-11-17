"""
Gold-set evaluation on Kaggle's GPUs, started from your laptop (see README, "Evaluating on Kaggle").

Kaggle runs this as a batch job on GPU T4 x2: it installs the project from its lock files in a
Python 3.12 venv, starts Ollama on the second GPU, and runs "eval run" for each of RUNS with PaddleOCR
on the first. Runs on the same gold set and model are then compared with McNemar's test. Results and
logs go to /kaggle/working/eval_results, which "kaggle kernels output" downloads; the venv and the
code stay in /tmp, so they aren't downloaded too.
"""

import itertools
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# (name, gold set, OCR_MODE, model)
RUNS = [
    ("layout_ppocr", "goldset_layout", "ppocr", "qwen2.5:7b"),
    ("layout_hybrid", "goldset_layout", "hybrid", "qwen2.5:7b"),
]
OCR_MODEL_SIZE = "medium"  # the most accurate PP-OCRv6; fast enough on a GPU
OLLAMA_NUM_CTX = 32768

APP, VENV = Path("/tmp/je/JE_Extractor"), Path("/tmp/je/venv")
RESULTS = Path("/kaggle/working/eval_results")
PY = str(VENV / "bin" / "python")
LOCKS = ["requirements.lock", "requirements-ocr.lock", "requirements-vl.lock"]


def sh(*cmd, **kw):
    print("$", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kw)


def get_code():
    """The code dataset, as uploaded (kaggle/eval/push.ps1 code): a zip, or its files if Kaggle unzipped it."""
    found = [p.parent.parent for p in Path("/kaggle/input").rglob("je_validator/__main__.py")]
    if found:
        shutil.copytree(found[0], APP)
        return
    archives = list(Path("/kaggle/input").rglob("*.zip"))
    if not archives:
        sys.exit("The code isn't in /kaggle/input: upload it with kaggle/eval/push.ps1 code")
    with zipfile.ZipFile(archives[0]) as z:
        z.extractall(APP)


def wait_for(url, what):
    for _ in range(120):
        try:
            urllib.request.urlopen(url)
            return
        except urllib.error.HTTPError:
            return  # it answered: it is up
        except OSError:
            time.sleep(1)
    sys.exit(f"{what} didn't start")


gpus = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
                      capture_output=True, text=True).stdout.strip().splitlines()
print("\n".join(gpus) or "No GPU", flush=True)
if not gpus or any(float(g.split(",")[1]) < 7.0 for g in gpus):
    sys.exit("PaddlePaddle needs a GPU with compute capability 7.0+: push with --accelerator NvidiaTeslaT4")
OCR_GPU, OLLAMA_GPU = "0", "1" if len(gpus) > 1 else "0"

get_code()
RESULTS.mkdir(parents=True, exist_ok=True)

# The same pinned packages as on the laptop; PaddlePaddle for CUDA 12.6 from its own index.
sh(sys.executable, "-m", "pip", "install", "-q", "uv")
sh(sys.executable, "-m", "uv", "venv", "-q", "--seed", "-p", "3.12", VENV)
sh(PY, "-m", "pip", "install", "-q", "paddlepaddle-gpu==3.2.1", "-i", "https://www.paddlepaddle.org.cn/packages/stable/cu126/")
sh(sys.executable, "-m", "uv", "pip", "install", "-q", "--python", PY, "--require-hashes",
   *[a for lock in LOCKS for a in ("-r", APP / lock)])
sh(PY, "-c", "import paddle; paddle.utils.run_check()", env={**os.environ, "CUDA_VISIBLE_DEVICES": OCR_GPU})

sh("bash", "-c", "apt-get -qq install -y zstd > /dev/null && curl -fsSL https://ollama.com/install.sh | sh")
subprocess.Popen(["ollama", "serve"], stdout=open(RESULTS / "ollama.log", "w"), stderr=subprocess.STDOUT,
                 env={**os.environ, "CUDA_VISIBLE_DEVICES": OLLAMA_GPU,
                      "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"})
wait_for("http://127.0.0.1:11434", "Ollama")
for model in sorted({r[3] for r in RUNS}):
    sh("ollama", "pull", model, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

env = {**os.environ, "CUDA_VISIBLE_DEVICES": OCR_GPU, "OCR_DEVICE": "gpu:0", "OCR_MODEL_SIZE": OCR_MODEL_SIZE,
       "LLM_PROVIDER": "ollama", "OLLAMA_NUM_CTX": str(OLLAMA_NUM_CTX), "TEMPLATES_DB": "/tmp/je/templates.db"}
sh(PY, "-m", "je_validator", "templates", "add", *sorted((APP / "sample_templates").glob("*.json")), cwd=APP, env=env)

for name, gold, mode, model in RUNS:
    print(f"\n=== {name}: {gold}, OCR_MODE={mode}, {model}", flush=True)
    with open(RESULTS / f"{name}.log", "w") as log:
        p = subprocess.Popen([PY, "-m", "je_validator", "eval", "run", "--gold", gold, "--model", model,
                              "--out", str(RESULTS / name)], cwd=APP, env={**env, "OCR_MODE": mode},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            print(line, end="", flush=True)
            log.write(line)
        print(f"=== {name} finished with exit code {p.wait()}", flush=True)

for a, b in itertools.combinations(RUNS, 2):
    if a[1] == b[1] and a[3] == b[3] and (RESULTS / a[0] / "report.json").exists() and (RESULTS / b[0] / "report.json").exists():
        out = subprocess.run([PY, "-m", "je_validator", "eval", "compare", RESULTS / a[0], RESULTS / b[0]],
                             cwd=APP, env=env, capture_output=True, text=True).stdout
        print(out)
        (RESULTS / f"compare_{a[0]}_vs_{b[0]}.txt").write_text(out)
