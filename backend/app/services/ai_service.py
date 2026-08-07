from __future__ import annotations

import json
import time
from typing import Any, Protocol

import requests

from backend.app.models import ModelConfig

XHS_TITLE_MAX_LENGTH = 20


def normalize_xhs_title(value: str, fallback: str = "") -> str:
    title = (value or fallback).strip().strip('"').strip("'").strip("《》")
    return title[:XHS_TITLE_MAX_LENGTH]


class TextAiClient(Protocol):
    def rewrite_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> str:
        ...

    def generate_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        topic: str,
        reference: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> dict[str, str]:
        ...

    def generate_titles(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
        instruction: str = "",
        system_prompt_override: str = "",
    ) -> list[str]:
        ...

    def generate_tags(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
        system_prompt_override: str = "",
    ) -> list[str]:
        ...

    def polish_text(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        text: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> str:
        ...


    def parse_auto_task_description(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        description: str,
        accounts: list[dict[str, Any]],
        instruction_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


class ImageAiClient(Protocol):
    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        ...

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
        size: str = "1024x1024",
    ) -> dict[str, Any]:
        ...

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> str:
        ...


def _candidate_response_encodings(response: requests.Response) -> list[str]:
    encodings: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", response.apparent_encoding, response.encoding):
        normalized = (encoding or "").strip()
        if normalized and normalized.lower() not in {item.lower() for item in encodings}:
            encodings.append(normalized)
    return encodings


def _load_json_response(response: requests.Response) -> Any:
    raw = response.content
    last_error: Exception | None = None
    for encoding in _candidate_response_encodings(response):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc

    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        last_error = exc

    raise ValueError("AI response is not valid JSON") from last_error


def _extract_json_object(content: str) -> dict[str, Any]:
    """从模型回复里取出一个 JSON 对象。

    即便 prompt 里写了「不要 markdown」，模型仍常常包一层 ```json 或在前后加一句话，
    所以先直接 parse，失败再截取第一个 `{` 到最后一个 `}`。
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if len(text.split("```")) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("AI 未返回可解析的 JSON 配置") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("AI 未返回可解析的 JSON 配置") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回的 JSON 不是对象")
    return parsed


class OpenAICompatibleTextClient:
    def _complete(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        timeout: int = 60,
    ) -> str:
        if not model_config.base_url:
            raise ValueError("Text model base_url is required")
        if not model_config.model_name:
            raise ValueError("Text model_name is required")
        if not api_key:
            raise ValueError("Text model api_key is required")

        endpoint = f"{model_config.base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = _load_json_response(response)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI response content is empty")
        return content.strip()

    def rewrite_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> str:
        # 用户从指令库选定或自己填写的指令，直接作为 system_prompt 取代默认人设。
        # 不叠加——用户明确要求「必须按照我的指令生成」。
        return self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=system_prompt_override.strip()
            or "你是小红书内容运营编辑，负责在保留事实的前提下改写成自然、可发布的种草笔记。",
            # 用户指定了指令时不再塞默认要求，否则他的指令旁边会冒出系统自己的话。
            user_prompt=(
                (f"改写要求：{instruction}\n\n" if instruction.strip()
                 else ("" if system_prompt_override.strip() else "改写要求：提升表达、增强小红书语感\n\n"))
                + f"标题：{title}\n\n正文：\n{body}"
            ),
        )

    def generate_note(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        topic: str,
        reference: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> dict[str, str]:
        # 用户指令取代默认人设。但输出格式与 20 字标题上限必须保留——前者是解析
        # 标题/正文的前提，后者是小红书平台硬限制（normalize_xhs_title 也会兜底截断）。
        persona = system_prompt_override.strip() or "你是小红书内容策划。"
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=f"{persona}\n输出必须包含标题和正文，标题不超过20个字符（含标点）。",
            user_prompt=(
                "请生成一篇小红书笔记，格式必须是：\n标题：...\n正文：...\n\n"
                "硬性规则：标题最多20个字符（含标点），不得超出。\n"
                f"选题：{topic}\n参考材料：{reference or '无'}"
                + (f"\n要求：{instruction}" if instruction.strip()
                   else ("" if system_prompt_override.strip() else "\n要求：自然、有信息密度"))
            ),
        )
        title = topic
        body = content
        for line in content.splitlines():
            if line.startswith("标题："):
                title = line.replace("标题：", "", 1).strip() or title
                break
        if "正文：" in content:
            body = content.split("正文：", 1)[1].strip()
        return {"title": normalize_xhs_title(title, topic), "body": body}

    def generate_titles(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
        instruction: str = "",
        system_prompt_override: str = "",
    ) -> list[str]:
        requirement = f"\n写作要求：\n{instruction.strip()}" if instruction.strip() else ""
        # 用户指令取代默认人设；20 字上限保留（平台硬限制，normalize_xhs_title 也会兜底截断）。
        persona = system_prompt_override.strip() or "你是小红书标题优化专家。"
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=f"{persona}\n每个标题必须不超过20个字符（含标点）。",
            user_prompt=(
                f"请给出 {count} 个小红书标题，每行一个。"
                "硬性规则：每个标题最多20个字符（含标点）。\n"
                f"原标题：{title}\n正文：{body}"
                f"{requirement}"
            ),
        )
        return [
            normalize_xhs_title(line.strip(" -0123456789.、"))
            for line in content.splitlines()
            if line.strip()
        ][:count]

    def generate_tags(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        title: str,
        body: str,
        count: int,
        system_prompt_override: str = "",
    ) -> list[str]:
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=system_prompt_override.strip() or "你是小红书 SEO 和话题标签专家。",
            user_prompt=f"请给出 {count} 个小红书话题标签，只输出标签，用逗号或换行分隔。\n标题：{title}\n正文：{body}",
        )
        separators = content.replace("，", ",").replace("\n", ",").split(",")
        return [item.strip().lstrip("#") for item in separators if item.strip()][:count]

    def parse_auto_task_description(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        description: str,
        accounts: list[dict[str, Any]],
        instruction_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """把一句话需求解析成自动运营任务配置的**建议值**。

        这里只负责拿到 AI 的原始 JSON；合法性校验全部在 API 层做（`_build_auto_task_proposal`）。
        账号候选以「id + 名称」列表喂进 prompt，让模型从既有账号里挑，而不是自由发挥一个 id——
        即便如此，返回的 id 仍会在 API 层按归属复核一次。
        """
        account_lines = "\n".join(
            f"- id={item.get('id')} 名称={item.get('nickname') or '(未命名)'} 类型={item.get('sub_type')}"
            for item in accounts
        ) or "（该用户没有可用账号）"
        instruction_lines = "\n".join(
            f"- {item.get('name')}：{str(item.get('content') or '')[:200]}"
            for item in instruction_candidates
        ) or "（无）"
        content = self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=(
                "你是小红书自动运营任务的配置助手。你只输出配置建议，绝不代替用户做决定。\n"
                "必须只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。"
            ),
            user_prompt=(
                "把下面这句需求解析成自动运营任务配置。\n\n"
                f"需求：{description}\n\n"
                f"可用账号（只能从中挑选 id，不确定就填 null）：\n{account_lines}\n\n"
                f"用户指令库中的 auto_ops 指令（可作为改写指令的参考）：\n{instruction_lines}\n\n"
                "输出 JSON，字段如下：\n"
                '{"name": "任务名，20字内", '
                '"keywords": ["搜索关键词", "最多10个"], '
                '"ai_instruction": "改写要求", '
                '"schedule_type": "manual|daily|weekly|interval 之一", '
                '"schedule_time": "HH:MM 24小时制", '
                '"schedule_days": "weekly 时填周几，1-7 逗号分隔，否则空字符串", '
                '"schedule_interval_hours": 数字，interval 时用，否则 null, '
                '"pc_account_id": 采集账号 id 或 null, '
                '"creator_account_id": 发布账号 id 或 null, '
                '"notes": "你不确定的地方，中文简述"}\n\n'
                "规则：需求里没提到的字段填 null 或空，不要编造。账号 id 只能来自上面的列表。"
            ),
            temperature=0.2,
        )
        return _extract_json_object(content)

    def polish_text(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        text: str,
        instruction: str,
        system_prompt_override: str = "",
    ) -> str:
        return self._complete(
            model_config=model_config,
            api_key=api_key,
            system_prompt=system_prompt_override.strip() or "你是小红书正文润色编辑。",
            user_prompt=(
                (f"润色要求：{instruction}\n\n" if instruction.strip()
                 else ("" if system_prompt_override.strip() else "润色要求：更自然、清晰、有种草感\n\n"))
                + f"原文：\n{text}"
            ),
        )


class OpenAICompatibleImageClient:
    def _validate(self, *, model_config: ModelConfig, api_key: str) -> None:
        if not model_config.base_url:
            raise ValueError("Image model base_url is required")
        if not model_config.model_name:
            raise ValueError("Image model_name is required")
        if not api_key:
            raise ValueError("Image model api_key is required")

    def generate_cover(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
    ) -> dict[str, Any]:
        return self.generate_image(
            model_config=model_config,
            api_key=api_key,
            prompt=f"{prompt}\nStyle: {style or 'clean XHS cover'}",
            size=size,
        )

    def generate_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        prompt: str,
        reference_images: list[str] | None = None,
        size: str = "1024x1024",
    ) -> dict[str, Any]:
        self._validate(model_config=model_config, api_key=api_key)
        endpoint = f"{model_config.base_url.rstrip('/')}/images/generations"
        body: dict[str, Any] = {
            "model": model_config.model_name,
            "prompt": prompt,
            "n": 1,
            "size": size or "1024x1024",
        }
        if reference_images:
            resolved = [self._resolve_image_ref(url) for url in reference_images]
            if len(resolved) == 1:
                body["image"] = resolved[0]
            else:
                body["image"] = resolved
                body["sequential_image_generation"] = "disabled"
            body["watermark"] = False
        response: requests.Response | None = None
        try:
            for attempt in range(3):
                try:
                    response = requests.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "Connection": "close",
                            "User-Agent": "Spider-XHS/1.0",
                        },
                        json=body,
                        timeout=(20, 180),
                    )
                    break
                except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
            if response is None:
                raise ValueError("图片生成请求未返回响应")
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                error_payload = _load_json_response(exc.response) if exc.response else {}
                detail = error_payload.get("error", {}).get("message", "") if isinstance(error_payload, dict) else ""
            except Exception:
                pass
            raise ValueError(f"图片生成失败: {detail or exc}") from exc
        except requests.exceptions.ReadTimeout as exc:
            raise ValueError("图片生成超时；服务可能仍在处理，请稍后查看图片库后再重试") from exc
        except requests.exceptions.RequestException as exc:
            raise ValueError(f"图片服务连接失败: {exc}") from exc
        payload = _load_json_response(response)
        try:
            item = payload["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Image response missing data[0]") from exc
        image_ref = item.get("url") or item.get("image_url")
        b64_json = item.get("b64_json")
        if not image_ref and isinstance(b64_json, str) and b64_json:
            image_ref = (
                b64_json
                if b64_json.startswith("data:image/")
                else f"data:image/png;base64,{b64_json}"
            )
        if not isinstance(image_ref, str) or not image_ref:
            raise ValueError("Image response missing url, image_url or b64_json")
        return {"url": image_ref, "b64_json": b64_json, "raw": payload}

    @staticmethod
    def _resolve_image_ref(url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/api/files/media/"):
            import base64
            from pathlib import Path
            from backend.app.core.config import get_settings
            file_name = url.split("/")[-1]
            local = Path(get_settings().storage_dir) / "media" / file_name
            if local.is_file():
                raw = local.read_bytes()
                ext = local.suffix.lower().lstrip(".")
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
                return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        return url

    def describe_image(
        self,
        *,
        model_config: ModelConfig,
        api_key: str,
        image_url: str,
        instruction: str,
    ) -> str:
        self._validate(model_config=model_config, api_key=api_key)
        endpoint = f"{model_config.base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model_config.model_name,
                "messages": [
                    {"role": "system", "content": "你是小红书图片分析助手。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction or "描述这张图片适合的小红书卖点。"},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = _load_json_response(response)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI image description is empty")
        return content.strip()
