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

from config import BASE_DIR, UPLOADS_DIR, DOWNLOADS_DIR, DEFAULT_CATEGORIES
from browser.manager import browser_manager
from browser.scripted_steps import run_scripted_steps, wait_until_generating, download_all_videos
from models.schemas import InputData, TaskStatus
from services.template import render_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

UPLOADS_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)

tasks: dict[str, TaskStatus] = {}
ws_connections: dict[str, list[WebSocket]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    video_count: int = Form(1),
    image_paths: str = Form(""),
    video_paths: str = Form(""),
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
        video_count=max(1, min(video_count, 20)),
        image_paths=[p.strip() for p in image_paths.split(",") if p.strip()],
        video_paths=[p.strip() for p in video_paths.split(",") if p.strip()],
    )

    tasks[task_id] = TaskStatus(
        task_id=task_id,
        status="pending",
        total_videos=input_data.video_count,
    )

    asyncio.create_task(_run_task(task_id, input_data))
    return {"task_id": task_id, "status": "started"}


async def _run_task(task_id: str, input_data: InputData):
    tasks[task_id].status = "running"
    try:
        page = await browser_manager.open_tiktok()
        conversation_titles = []

        # For each video: complete the full flow until "正在生成视频" before moving to next
        for i in range(input_data.video_count):
            tasks[task_id].current_video = i + 1
            tasks[task_id].message = f"[{i+1}/{input_data.video_count}] 提交中..."
            await _broadcast(task_id, tasks[task_id].model_dump())

            async def on_progress(msg: str):
                tasks[task_id].message = f"[{i+1}/{input_data.video_count}] {msg}"
                await _broadcast(task_id, tasks[task_id].model_dump())

            # Fill prompt, upload images, select trend, send
            title = await run_scripted_steps(page, input_data, trend_index=i, on_progress=on_progress)
            if title:
                conversation_titles.append(title)

            # Click replies until "正在生成视频" appears
            tasks[task_id].message = f"[{i+1}/{input_data.video_count}] 点击回复中，等待进入生成状态..."
            await _broadcast(task_id, tasks[task_id].model_dump())
            await wait_until_generating(page)

            tasks[task_id].message = f"[{i+1}/{input_data.video_count}] 已进入生成状态"
            await _broadcast(task_id, tasks[task_id].model_dump())

        # All submitted and generating, now wait and download only OUR conversations
        tasks[task_id].message = f"全部已进入生成状态，等待下载（每10分钟检查）..."
        await _broadcast(task_id, tasks[task_id].model_dump())
        logger.info("Tracking conversations: %s", conversation_titles)

        async def on_download_progress(msg: str):
            tasks[task_id].message = msg
            await _broadcast(task_id, tasks[task_id].model_dump())

        results = await download_all_videos(page, task_id, conversation_titles, on_progress=on_download_progress)
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
