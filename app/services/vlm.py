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

FORBIDDEN_VERDICT_PATTERNS = [
    r"(?:真品|赝品|仿品|高仿|伪作|真迹|正品|复制品|摹本)",
    r"(?:确定|明确|判定|认定|断定)(?:该|此|其|本)?(?:器物|作品)?(?:的)?(?:年代|时期|朝代|作者|创作者|作者归属|归属|窑口)",
    r"(?:年代|时期|朝代|作者|创作者|作者归属|归属|窑口)(?:已经|已|可以|可)?(?:确定|明确|判定|认定|断定|为|是|属于)",
    r"(?:出自|创作于|制作于|烧造于).{0,20}(?:年|世纪|朝|代|时期|窑|作者|画家|书家|工匠|作坊)",
    r"(?:作品|器物|书画|画作|此画|该作).{0,4}(?:出自|归属于)",
    r"(?:该|此|本)(?:器物|作品|画作).{0,4}(?:为|是|属于)(?:唐|宋|元|明|清|民国)(?:代|朝|时期)",
    r"(?:估价|市场价格|价值为|售价)",
    r"(?:法律鉴定|行政认定|文物定级为)",
    r"\b(?:authentic(?:ity)?|genuine(?:ness)?|counterfeit|fake|forg(?:ery|ed)|replica|imitation|reproduction|facsimile)\b",
    r"\b(?:dates?|dated|dating)\s+(?:to|from)\b",
    r"\b(?:made|created|painted|produced|manufactured|fired)\s+(?:in|during|circa|c\.)\s+(?:the\s+)?(?:\d{3,4}|\d{1,2}(?:st|nd|rd|th)[- ]century|[a-z]+\s+dynasty)",
    r"\b(?:attributed|ascribed)\s+to\b",
    r"\b(?:created|painted|made|signed|authored)\s+by\b",
    r"\b(?:work|painting|artifact|object)\s+(?:is|was)\s+by\b",
    r"\b(?:object|artifact|work|painting)\s+(?:is|was)\s+from\s+(?:the\s+)?(?:[a-z]+\s+dynasty|\d{3,4}|\d{1,2}(?:st|nd|rd|th)[- ]century)",
    r"\b(?:artist|author|maker|painter|workshop)\s+(?:is|was|:)\b",
    r"\b(?:market value|legally certified)\b",
]


def _guardrail_text(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False).lower()
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
        model_source: str = "unknown",
        model_revision: str = "unknown",
        deployment_git_commit: str = "unknown",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.model_profile = model_profile
        self.model_source = model_source
        self.model_revision = model_revision
        self.deployment_git_commit = deployment_git_commit

    def _runtime_metadata(self) -> Dict[str, Any]:
        return {
            "model_profile": self.model_profile,
            "model_source": self.model_source,
            "model_revision": self.model_revision,
            "deployment_git_commit": self.deployment_git_commit,
        }

    @property
    def is_nemotron_omni(self) -> bool:
        return "nemotron" in self.model.lower() and "omni" in self.model.lower()

    def _model_request_options(self, *, video: bool = False) -> Dict[str, Any]:
        """Apply model-card-safe options without changing the shared evidence schema."""

        if not self.is_nemotron_omni:
            return {}
        options: Dict[str, Any] = {
            "top_k": 1,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if video:
            options["mm_processor_kwargs"] = {"use_audio_in_video": False}
        return options

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
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
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
            output_hash = hashlib.sha256(
                json.dumps(parsed, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            return {
                "available": True,
                "mode": "local_vllm",
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
                "prompt_hash": prompt_hash,
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
                "prompt_hash": prompt_hash,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": type(exc).__name__,
                **self._runtime_metadata(),
            }
