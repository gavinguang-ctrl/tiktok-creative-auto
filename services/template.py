from __future__ import annotations

from models.schemas import InputData

TEMPLATE = """根据此 TikTok 趋势的灵感，根据以下提供的产品信息、参考图片和视频风格，为我的商品制作广告：
商品名称：{product_name}
售卖价格：{product_price}
商品详情：{product_details}
商品卖点：{selling_points}
售卖国家：{country}
售卖语言：{language}
其中
1、素材与风格参考
1）产品图片： 我已上传产品图片，请务必以此为准。
2）参考视频： 请参考我提供的趋势视频，模仿其风格。
2、制作要求：
1）尺寸与比例： 必须严格遵循我提供的产品图片中的尺寸比例，比如人物手部与产品的相对大小，确保真实感，切勿随意修改。
2）语言与字幕： 视频的口播/旁白必须使用我上面写到的售卖语言，并配上准确、地道的同语言字幕。{subtitle_note}
视频目标： 视频的整体节奏和叙事逻辑以突出卖点和激发购买欲为目标，力求成为热卖视频。
3）引导购买：视频文案或口播中，一定要包含引导用户点击链接购买"""


def render_prompt(data: InputData) -> str:
    subtitle_note = "" if data.subtitle_enabled else "\n注意：本视频不需要字幕。"
    return TEMPLATE.format(
        product_name=data.product_name,
        product_price=data.product_price,
        product_details=data.product_details,
        selling_points=data.selling_points,
        country=data.country,
        language=data.language,
        subtitle_note=subtitle_note,
    )
