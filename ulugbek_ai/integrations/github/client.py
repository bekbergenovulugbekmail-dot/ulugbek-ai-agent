"""A minimal, well-behaved GitHub API client.

Two design rules drive everything here:

1. **The token never leaves this module.** It is held as a ``SecretStr``, sent
   only as a header, and no error message or log line built here contains it.
2. **Responses are projected, not forwarded.** A GitHub payload is tens of
   kilobytes of JSON; the agent needs a handful of fields. Every method returns
   a small dict, so a tool result cannot flood the model's context window.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx
from pydantic import SecretStr

from ulugbek_ai.core.errors import ToolError, ValidationError
from ulugbek_ai.core.redaction import redact_text, truncate

logger = logging.getLogger(__name__)

DEFAULT_API_URL: Final[str] = "https://api.github.com"
API_VERSION: Final[str] = "2022-11-28"
USER_AGENT: Final[str] = "ulugbek-ai-agent"

#: ``owner/name`` — GitHub's own rules for each half.
_REPO_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)

#: Free text we forward from GitHub is capped: a commit message or an issue body
#: can be arbitrarily long, and it all ends up in the model's context.
_TEXT_LIMIT: Final[int] = 500
_BODY_LIMIT: Final[int] = 2_000


class GitHubApiError(ToolError):
    """A GitHub request failed. ``message`` is always safe to show."""

    code = "github_api_error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details={"status": status, **(details or {})})
        self.status = status
        self.retryable = retryable


def parse_repository(value: str) -> str:
    """Validate an ``owner/name`` reference.

    Also accepts a full GitHub URL, because that is what a human pastes.
    """
    candidate = value.strip()
    if candidate.startswith(("http://", "https://")):
        candidate = re.sub(r"^https?://[^/]+/", "", candidate)
    candidate = candidate.removesuffix(".git").strip("/")

    if not _REPO_RE.match(candidate):
        raise ValidationError(
            f"'{value}' is not a valid repository reference. "
            "Use the form owner/name, for example ulugbek/erp.",
            details={"repository": value},
        )
    return candidate


@dataclass(slots=True)
class RateLimit:
    """What GitHub told us about our remaining budget."""

    remaining: int | None = None
    limit: int | None = None
    reset_at: int | None = None

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "RateLimit":
        def as_int(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        return cls(
            remaining=as_int("x-ratelimit-remaining"),
            limit=as_int("x-ratelimit-limit"),
            reset_at=as_int("x-ratelimit-reset"),
        )


class GitHubClient:
    """Async GitHub REST client scoped to what the agent actually needs."""

    def __init__(
        self,
        *,
        token: SecretStr | str | None = None,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = (
            token
            if isinstance(token, SecretStr) or token is None
            else SecretStr(token)
        )
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport
        self.last_rate_limit: RateLimit | None = None

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> "GitHubClient":
        return cls(
            token=settings.github_token,
            api_url=settings.github_api_url,
            timeout_seconds=settings.github_timeout_seconds,
            **overrides,
        )

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        return headers

    # ----------------------------------------------------------------- HTTP #
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """One request, with every failure mode turned into a clear message."""
        url = f"{self._api_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise GitHubApiError(
                f"GitHub did not respond within {self._timeout:.0f}s.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubApiError(
                f"Could not reach GitHub: {redact_text(str(exc))}", retryable=True
            ) from exc

        self.last_rate_limit = RateLimit.from_headers(response.headers)

        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubApiError(
                    "GitHub returned a response that was not JSON."
                ) from exc

        raise self._error_for(response)

    def _error_for(self, response: httpx.Response) -> GitHubApiError:
        status = response.status_code
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("message", ""))
        except ValueError:
            detail = ""
        detail = truncate(redact_text(detail), 300)

        if status == 401:
            return GitHubApiError(
                "GitHub rejected the credential. Check that GITHUB_TOKEN is set "
                "and has not expired.",
                status=status,
            )
        if status == 403:
            rate = self.last_rate_limit
            if rate and rate.remaining == 0:
                return GitHubApiError(
                    "GitHub rate limit reached. "
                    + (
                        "Set GITHUB_TOKEN to raise the limit."
                        if not self.authenticated
                        else "Wait for the window to reset."
                    ),
                    status=status,
                    retryable=True,
                    details={"reset_at": rate.reset_at},
                )
            return GitHubApiError(
                f"GitHub refused the request: {detail or 'forbidden'}.",
                status=status,
            )
        if status == 404:
            return GitHubApiError(
                "Not found on GitHub. Either it does not exist, or the token "
                "cannot see it.",
                status=status,
            )
        if status == 422:
            return GitHubApiError(
                f"GitHub rejected the request as invalid: {detail or 'unprocessable'}.",
                status=status,
            )
        if status >= 500:
            return GitHubApiError(
                f"GitHub is having trouble (HTTP {status}). Try again shortly.",
                status=status,
                retryable=True,
            )
        return GitHubApiError(
            f"GitHub request failed (HTTP {status}): {detail or 'unknown error'}.",
            status=status,
        )

    # ------------------------------------------------------------ projections #
    async def get_repository(self, repository: str) -> dict[str, Any]:
        """Repository metadata, trimmed to what the agent reasons about."""
        repo = parse_repository(repository)
        data = await self._request("GET", f"/repos/{repo}")
        return {
            "repository": data.get("full_name"),
            "description": truncate(data.get("description") or "", _TEXT_LIMIT),
            "default_branch": data.get("default_branch"),
            "private": data.get("private"),
            "archived": data.get("archived"),
            "language": data.get("language"),
            "open_issues": data.get("open_issues_count"),
            "pushed_at": data.get("pushed_at"),
            "updated_at": data.get("updated_at"),
            "url": data.get("html_url"),
        }

    async def _branch_failure(self, repo: str, branch: str) -> GitHubApiError:
        """Turn a bare 404 into something the caller can act on.

        A 404 on a branch-scoped listing has two very different causes, and
        saying "not found" covers both uselessly — an agent that cannot tell
        them apart will retry the same wrong branch forever. One extra request,
        only on the error path, settles which it is and names the branch to use
        instead.
        """
        try:
            info = await self.get_repository(repo)
        except GitHubApiError:
            return GitHubApiError(
                f"Repository {repo} could not be read: it does not exist, or "
                "the token cannot see it.",
                status=404,
            )
        default = info.get("default_branch")
        return GitHubApiError(
            f"Branch '{branch}' does not exist in {repo}. "
            + (f"The default branch is '{default}'. " if default else "")
            + "Retry without the branch argument to use the default, or pass a "
            "branch that exists.",
            status=404,
            details={"default_branch": default},
        )

    async def list_commits(
        self, repository: str, *, branch: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        repo = parse_repository(repository)
        params: dict[str, Any] = {"per_page": max(1, min(limit, 50))}
        if branch:
            params["sha"] = branch
        try:
            data = await self._request("GET", f"/repos/{repo}/commits", params=params)
        except GitHubApiError as exc:
            if exc.status == 404 and branch:
                raise await self._branch_failure(repo, branch) from exc
            raise
        return [
            {
                "sha": entry.get("sha", "")[:12],
                "message": truncate(
                    (entry.get("commit", {}).get("message") or "").splitlines()[0]
                    if entry.get("commit", {}).get("message")
                    else "",
                    _TEXT_LIMIT,
                ),
                "author": (entry.get("commit", {}).get("author") or {}).get("name"),
                "date": (entry.get("commit", {}).get("author") or {}).get("date"),
                "url": entry.get("html_url"),
            }
            for entry in (data or [])
        ]

    async def get_commit(self, repository: str, sha: str) -> dict[str, Any]:
        """Fetch one commit — used to *prove* a commit exists after a push."""
        repo = parse_repository(repository)
        data = await self._request("GET", f"/repos/{repo}/commits/{sha}")
        return {
            "sha": data.get("sha", "")[:12],
            "message": truncate(
                (data.get("commit", {}).get("message") or "").splitlines()[0], _TEXT_LIMIT
            ),
            "author": (data.get("commit", {}).get("author") or {}).get("name"),
            "date": (data.get("commit", {}).get("author") or {}).get("date"),
            "url": data.get("html_url"),
        }

    async def list_workflow_runs(
        self, repository: str, *, branch: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Recent CI runs — the agent's read on whether a branch is healthy."""
        repo = parse_repository(repository)
        params: dict[str, Any] = {"per_page": max(1, min(limit, 30))}
        if branch:
            params["branch"] = branch
        data = await self._request(
            "GET", f"/repos/{repo}/actions/runs", params=params
        )
        runs = (data or {}).get("workflow_runs", []) if isinstance(data, dict) else []
        return [
            {
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "branch": run.get("head_branch"),
                "commit": (run.get("head_sha") or "")[:12],
                "created_at": run.get("created_at"),
                "url": run.get("html_url"),
            }
            for run in runs
        ]

    async def list_pull_requests(
        self, repository: str, *, state: str = "open", limit: int = 10
    ) -> list[dict[str, Any]]:
        repo = parse_repository(repository)
        data = await self._request(
            "GET",
            f"/repos/{repo}/pulls",
            params={"state": state, "per_page": max(1, min(limit, 50))},
        )
        return [
            {
                "number": pr.get("number"),
                "title": truncate(pr.get("title") or "", _TEXT_LIMIT),
                "state": pr.get("state"),
                "draft": pr.get("draft"),
                "author": (pr.get("user") or {}).get("login"),
                "branch": (pr.get("head") or {}).get("ref"),
                "base": (pr.get("base") or {}).get("ref"),
                "created_at": pr.get("created_at"),
                "url": pr.get("html_url"),
            }
            for pr in (data or [])
        ]

    async def create_issue(
        self,
        repository: str,
        *,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        repo = parse_repository(repository)
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = truncate(body, _BODY_LIMIT)
        if labels:
            payload["labels"] = labels[:10]
        data = await self._request("POST", f"/repos/{repo}/issues", json_body=payload)
        return {
            "number": data.get("number"),
            "title": data.get("title"),
            "state": data.get("state"),
            "url": data.get("html_url"),
        }

    async def get_issue(self, repository: str, number: int) -> dict[str, Any]:
        """Re-read an issue — how the write tool proves its effect landed."""
        repo = parse_repository(repository)
        data = await self._request("GET", f"/repos/{repo}/issues/{number}")
        return {
            "number": data.get("number"),
            "title": data.get("title"),
            "state": data.get("state"),
            "url": data.get("html_url"),
        }
