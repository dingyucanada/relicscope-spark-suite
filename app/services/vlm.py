from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Dict, Iterable, Optional, Set

import httpx


VISION_SYSTEM_PROMPT = """You are a cultural-heritage imaging observation assistant.
Return JSON only with keys: observations (array of short visible facts), suggested_regions
(array with label and reason), limitations (array), ood_risk (LOW|MEDIUM|HIGH).
Use Chinese for every natural-language field.
Do not authenticate, date, price, or identify the object as genuine or fake. Distinguish
visible observations from hypotheses. The application will make all scientific decisions."""

REASONER_SYSTEM_PROMPT = """You draft a concise scientific review note from a structured
RelicScope evidence package. Return JSON only with keys: summary, limitations, next_steps,
citation_ids. citation_ids must be an array containing only source_id values already present
in the evidence package; use an empty array when no local knowledge claim is used. Never
claim authenticity, legal status, age, attribution, or market value. Preserve the
application's evidence states and abstention decision exactly. Use Chinese for every
natural-language field."""

NATIVE_VIDEO_SYSTEM_PROMPT = """You are a cultural-heritage video observation assistant.
Return JSON only with keys: observations (array of visible facts), temporal_observations
(array of changes across viewpoints or time), suggested_regions (array with label and
reason), limitations (array), ood_risk (LOW|MEDIUM|HIGH). Do not authenticate, date,
price, attribute, or identify the object as genuine or fake. State when motion, focus,
lighting, occlusion, or incomplete coverage limits the observation. Use Chinese for all
natural-language fields when supported; otherwise use English consistently and record the
language limitation. The application makes all scientific decisions."""

SCOUT_MULTI_VIEW_SYSTEM_PROMPT = """You are a cultural-heritage multi-view observation
assistant. Return JSON only with keys: observations (array of objects containing exactly
capture_id, view_code, text), cross_view_observations (array of short visible facts),
limitations (array of strings), capture_issues (array of objects containing capture_id and
issue), ood_risk (LOW|MEDIUM|HIGH). Use only capture_id and view_code values provided by
the application. Use Chinese for natural-language fields. Record visible shape,
decoration, glaze, base, mark, condition, and capture limits. A literal mark or inscription
may be copied only as `逐字转录：「...」`; do not interpret it. Do not authenticate, date,
price, attribute, identify kiln, identify a production centre, or identify the object as
genuine or fake. Do not use auction, collecting, connoisseurship, or art-market verdict
jargon."""

SCOUT_OBSERVATION_INSTRUCTION = (
    "将所有合格视角作为同一器物的一组观察输入；仅记录可见形态、"
    "纹饰、釉面、底足、款识、保存状态与跨视角一致性。不得输出真伪、"
    "断代、窑口、作者或价格结论。操作员标签未经验证，不可作为模型观察"
    "或结论依据。"
)


def model_request_options(model: str, *, video: bool = False) -> Dict[str, Any]:
    """Return model-card-specific options shared by runtime and acceptance tools."""

    normalized = model.lower()
    if "qwen3.6" in normalized or "qwen3.8" in normalized:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if "nemotron" not in normalized or "omni" not in normalized:
        return {}
    options: Dict[str, Any] = {
        "top_k": 1,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if video:
        options["mm_processor_kwargs"] = {"use_audio_in_video": False}
    return options


def scout_response_format(allowed_captures: Dict[str, str]) -> Dict[str, Any]:
    """Build the NIM/vLLM JSON-schema contract for one Scout request.

    NVIDIA recommends ``json_schema`` instead of the weaker ``json_object``
    mode because the latter also permits an empty object. The server still
    performs its own semantic checks after decoding: the schema cannot express
    the exact capture_id-to-view_code pairing or the scientific conclusion
    boundary.
    """

    capture_ids = list(allowed_captures)
    view_codes = list(dict.fromkeys(allowed_captures.values()))
    text = {"type": "string", "minLength": 1, "maxLength": 500}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "RelicScopeScoutObservationV2",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "observations": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "capture_id": {
                                    "type": "string",
                                    "enum": capture_ids,
                                },
                                "view_code": {
                                    "type": "string",
                                    "enum": view_codes,
                                },
                                "text": text,
                            },
                            "required": ["capture_id", "view_code", "text"],
                        },
                    },
                    "cross_view_observations": {
                        "type": "array",
                        "maxItems": 32,
                        "items": text,
                    },
                    "limitations": {
                        "type": "array",
                        "maxItems": 32,
                        "items": text,
                    },
                    "capture_issues": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "capture_id": {
                                    "type": "string",
                                    "enum": capture_ids,
                                },
                                "issue": text,
                            },
                            "required": ["capture_id", "issue"],
                        },
                    },
                    "ood_risk": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH"],
                    },
                },
                "required": [
                    "observations",
                    "cross_view_observations",
                    "limitations",
                    "capture_issues",
                    "ood_risk",
                ],
            },
        },
    }


