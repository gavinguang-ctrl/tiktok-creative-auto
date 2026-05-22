from __future__ import annotations

from pydantic import BaseModel


class InputData(BaseModel):
    product_name: str = ""
    product_price: str = ""
    product_details: str = ""
    selling_points: str = ""
    product_link: str = ""
    country: str = ""
    language: str = ""
    subtitle_enabled: bool = True
    category: str = ""
    sub_category: str = ""
    video_count: int = 1
    start_trend_index: int = 0
    image_paths: list[str] = []
    video_paths: list[str] = []
    custom_prompt: str = ""


class TaskStatus(BaseModel):
    task_id: str
    status: str  # pending, running, completed, failed
    current_step: int = 0
    total_steps: int = 0
    current_video: int = 0
    total_videos: int = 1
    message: str = ""
    result_paths: list[str] = []
