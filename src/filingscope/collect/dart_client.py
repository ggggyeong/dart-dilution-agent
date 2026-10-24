"""OpenDART JSON API 호출. 일시적 오류만 간격을 늘려 가며 재시도한다.

재시도: 네트워크 오류, HTTP 5xx, DART 020(한도 초과)·800(점검)·900(미정의 오류)
바로 실패: 010(키 오류)·100(잘못된 값) 등 나머지 오류
정상: 000, 013(데이터 없음)
"""

import re
import time
from dataclasses import dataclass
from typing import Callable

import httpx

DART_BASE = "https://opendart.fss.or.kr/api"
OK_STATUSES = {"000", "013"}
RETRYABLE_STATUSES = {"020", "800", "900"}


@dataclass
class CallResult:
    ok: bool
    status: str | None  # DART status, 또는 HTTP_xxx / NETWORK / BAD_JSON
    message: str | None
    attempts: int
    body: dict | None  # DART가 돌려준 JSON (실패해도 받았으면 보관)
    content: bytes | None = None  # get_zip 성공 시 ZIP 바이트


class DartClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._key = api_key
        self._http = http or httpx.Client(base_url=DART_BASE, timeout=30)
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep

    def get_json(self, endpoint: str, params: dict) -> CallResult:
        return self._with_retry(lambda: self._call_once(endpoint, params, self._parse_json))

    def get_zip(self, endpoint: str, params: dict) -> CallResult:
        """원문(document.xml)처럼 성공하면 ZIP, 실패하면 XML 오류 메시지를 주는 API. 성공 시 status '000'."""
        return self._with_retry(lambda: self._call_once(endpoint, params, self._parse_zip))

    def _with_retry(self, call_once) -> CallResult:
        attempt = 0
        while True:
            attempt += 1
            status, message, body, content, retryable = call_once()
            if status in OK_STATUSES:
                return CallResult(True, status, message, attempt, body, content)
            if not retryable or attempt > self._max_retries:
                return CallResult(False, status, message, attempt, body, content)
            self._sleep(self._backoff * 2 ** (attempt - 1))

    def _call_once(self, endpoint: str, params: dict, parse):
        """(status, message, body, content, retryable)"""
        try:
            resp = self._http.get(f"/{endpoint}", params={"crtfc_key": self._key, **params})
        except httpx.TransportError as e:
            return "NETWORK", self._scrub(f"{type(e).__name__}: {e}"), None, None, True
        if resp.status_code >= 400:
            return f"HTTP_{resp.status_code}", resp.text[:200], None, None, resp.status_code >= 500
        return parse(resp)

    @staticmethod
    def _parse_json(resp: httpx.Response):
        try:
            body = resp.json()
        except ValueError:
            return "BAD_JSON", resp.text[:200], None, None, True
        status = body.get("status")
        return status, body.get("message"), body, None, status in RETRYABLE_STATUSES

    @staticmethod
    def _parse_zip(resp: httpx.Response):
        if resp.content.startswith(b"PK"):
            return "000", None, None, resp.content, False
        text = resp.text
        m = re.search(r"<status>\s*(\d+)\s*</status>", text)
        if not m:
            return "BAD_ZIP", text[:200], None, None, True
        msg = re.search(r"<message>(.*?)</message>", text, re.S)
        status = m.group(1)
        return status, msg.group(1).strip() if msg else None, None, None, status in RETRYABLE_STATUSES

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")
