from __future__ import annotations

from typing import Any


PORCELAIN_CAPTURE_PROTOCOL: dict[str, Any] = {
    "id": "porcelain-v1",
    "version": "1.0.0",
    "title": "瓷器现场五视角采集",
    "purpose": "为本地多模态模型提供方向明确、可复查的现场图像输入",
    "required_for_standard": 3,
    "recommended_views": [
        {
            "view_code": "FRONT",
            "label": "正面",
            "instruction": "器物完整入框，镜头与器身中部大致平齐。",
        },
        {
            "view_code": "BACK",
            "label": "背面",
            "instruction": "转动器物或移动相机，保持距离和光线基本一致。",
        },
        {
            "view_code": "LEFT_PROFILE",
            "label": "侧面",
            "instruction": "展示轮廓、口沿、肩腹和足部关系。",
        },
        {
            "view_code": "TOP",
            "label": "口沿与内部",
            "instruction": "从上方展示口沿、内壁和可见使用痕迹。",
        },
        {
            "view_code": "BASE",
            "label": "底足",
            "instruction": "展示底足、胎体、款识或标签；注意防滑和托持。",
        },
    ],
    "optional_views": ["DETAIL", "MARK", "DAMAGE"],
    "device_checks": [
        "resolution",
        "exposure",
        "clipping",
        "sharpness",
    ],
    "future_device_checks": ["object_coverage"],
    "scientific_boundary": (
        "Scout 端只提示采集质量与视角，不在端侧输出真伪、年代、窑口、作者或价格。"
    ),
}


def capture_protocol() -> dict[str, Any]:
    return {
        **PORCELAIN_CAPTURE_PROTOCOL,
        "recommended_views": [
            dict(item) for item in PORCELAIN_CAPTURE_PROTOCOL["recommended_views"]
        ],
        "optional_views": list(PORCELAIN_CAPTURE_PROTOCOL["optional_views"]),
        "device_checks": list(PORCELAIN_CAPTURE_PROTOCOL["device_checks"]),
        "future_device_checks": list(
            PORCELAIN_CAPTURE_PROTOCOL["future_device_checks"]
        ),
    }
