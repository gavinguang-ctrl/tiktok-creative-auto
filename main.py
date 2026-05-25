from __future__ import annotations

import asyncio
import sys
import uuid
import logging
from pathlib import Path
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from config import BASE_DIR, UPLOADS_DIR, DOWNLOADS_DIR, DEFAULT_CATEGORIES, DATA_DIR, SUBCATEGORIES_FILE
from browser.manager import browser_manager
from browser.scripted_steps import run_scripted_steps, wait_until_generating, download_all_videos, scrape_all_subcategories
from models.schemas import InputData, TaskStatus
from services.template import render_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

UPLOADS_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

tasks: dict[str, TaskStatus] = {}
ws_connections: dict[str, list[WebSocket]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify event loop supports subprocess (required by Playwright)
    loop = asyncio.get_running_loop()
    logger.info("Event loop type: %s", type(loop).__name__)
    yield
    await browser_manager.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


# --- Run API ---

@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    paths = []
    for f in files:
        save_path = UPLOADS_DIR / f.filename
        save_path.write_bytes(await f.read())
        paths.append(str(save_path))
    return {"paths": paths}


@app.get("/api/categories")
async def get_categories():
    return {"categories": DEFAULT_CATEGORIES}


@app.get("/api/subcategories")
async def get_subcategories():
    """Return cached subcategories data."""
    import json
    if SUBCATEGORIES_FILE.exists():
        return json.loads(SUBCATEGORIES_FILE.read_text(encoding="utf-8"))
    return {}


@app.post("/api/scrape-subcategories")
async def scrape_subcategories_api():
    """Scrape sub-categories from TikTok trend modal for all industries."""
    import json
    try:
        page = await browser_manager.open_tiktok()
        result = await scrape_all_subcategories(page, DEFAULT_CATEGORIES)
        SUBCATEGORIES_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Scrape subcategories failed")
        return JSONResponse({"error": f"{type(e).__name__}: {e}", "traceback": tb}, status_code=500)


@app.post("/api/preview-prompt")
async def preview_prompt(
    product_name: str = Form(""),
    product_price: str = Form(""),
    product_details: str = Form(""),
    selling_points: str = Form(""),
    product_link: str = Form(""),
    country: str = Form(""),
    language: str = Form(""),
    subtitle_enabled: bool = Form(True),
):
    data = InputData(
        product_name=product_name,
        product_price=product_price,
        product_details=product_details,
        selling_points=selling_points,
        product_link=product_link,
        country=country,
        language=language,
        subtitle_enabled=subtitle_enabled,
    )
    return {"prompt": render_prompt(data)}


@app.post("/api/run")
async def run_workflow(
    product_name: str = Form(""),
    product_price: str = Form(""),
    product_details: str = Form(""),
    selling_points: str = Form(""),
    product_link: str = Form(""),
    country: str = Form(""),
    language: str = Form(""),
    subtitle_enabled: bool = Form(True),
    category: str = Form(""),
    sub_category: str = Form(""),
    video_count: int = Form(1),
    start_trend_index: int = Form(0),
    image_paths: str = Form(""),
    video_paths: str = Form(""),
    custom_prompt: str = Form(""),
):
    task_id = uuid.uuid4().hex[:8]
    input_data = InputData(
        product_name=product_name,
        product_price=product_price,
        product_details=product_details,
        selling_points=selling_points,
        product_link=product_link,
        country=country,
        language=language,
        subtitle_enabled=subtitle_enabled,
        category=category,
        sub_category=sub_category,
        video_count=max(1, min(video_count, 20)),
        start_trend_index=max(0, start_trend_index),
        image_paths=[p.strip() for p in image_paths.split(",") if p.strip()],
        video_paths=[p.strip() for p in video_paths.split(",") if p.strip()],
        custom_prompt=custom_prompt,
    )

    tasks[task_id] = TaskStatus(
        task_id=task_id,
        status="pending",
        total_videos=input_data.video_count,
    )

    asyncio.create_task(_run_task(task_id, input_data))
    return {"task_id": task_id, "status": "started"}


async def _run_task(task_id: str, input_data: InputData):
    from datetime import datetime
    tasks[task_id].status = "running"
    task_started_at = datetime.now()
    try:
        page = await browser_manager.open_tiktok()
        successful_submissions = 0

        # For each video: complete the full flow until "正在生成视频" before moving to next
        for i in range(input_data.video_count):
            if tasks[task_id].status == "stopped":
                logger.info("Task %s stopped by user", task_id)
                break

            trend_idx = input_data.start_trend_index + i
            retry_count = 0
            max_retries = 2
            success = False

            while retry_count <= max_retries and not success:
                if tasks[task_id].status == "stopped":
                    break
                tasks[task_id].current_video = i + 1
                attempt_msg = f" (重试{retry_count})" if retry_count > 0 else ""
                tasks[task_id].message = f"[{i+1}/{input_data.video_count}]{attempt_msg} 提交中..."
                await _broadcast(task_id, tasks[task_id].model_dump())

                async def on_progress(msg: str):
                    tasks[task_id].message = f"[{i+1}/{input_data.video_count}]{attempt_msg} {msg}"
                    await _broadcast(task_id, tasks[task_id].model_dump())

                # Fill prompt, upload images, select trend, send
                try:
                    title = await run_scripted_steps(page, input_data, trend_index=trend_idx, on_progress=on_progress)
                except Exception as e:
                    logger.warning("Round %d attempt %d scripted steps failed: %s", i+1, retry_count+1, e)
                    retry_count += 1
                    continue

                if not title:
                    logger.warning("Round %d attempt %d returned no title", i+1, retry_count+1)
                    retry_count += 1
                    continue

                # Click replies until "正在生成视频" appears
                try:
                    tasks[task_id].message = f"[{i+1}/{input_data.video_count}]{attempt_msg} 点击回复中，等待进入生成状态..."
                    await _broadcast(task_id, tasks[task_id].model_dump())
                    generating = await wait_until_generating(page)
                    if generating:
                        successful_submissions += 1
                        tasks[task_id].message = f"[{i+1}/{input_data.video_count}] 已进入生成状态"
                        await _broadcast(task_id, tasks[task_id].model_dump())
                        success = True
                    else:
                        logger.warning("Round %d attempt %d: stuck waiting for generation", i+1, retry_count+1)
                        retry_count += 1
                except Exception as e:
                    logger.warning("Round %d attempt %d wait_until_generating failed: %s", i+1, retry_count+1, e)
                    retry_count += 1

            if not success:
                logger.warning("Round %d failed after %d retries, skipping", i+1, max_retries)
                tasks[task_id].message = f"[{i+1}/{input_data.video_count}] 重试{max_retries}次仍失败，跳过"
                await _broadcast(task_id, tasks[task_id].model_dump())

        # All submitted and generating, now navigate to history page and download
        if tasks[task_id].status == "stopped":
            return

        if successful_submissions == 0:
            tasks[task_id].status = "failed"
            tasks[task_id].message = "所有提交都失败，无任务可下载"
            await _broadcast(task_id, tasks[task_id].model_dump())
            return

        tasks[task_id].message = f"全部已提交（成功 {successful_submissions}），切换到历史页等待下载..."
        await _broadcast(task_id, tasks[task_id].model_dump())
        logger.info("Task %s: %d successful submissions, started at %s", task_id, successful_submissions, task_started_at)

        async def on_download_progress(msg: str):
            tasks[task_id].message = msg
            await _broadcast(task_id, tasks[task_id].model_dump())

        results = await download_all_videos(
            page, task_id, task_started_at, successful_submissions,
            on_progress=on_download_progress,
        )
        tasks[task_id].result_paths = results

        tasks[task_id].status = "completed"
        tasks[task_id].message = f"全部完成! 共下载 {len(results)} 个视频"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        tasks[task_id].status = "failed"
        tasks[task_id].message = f"{type(e).__name__}: {e}\n{tb}"
        logger.exception("Task %s failed", task_id)
    await _broadcast(task_id, tasks[task_id].model_dump())


@app.get("/api/run/{task_id}/status")
async def task_status(task_id: str):
    if task_id not in tasks:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return tasks[task_id].model_dump()


@app.post("/api/run/{task_id}/stop")
async def stop_task(task_id: str):
    t = tasks.get(task_id)
    if not t:
        return JSONResponse({"error": "Not found"}, status_code=404)
    t.status = "stopped"
    t.message = "用户终止任务"
    await _broadcast(task_id, t.model_dump())
    return {"status": "stopped"}


@app.get("/api/run/{task_id}/results")
async def task_results(task_id: str):
    t = tasks.get(task_id)
    if not t:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"results": [{"index": i + 1, "path": p} for i, p in enumerate(t.result_paths)]}


@app.get("/api/run/{task_id}/result/{index}")
async def task_result_download(task_id: str, index: int):
    t = tasks.get(task_id)
    if not t or index < 1 or index > len(t.result_paths):
        return JSONResponse({"error": "No result"}, status_code=404)
    path = t.result_paths[index - 1]
    return FileResponse(path, filename=Path(path).name)


# --- WebSocket ---

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    ws_connections.setdefault(task_id, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_connections[task_id].remove(websocket)


async def _broadcast(task_id: str, data: dict):
    import json
    for ws in ws_connections.get(task_id, []):
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
