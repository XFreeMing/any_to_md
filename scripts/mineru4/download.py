"""TV-01 download preparation, without importing MinerU, Torch or inference engines.

Required paths follow MinerU's mineru-4.0.12-released model/registry.py.
Hub SDK owns transport, partial files and resume; this script verifies content.
It never creates MinerU's completion markers or claims runtime readiness.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

from filelock import FileLock, Timeout
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub._local_folder import get_local_download_paths


ENDPOINTS = {"huggingface": "https://huggingface.co", "hf-mirror": "https://hf-mirror.com"}
TITLE_REVISION = "bc640142c66e1fdd12af0bd68f40445458f3869b"
TITLE_SHA256 = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
# (repository, local directory name, required files or directory prefixes, revision)
MODELS = (
    ("opendatalab/MinerU-4_models_torch", "MinerU-4_models_torch", (
        "Layout/PP-DocLayoutV2", "MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M.pth",
        "MFR/pp_formulanet_plus_m/PP-FormulaNet_plus-M_inference.yml", "OCR/paddleocr",
        "Table/slanet-plus.onnx", "Table/unet.onnx", "Table/PP-LCNet_x1_0_table_cls.onnx",
    ), None),
    ("jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF", "MinerU2.5-Pro-2605-1.2B-GGUF", (
        "MinerU2.5-Pro-2605-1.2B-Q8_0.gguf", "mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf",
    ), None),
    ("Qwen/Qwen3-4B-GGUF", "Qwen3-4B-GGUF", ("Qwen3-4B-Q4_K_M.gguf",), TITLE_REVISION),
)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def record_transfer(state: Path, model: dict, item: dict, endpoint: str, phase: str, **details: object) -> None:
    event = {
        "at": datetime.now(timezone.utc).isoformat(), "repo": model["repo"], "revision": model["revision"],
        "path": item["path"], "requested_endpoint": endpoint, "phase": phase, **details,
    }
    with (state / "download-provenance.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def local_path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or ".." in parts.parts or "\\" in name:
        raise ValueError(f"不安全的模型相对路径：{name}")
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"模型路径越过目录：{name}")
    return path


def build_plan(endpoint: str) -> dict:
    api = HfApi(endpoint=endpoint, token=False)
    models = []
    for repo, directory, required, revision in MODELS:
        print(f"读取模型清单：{repo}", flush=True)
        info = api.model_info(repo, revision=revision or "main", files_metadata=True)
        if not info.sha or not re.fullmatch(r"[0-9a-f]{40}", info.sha):
            raise ValueError(f"{repo} 未提供真实 revision")
        if revision and info.sha != revision:
            raise ValueError(f"{repo} revision 与固定版本不符")
        files = []
        for item in info.siblings or []:
            if not any(item.rfilename == path or item.rfilename.startswith(path + "/") for path in required):
                continue
            local_path(Path(directory), item.rfilename)
            algorithm = "sha256" if item.lfs else "git-sha1"
            digest = item.lfs.sha256 if item.lfs else item.blob_id
            if not digest or item.size is None or item.size <= 0:
                raise ValueError(f"缺少独立校验信息：{repo}/{item.rfilename}")
            if revision == TITLE_REVISION and digest != TITLE_SHA256:
                raise ValueError("标题模型的远程 SHA-256 与固定值不符")
            files.append({"path": item.rfilename, "size": item.size, "algorithm": algorithm, "digest": digest})
        for path in required:
            if not any(item["path"] == path or item["path"].startswith(path + "/") for item in files):
                raise ValueError(f"远程清单缺少必要路径：{repo}/{path}")
        models.append({"repo": repo, "directory": directory, "revision": info.sha, "files": files})
    return {"schema": 1, "metadata_endpoint": endpoint, "models": models}


def validate_plan(plan: dict) -> None:
    """Reject stale schemas or plans for different models before touching weights."""
    if plan.get("schema") != 1 or len(plan.get("models", [])) != len(MODELS):
        raise ValueError("下载清单格式不兼容；保留原清单，人工核对后再处理")
    for model, (repo, directory, required, revision) in zip(plan["models"], MODELS, strict=True):
        if model["repo"] != repo or model["directory"] != directory:
            raise ValueError("下载清单与本轮模型选择不符")
        if not re.fullmatch(r"[0-9a-f]{40}", model["revision"]) or (revision and model["revision"] != revision):
            raise ValueError(f"无效 revision：{repo}")
        names = [item["path"] for item in model["files"]]
        if len(names) != len(set(names)):
            raise ValueError(f"重复模型文件：{repo}")
        for path in required:
            if not any(name == path or name.startswith(path + "/") for name in names):
                raise ValueError(f"清单缺少必要路径：{path}")
        for item in model["files"]:
            local_path(Path(directory), item["path"])
            if not any(item["path"] == path or item["path"].startswith(path + "/") for path in required):
                raise ValueError(f"清单包含未选择的文件：{item['path']}")
            length = {"sha256": 64, "git-sha1": 40}.get(item["algorithm"])
            if not length or not re.fullmatch(f"[0-9a-f]{{{length}}}", item["digest"]) or item["size"] <= 0:
                raise ValueError(f"清单校验信息无效：{item['path']}")
            if revision and (item["algorithm"] != "sha256" or item["digest"] != TITLE_SHA256):
                raise ValueError("清单中的标题模型 SHA-256 与固定值不符")


def verify_file(path: Path, item: dict) -> str | None:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return None
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(f"blob {item['size']}\0".encode(), usedforsecurity=False)
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            sha256.update(chunk)
            if item["algorithm"] == "git-sha1":
                git_sha1.update(chunk)
    actual = sha256.hexdigest() if item["algorithm"] == "sha256" else git_sha1.hexdigest()
    return sha256.hexdigest() if actual == item["digest"] else None


def prepare(state: Path, args: argparse.Namespace) -> None:
    plan_path = state / "download-plan.json"
    endpoint = ENDPOINTS[args.source]
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
    if plan is None:
        if args.check:
            raise ValueError("还没有下载清单；先执行 uv run mineru4 download --dry-run 或 download")
        plan = build_plan(endpoint)
        validate_plan(plan)
        write_json(plan_path, plan)
    validate_plan(plan)
    print(f"清单：{plan_path}\n模型目录：{state / 'models'}\n本次来源：{endpoint}", flush=True)
    total = sum(item["size"] for model in plan["models"] for item in model["files"])
    print(f"共 {sum(len(model['files']) for model in plan['models'])} 个文件，{total / 1e9:.2f} GB；已固定 revision，重复执行复用校验通过的文件。", flush=True)
    if args.dry_run:
        for model in plan["models"]:
            print(f"  {model['repo']} @ {model['revision']}", flush=True)
        print("未下载权重；未判定模型就绪。", flush=True)
        return

    verified = []
    missing = []
    for model in plan["models"]:
        root = local_path(state / "models", model["directory"])
        for item in model["files"]:
            path = local_path(root, item["path"])
            print(f"校验：{model['directory']}/{item['path']}", flush=True)
            digest = verify_file(path, item)
            if digest is None and not args.check:
                # An invalid complete file must bypass Hub's local metadata cache.
                force = path.exists()
                # This internal path API is tied to our pinned 0.36 SDK. Let the
                # SDK own its cache naming; account only for bytes still needed.
                partial = get_local_download_paths(root, item["path"]).incomplete_path(item["digest"])
                resumed = min(partial.stat().st_size, item["size"]) if not force and partial.is_file() else 0
                remaining = item["size"] - resumed
                free = shutil.disk_usage(state).free
                if free < remaining + 256 * 1024 * 1024:
                    raise OSError(f"磁盘空间不足：{path.name} 还需要至少 {remaining / 1e9:.2f} GB 加安全余量")
                print(f"下载：{item['path']}（{item['size'] / 1e6:.1f} MB）", flush=True)
                record_transfer(state, model, item, endpoint, "requested", resumed_bytes=resumed)
                hf_hub_download(
                    model["repo"], item["path"], revision=model["revision"], local_dir=root,
                    endpoint=endpoint, token=False, force_download=force,
                )
                digest = verify_file(path, item)
                record_transfer(state, model, item, endpoint, "verified" if digest else "invalid", sha256=digest)
            if digest is None:
                missing.append(str(path))
                if not args.check:
                    raise ValueError(f"下载后文件校验失败：{path}；保留文件，下次重新下载")
            else:
                verified.append({"repo": model["repo"], "revision": model["revision"], "path": str(path), "size": item["size"], "sha256": digest})
    if missing:
        raise ValueError("文件缺失或校验失败：\n" + "\n".join(missing))
    provenance = state / "download-provenance.jsonl"
    write_json(state / "download-status.json", {
        "status": "verified", "runtime_verified": False, "checked_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "requested_endpoint": None if args.check else endpoint,
        "transfer_history": str(provenance) if provenance.exists() else None,
        "transfer_history_sha256": hashlib.sha256(provenance.read_bytes()).hexdigest() if provenance.exists() else None,
        "huggingface_hub": importlib.metadata.version("huggingface-hub"), "files": verified,
    })
    print(f"全部模型文件校验通过。记录：{state / 'download-status.json'}\n尚未安装或验证推理运行环境。", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=ENDPOINTS, default="huggingface")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    state = Path(__file__).resolve().parents[2] / "var" / "mineru4"
    try:
        state.mkdir(parents=True, exist_ok=True)
        with FileLock(str(state / "download.lock"), timeout=0):
            try:
                if not args.dry_run:
                    write_json(state / "download-status.json", {"status": "checking", "runtime_verified": False})
                prepare(state, args)
            except BaseException as exc:
                if not args.dry_run:
                    write_json(state / "download-status.json", {
                        "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                        "runtime_verified": False, "error": str(exc),
                    })
                raise
    except KeyboardInterrupt:
        print("已中断，保留文件与续传缓存；重新执行同一命令可继续。", file=sys.stderr)
        return 130
    except Timeout:
        print("另一个模型下载或校验命令正在运行；请等待它结束。", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"模型准备失败：{exc}\n未标记就绪；保留已下载文件，可重试或显式选择 --source hf-mirror。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