def model_output_hash(value: Dict[str, Any]) -> str:
    """Hash validated model output exactly as the production evidence record does."""

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_scout_multi_view_payload(
    model: str,
    images: list[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Build the exact production Scout request and its capture allowlist."""

    if not 1 <= len(images) <= 8:
        raise ValueError("Scout multi-view request must contain one to eight images")
    allowed_captures: Dict[str, str] = {}
    content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Inspect all views as one object-observation set. Bind every per-view "
                "observation to the exact capture identifier and declared view. "
                "Context metadata: "
                + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            ),
        }
    ]
    for item in images:
        capture_id = str(item["capture_id"])
        view_code = str(item["view_code"])
        image_data_url = str(item["image_data_url"])
        if capture_id in allowed_captures:
            raise ValueError("Scout multi-view capture identifiers must be unique")
        allowed_captures[capture_id] = view_code
        content.extend(
            [
                {
                    "type": "text",
                    "text": f"capture_id={capture_id}; view_code={view_code}",
                },
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        )
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SCOUT_MULTI_VIEW_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 1200,
        "response_format": scout_response_format(allowed_captures),
    }
    payload.update(model_request_options(model))
    return payload, allowed_captures

FORBIDDEN_VERDICT_PATTERNS = [
    r"(?:真品|赝品|仿品|高仿|伪作|真迹|正品|复制品|摹本)",
    r"(?:确定|明确|判定|认定|断定)(?:该|此|其|本)?(?:器物|作品)?(?:的)?(?:年代|时期|朝代|作者|创作者|作者归属|归属|窑口)",
    r"(?:年代|时期|朝代|作者|创作者|作者归属|归属|窑口)(?:已经|已|可以|可)?(?:确定|明确|判定|认定|断定|为|是|属于)",
    r"(?:出自|创作于|制作于|烧造于).{0,20}(?:年|世纪|朝|代|时期|窑|作者|画家|书家|工匠|作坊)",
    r"(?:作品|器物|书画|画作|此画|该作).{0,4}(?:出自|归属于)",
    r"(?:该|此|本)(?:器物|作品|画作).{0,4}(?:为|是|属于)(?:唐|宋|元|明|清|民国)(?:代|朝|时期)",
    r"(?:估价|市场价格|价值为|售价)",
    r"(?:法律鉴定|行政认定|文物定级为)",
    r"(?:可能|疑似|似为|或为|推测|倾向于).{0,24}(?:真品|赝品|仿制|伪作|复制|唐代|宋代|元代|明代|清代|民国|窑口|窑烧)",
    r"(?:唐|宋|元|明|清)(?:代|朝|时期).{0,16}(?:风格|制式|烧造|制作|器物|作品)",
    r"(?:景德镇|龙泉|汝|官|哥|定|钧|越|德化|磁州|耀州|建|吉州).{0,3}窑",
    r"(?:现代|当代|近代).{0,12}(?:仿|复制|伪)",
    r"\b(?:authentic(?:ity)?|genuine(?:ness)?|counterfeit|fake|forg(?:ery|ed)|replica|imitation|reproduction|facsimile)\b",
    r"\b(?:dates?|dated|dating)\s+(?:to|from)\b",
    r"\b(?:made|created|painted|produced|manufactured|fired)\s+(?:in|during|circa|c\.)\s+(?:the\s+)?(?:\d{3,4}|\d{1,2}(?:st|nd|rd|th)[- ]century|[a-z]+\s+dynasty)",
    r"\b(?:attributed|ascribed)\s+to\b",
    r"\b(?:created|painted|made|signed|authored)\s+by\b",
    r"\b(?:work|painting|artifact|object)\s+(?:is|was)\s+by\b",
    r"\b(?:object|artifact|work|painting)\s+(?:is|was)\s+from\s+(?:the\s+)?(?:[a-z]+\s+dynasty|\d{3,4}|\d{1,2}(?:st|nd|rd|th)[- ]century)",
    r"\b(?:artist|author|maker|painter|workshop)\s+(?:is|was|:)\b",
    r"\b(?:market value|legally certified)\b",
    # A named chronology is itself a dating claim in an observation-only result. Literal
    # inscriptions are removed by _redact_literal_marks before these checks run.
    r"(?:先秦|秦汉|汉代|魏晋|南北朝|隋代|唐代|五代|宋代|辽代|金代|元代|明代|清代|民国(?:时期)?)",
    r"(?:明|清)(?:初|中|晚|末|早期|中期|晚期|朝|代)",
    r"(?:洪武|建文|永乐|洪熙|宣德|正统|景泰|天顺|成化|弘治|正德|嘉靖|隆庆|万历|泰昌|天启|崇祯|顺治|康熙|雍正|乾隆|嘉庆|道光|咸丰|同治|光绪|宣统)(?:年间|时期|朝|代|年制|年造)?",
    r"(?:约|大约|公元)?\s*(?:第)?[0-9一二三四五六七八九十百]{1,4}\s*世纪(?:上半叶|下半叶|早期|中期|晚期|前后)?",
    r"(?:公元前?\s*)?\d{3,4}\s*年(?:前后|左右|代)?",
    # Kiln/production-centre attribution, including common shorthand without '窑'.
    r"[一-鿿]{1,8}窑(?:口|系|场|址|烧|造|产|器)?",
    r"(?:景德镇|龙泉|德化|磁州|耀州|吉州|建阳|越州)(?:制|产|烧|造|系|风格)?",
    r"(?:窑口|产地|烧造地|作坊).{0,16}(?:为|是|属于|可能|疑似|推测|倾向|指向|符合)",
    # Connoisseurship and art-market shorthand can smuggle an authenticity or value
    # verdict without using the formal words above.
    r"(?:大开门|开门货|(?:很|颇|十分|比较)?开门(?:器|度高|特征|感强|[，。；、！？\s]|$)|一眼真|一眼老|看真|到代|保到代|包老|保真|老货|老器|行货|生坑|熟坑|传世品|原装|老气|贼光|火气|新仿|后仿|臆造|做旧|改款|后加彩|接底|国宝帮)",
    r"(?:估值|估价|市场价|成交价|拍卖价|行情|保值|升值空间|收藏价值|投资价值|值得收藏|捡漏)",
    r"(?:价值|价格|售价|身价).{0,12}(?:人民币|港币|美元|欧元|英镑|元|万|千|百)",
    # English chronology, including hedged dating and style-based dating.
    r"\b(?:shang|zhou|qin|han|sui|tang|song|liao|jin|yuan|ming|qing)[- ]+(?:dynasty|period|era|style)\b",
    r"\b(?:early|mid(?:dle)?|late)[- ]+(?:shang|zhou|qin|han|sui|tang|song|liao|jin|yuan|ming|qing)\b",
    r"\b(?:hongwu|yongle|xuande|chenghua|jiajing|wanli|shunzhi|kangxi|yongzheng|qianlong|jiaqing|daoguang|xianfeng|tongzhi|guangxu|xuantong)(?:[- ](?:period|era|reign|style))?\b",
    r"\b(?:early|mid(?:dle)?|late)?[- ]*\d{1,2}(?:st|nd|rd|th)[- ]century\b",
    r"\b(?:circa|ca\.?|c\.)\s*\d{3,4}\b|\b\d{3,4}s\b|\b\d{3,4}\s*(?:ce|ad|bce|bc)\b",
    r"\b(?:may|might|could|probably|possibly|likely|apparently|seemingly)\b.{0,40}\b(?:dynasty|period|era|century|dated?|dating|made|fired|produced)\b",
    r"\b(?:may be|might be|could be|probably|possibly|likely|appears? to be|seems? to be)\b.{0,32}\b(?:shang|zhou|qin|han|sui|tang|song|liao|jin|yuan|ming|qing|antique|period piece)\b",
    r"\b(?:appears?|seems?|suggests?|indicates?|consistent with|indicative of)\b.{0,40}\b(?:dynasty|period|era|century|date|dating|reign|antique)\b",
    r"\b(?:jingdezhen|longquan|dehua|cizhou|yaozhou|jian|jizhou|ru|guan|ge|ding|jun)\b.{0,16}\b(?:kiln|ware|workshop|production|made|fired)\b",
    r"\b(?:likely|probably|possibly|perhaps|apparently)\b.{0,24}\b(?:jingdezhen|longquan|dehua|cizhou|yaozhou|jian|jizhou)\b",
    r"\b(?:auction estimate|auction record|market price|market value|collectible|investment[- ]grade|museum[- ]quality|blue[- ]chip|appraisal value|replacement value)\b",
    r"(?:[$¥€£]|\b(?:usd|cny|rmb|hkd|eur|gbp)\b)\s*\d[\d,.]*",
]


_LITERAL_MARK_PATTERN = re.compile(
    r"(?:款识|底款|款文|铭文|印文|题款|落款|戳记|mark|inscription)"
    r"\s*(?:文字)?\s*(?:逐字)?\s*(?:转录|可见|读作|reads?|transcription)?"
    r"\s*(?:为|是|[:：])?\s*[「『“\"']([^\u300d』”\"']{1,120})[」』”\"']",
    flags=re.IGNORECASE,
)


def _guardrail_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _guardrail_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _guardrail_strings(item)


def _redact_literal_marks(text: str) -> str:
    """Keep literal transcription usable without allowing an interpretation suffix."""

    return _LITERAL_MARK_PATTERN.sub("款识逐字转录：「<LITERAL_MARK>」", text)


def _guardrail_text(value: Any) -> None:
    serialized = "\n".join(
        _redact_literal_marks(text).lower() for text in _guardrail_strings(value)
    )
    for pattern in FORBIDDEN_VERDICT_PATTERNS:
        if re.search(pattern, serialized, flags=re.IGNORECASE):
            raise ValueError("model output crossed the scientific conclusion boundary")


def validate_vision_output(value: Dict[str, Any]) -> Dict[str, Any]:
    required = {"observations", "suggested_regions", "limitations", "ood_risk"}
    if not required.issubset(value):
        raise ValueError("vision output is missing required fields")
    if not isinstance(value["observations"], list) or not all(
        isinstance(item, str) for item in value["observations"]
    ):
        raise ValueError("observations must be an array of strings")
    if not isinstance(value["suggested_regions"], list) or not all(
        isinstance(item, dict) and {"label", "reason"}.issubset(item)
        for item in value["suggested_regions"]
    ):
        raise ValueError("suggested_regions must contain label and reason")
    if not isinstance(value["limitations"], list) or not all(
        isinstance(item, str) for item in value["limitations"]
    ):
        raise ValueError("limitations must be an array of strings")
    if value["ood_risk"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("ood_risk must be LOW, MEDIUM, or HIGH")
    _guardrail_text(value)
    return value


def validate_native_video_output(value: Dict[str, Any]) -> Dict[str, Any]:
    validate_vision_output(value)
    temporal = value.get("temporal_observations")
    if not isinstance(temporal, list) or not all(
        isinstance(item, str) for item in temporal
    ):
        raise ValueError("temporal_observations must be an array of strings")
    return value


def validate_scout_multi_view_output(
    value: Dict[str, Any], allowed_captures: Dict[str, str]
) -> Dict[str, Any]:
    required = {
        "observations",
        "cross_view_observations",
        "limitations",
        "capture_issues",
        "ood_risk",
    }
    if set(value) != required:
        raise ValueError("Scout multi-view output fields are invalid")
    if value["ood_risk"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("Scout multi-view ood_risk is invalid")
    for field in ("cross_view_observations", "limitations"):
        if not isinstance(value[field], list) or not all(
            isinstance(item, str) and 0 < len(item.strip()) <= 500
            for item in value[field]
        ):
            raise ValueError(f"Scout multi-view {field} must be an array of strings")
        if len(value[field]) > 32:
            raise ValueError(f"Scout multi-view {field} is too long")
        if len(value[field]) != len(dict.fromkeys(value[field])):
            raise ValueError(f"Scout multi-view {field} must not contain duplicates")
    observations = value["observations"]
    if not isinstance(observations, list) or len(observations) > 64:
        raise ValueError("Scout multi-view observations must be an array")
    represented_captures: set[str] = set()
    for item in observations:
        if not isinstance(item, dict) or set(item) != {
            "capture_id",
            "view_code",
            "text",
        }:
            raise ValueError("Scout observation fields are invalid")
        capture_id = item["capture_id"]
        if (
            not isinstance(capture_id, str)
            or capture_id not in allowed_captures
            or item["view_code"] != allowed_captures[capture_id]
            or not isinstance(item["text"], str)
            or not item["text"].strip()
            or len(item["text"]) > 500
        ):
            raise ValueError("Scout observation is not bound to an allowed capture")
        represented_captures.add(capture_id)
    issues = value["capture_issues"]
    if not isinstance(issues, list) or len(issues) > 16:
        raise ValueError("Scout capture_issues must be an array")
    for item in issues:
        if (
            not isinstance(item, dict)
            or set(item) != {"capture_id", "issue"}
            or item.get("capture_id") not in allowed_captures
            or not isinstance(item.get("issue"), str)
            or not item["issue"].strip()
            or len(item["issue"]) > 500
        ):
            raise ValueError("Scout capture issue is invalid")
        represented_captures.add(item["capture_id"])
    if represented_captures != set(allowed_captures):
        raise ValueError("every Scout input must have an observation or capture issue")
    _guardrail_text(value)
    return value


def report_citation_ids(report: Dict[str, Any]) -> Set[str]:
    allowed: Set[str] = set()
    for search in report.get("knowledge", {}).get("searches", []):
        for result in search.get("results", []):
            source_id = result.get("source_id")
            if isinstance(source_id, str) and source_id:
                allowed.add(source_id)
    latest_recognition = report.get("latest_reference_recognition")
    if isinstance(latest_recognition, dict):
        recognitions = [latest_recognition]
    else:
        history = report.get("reference_recognitions", [])
        recognitions = history[-1:] if isinstance(history, list) else []
    for recognition in recognitions:
        for field in ("catalog_hits", "counterfeit_hits"):
            for result in recognition.get(field, []):
                citation_id = result.get("metadata", {}).get("citation_id")
                if isinstance(citation_id, str) and citation_id:
                    allowed.add(citation_id)
    return allowed


def validate_reasoner_output(
    value: Dict[str, Any], allowed_citation_ids: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    required = {"summary", "limitations", "next_steps", "citation_ids"}
    if not required.issubset(value):
        raise ValueError("reasoner output is missing required fields")
    if not isinstance(value["summary"], str):
        raise ValueError("summary must be a string")
    for field in ("limitations", "next_steps"):
        if not isinstance(value[field], list) or not all(
            isinstance(item, str) for item in value[field]
        ):
            raise ValueError(f"{field} must be an array of strings")
    citation_ids = value["citation_ids"]
    if not isinstance(citation_ids, list) or not all(
        isinstance(item, str) and item for item in citation_ids
    ):
        raise ValueError("citation_ids must be an array of source identifiers")
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError("citation_ids must not contain duplicates")
    if allowed_citation_ids is not None:
        unknown = set(citation_ids) - set(allowed_citation_ids)
        if unknown:
            raise ValueError("reasoner output contains an unbound local citation")
    _guardrail_text(value)
    return value


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        *,
        model_profile: str = "unknown",
        runtime_image: str = "unknown",
        model_source: str = "unknown",
        model_revision: str = "unknown",
        deployment_git_commit: str = "unknown",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.model_profile = model_profile
        self.runtime_image = runtime_image
        self.model_source = model_source
        self.model_revision = model_revision
        self.deployment_git_commit = deployment_git_commit

    @property
    def runtime_provider(self) -> str:
        """Identify the local serving layer without trusting response prose."""

        image = self.runtime_image.lower()
        profile = self.model_profile.lower()
        if "nvcr.io/nim/" in image or "nim" in profile:
            return "nvidia_nim"
        if "vllm" in image or "vllm" in profile:
            return "vllm"
        return "openai_compatible_local"

    @property
    def completion_mode(self) -> str:
        return "local_nim" if self.runtime_provider == "nvidia_nim" else "local_vllm"

    def _runtime_metadata(self) -> Dict[str, Any]:
        artifact_kind = (
            "nim_profile"
            if self.runtime_provider == "nvidia_nim"
            else "model_source_revision"
        )
        return {
            "model_profile": self.model_profile,
            "runtime_provider": self.runtime_provider,
            "runtime_attestation_scope": "configuration_bound_application_receipt",
            "runtime_image": self.runtime_image,
            "model_source": self.model_source,
            "model_identity_verification_scope": "provider_response_name_match",
            "model_artifact_kind": artifact_kind,
            "model_artifact_id": self.model_revision,
            # Compatibility field retained for existing evidence consumers. For
            # NVIDIA NIM it contains the immutable NIM profile ID, not an
            # upstream Hugging Face commit.
            "model_revision": self.model_revision,
            "deployment_git_commit": self.deployment_git_commit,
        }

    @property
    def is_nemotron_omni(self) -> bool:
        return "nemotron" in self.model.lower() and "omni" in self.model.lower()

    def _model_request_options(self, *, video: bool = False) -> Dict[str, Any]:
        """Apply model-card-safe options without changing the shared evidence schema."""
        return model_request_options(self.model, video=video)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _endpoint(self, suffix: str) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/{suffix.lstrip('/')}"
        return f"{self.base_url}/v1/{suffix.lstrip('/')}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health(self, name: str) -> Dict[str, Any]:
        if not self.configured:
            return {
                "name": name,
                "status": "disabled",
                "detail": "未配置；使用确定性本地引擎",
                "model": self.model,
                **self._runtime_metadata(),
            }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=min(self.timeout_seconds, 5.0)
            ) as client:
                response = await client.get(
                    self._endpoint("models"), headers=self._headers()
                )
                response.raise_for_status()
                body = response.json()
            served_models = sorted(
                {
                    str(item.get("id"))
                    for item in body.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                }
            )
            model_verified = self.model in served_models
            return {
                "name": name,
                "status": "online" if model_verified else "degraded",
                "detail": (
                    "OpenAI-compatible endpoint ready; configured model verified"
                    if model_verified
                    else "endpoint ready but configured model was not advertised"
                ),
                "model": self.model,
                "configured_model": self.model,
                "served_models": served_models,
                "model_identity_verified": model_verified,
                "request_id": response.headers.get("x-request-id"),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                **self._runtime_metadata(),
            }
        except Exception as exc:
            return {
                "name": name,
                "status": "degraded",
                "detail": f"endpoint unavailable: {type(exc).__name__}",
                "model": self.model,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                **self._runtime_metadata(),
            }

    @staticmethod
    def _parse_json_content(content: str) -> Dict[str, Any]:
        value = content.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[-1]
            value = value.rsplit("```", 1)[0]
            if value.lstrip().startswith("json"):
                value = value.lstrip()[4:].lstrip()
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("model response must be a JSON object")
        return parsed

    @staticmethod
    def _completion_identity(
        body: Dict[str, Any], response_headers: Any, expected_model: str
    ) -> tuple[str, str]:
        response_model = body.get("model")
        if not isinstance(response_model, str) or response_model != expected_model:
            raise ValueError("completion response did not prove the configured model identity")
        request_id = body.get("id") or response_headers.get("x-request-id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("completion response did not provide a request identifier")
        return response_model, request_id

    async def vision_observe(
        self, image_data_url: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self.configured:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "vision",
                "model": self.model,
                "prompt_hash": hashlib.sha256(
                    VISION_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
                "error": "NotConfigured",
                **self._runtime_metadata(),
            }
        prompt = (
            "Inspect this image as a visual observation only. Context metadata: "
            + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "temperature": 0.0,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }
        payload.update(self._model_request_options())
        return await self._completion(
            payload, VISION_SYSTEM_PROMPT, "vision", validate_vision_output
        )

    async def vision_observe_many(
        self,
        images: list[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload, allowed_captures = build_scout_multi_view_payload(
            self.model, images, metadata
        )
        if not self.configured:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "scout_multi_view",
                "model": self.model,
                "prompt_hash": hashlib.sha256(
                    SCOUT_MULTI_VIEW_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
                "error": "NotConfigured",
                **self._runtime_metadata(),
            }
        return await self._completion(
            payload,
            SCOUT_MULTI_VIEW_SYSTEM_PROMPT,
            "scout_multi_view",
            lambda value: validate_scout_multi_view_output(value, allowed_captures),
        )

    async def summarize_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "reasoner",
                "model": self.model,
                "prompt_hash": hashlib.sha256(
                    REASONER_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
                "error": "NotConfigured",
                **self._runtime_metadata(),
            }
        allowed_citations = report_citation_ids(report)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": REASONER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Allowed citation source_id values: "
                    + json.dumps(sorted(allowed_citations), ensure_ascii=False)
                    + "\nEvidence package:\n"
                    + json.dumps(report, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }
        payload.update(self._model_request_options())
        return await self._completion(
            payload,
            REASONER_SYSTEM_PROMPT,
            "reasoner",
            lambda value: validate_reasoner_output(value, allowed_citations),
        )

    async def video_observe(
        self, video_data_url: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self.configured:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "native_video",
                "model": self.model,
                "configured_model": self.model,
                "prompt_hash": hashlib.sha256(
                    NATIVE_VIDEO_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
                "error": "NotConfigured",
                **self._runtime_metadata(),
            }
        prompt = (
            "Inspect this complete video as a temporal visual observation. "
            "Follow the system language policy for all natural-language fields. "
            "Context metadata: "
            + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": NATIVE_VIDEO_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "video_url", "video_url": {"url": video_data_url}},
                    ],
                },
            ],
            "temperature": 0.0,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
        }
        payload.update(self._model_request_options(video=True))
        return await self._completion(
            payload,
            NATIVE_VIDEO_SYSTEM_PROMPT,
            "native_video",
            validate_native_video_output,
        )

    async def _completion(
        self,
        payload: Dict[str, Any],
        system_prompt: str,
        role: str,
        validator: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        system_prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        request_payload_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self._endpoint("chat/completions"),
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            choice = body["choices"][0]
            response_model, request_id = self._completion_identity(
                body, response.headers, self.model
            )
            if choice.get("finish_reason") != "stop":
                raise ValueError("completion did not finish cleanly")
            content = choice["message"]["content"]
            parsed = validator(self._parse_json_content(content))
            output_hash = model_output_hash(parsed)
            return {
                "available": True,
                "mode": self.completion_mode,
                "role": role,
                "model": response_model,
                "configured_model": self.model,
                "model_identity_verified": True,
                "request_id": request_id,
                "usage": {
                    key: int(value)
                    for key, value in (body.get("usage") or {}).items()
                    if isinstance(value, (int, float))
                },
                "finish_reason": choice.get("finish_reason"),
                # prompt_hash remains as a compatibility alias for older V1 evidence.
                "prompt_hash": system_prompt_hash,
                "system_prompt_hash": system_prompt_hash,
                "request_payload_hash": request_payload_hash,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "output_hash": output_hash,
                "output": parsed,
                **self._runtime_metadata(),
            }
        except Exception as exc:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": role,
                "model": self.model,
                "prompt_hash": system_prompt_hash,
                "system_prompt_hash": system_prompt_hash,
                "request_payload_hash": request_payload_hash,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": type(exc).__name__,
                **self._runtime_metadata(),
            }
