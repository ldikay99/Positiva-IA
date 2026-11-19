"""
Helpers de resiliencia para llamadas a Gemini (google-genai).

Aplica backoff exponencial sobre errores transitorios (429 rate-limit,
500/502/503/504 del servicio, timeouts de red). google-genai 3 usa httpx2
por defecto: sus excepciones no heredan de las de httpx.
"""

import logging

import httpx
import httpx2
from google.genai import errors as genai_errors
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    """Decide si una excepción de Gemini es transitoria."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return code in RETRYABLE_HTTP_STATUS
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return code in RETRYABLE_HTTP_STATUS
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                        httpx2.TimeoutException, httpx2.ConnectError, httpx2.ReadError)):
        return True
    return False


gemini_retry = retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception(_is_retryable_gemini_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
