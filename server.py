"""
UserTesting MCP Server
Exposes UserTesting.com session results and QX scores as MCP tools.
"""

import asyncio
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool

# Load .env from this file's directory
load_dotenv(Path(__file__).parent / ".env")

CLIENT_ID = os.environ.get("USERTESTING_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("USERTESTING_CLIENT_SECRET", "")
BASE_URL = "https://api.use2.usertesting.com"
TOKEN_URL = "https://auth.usertesting.com/oauth2/aus1p3vtd8vtm4Bxv0h8/v1/token"
V1_BASE_PATH = "/usertesting/api/v1"

server = Server("usertesting")


# ---------------------------------------------------------------------------
# Token management (OAuth2 Client Credentials, expires every 3600s)
# ---------------------------------------------------------------------------

_token: str = ""
_token_expires_at: float = 0.0


def _get_token() -> str:
    global _token, _token_expires_at
    if time.time() < _token_expires_at - 60:  # refresh 60s before expiry
        return _token
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "studies:read",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    _token = resp["access_token"]
    _token_expires_at = time.time() + int(resp.get("expires_in", 3600))
    return _token


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(headers={"Authorization": f"Bearer {_get_token()}"}, timeout=60)


def _get(path: str, params: dict | None = None) -> dict:
    with _client() as c:
        r = c.get(f"{BASE_URL}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _get_text(path: str) -> str:
    """GET a plain-text response (e.g. VTT transcript)."""
    with _client() as c:
        r = c.get(f"{BASE_URL}{path}")
        r.raise_for_status()
        return r.text


def _client_v1() -> httpx.Client:
    """HTTP client for V1 API — uses `token` header instead of Authorization: Bearer."""
    return httpx.Client(headers={"token": _get_token()}, timeout=60)


def _get_v1(path: str, params: dict | None = None) -> dict:
    with _client_v1() as c:
        r = c.get(f"{BASE_URL}{V1_BASE_PATH}{path}", params=params)
        r.raise_for_status()
        return r.json()


_MAX_PAGES = 50  # 50 * 500 = 25,000 sessions max


def _get_all_completed_sessions(study_uuid: str) -> list[dict]:
    """Fetch all completed sessions for a V1 study, cursor-paginating."""
    sessions: list[dict] = []
    cursor = None
    for _ in range(_MAX_PAGES):
        params = {"cursor": cursor} if cursor else None
        data = _get_v1(f"/studies/{study_uuid}/completed-sessions", params=params)
        batch = data.get("sessions", [])
        sessions.extend(batch)
        if not batch:
            break
        cursor = batch[-1].get("cursor")
        if not cursor:
            break
    return sessions


def _get_all_sessions(test_id: str) -> list[dict]:
    """Fetch all sessions for a test, auto-paginating through every page."""
    sessions: list[dict] = []
    limit = 500
    offset = 0
    for _ in range(_MAX_PAGES):
        data = _get(
            "/api/v2/sessionResults",
            params={"testId": test_id, "limit": limit, "offset": offset},
        )
        batch = data.get("sessions", [])
        sessions.extend(batch)
        total = data.get("meta", {}).get("pagination", {}).get("totalCount", 0)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return sessions


# ---------------------------------------------------------------------------
# VTT parser
# ---------------------------------------------------------------------------


def _parse_vtt(vtt: str) -> str:
    """Parse WebVTT transcript into readable '[H:MM:SS] text' lines."""
    result: list[str] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if re.search(r"\d{2}:\d{2}:\d{2}\.\d+\s+-->\s+", line):
            start = re.split(r"\s+-->\s+", line)[0]
            h, m, s = start.split(":")
            s = s.split(".")[0]
            label = f"[{int(h)}:{m}:{s}]"
            text_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            if text_lines:
                result.append(f"{label} {' '.join(text_lines)}")
        else:
            i += 1
    return "\n".join(result) if result else "(No transcript content)"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_sessions(sessions: list[dict]) -> str:
    if not sessions:
        return "No sessions found."
    lines = [
        f"{'Session ID':<38} {'Status':<14} {'Start Time':<26} Finish Time",
        "-" * 100,
    ]
    for s in sessions:
        lines.append(
            f"{s.get('sessionId', ''):<38} "
            f"{s.get('status', ''):<14} "
            f"{s.get('startTime', ''):<26} "
            f"{s.get('finishTime', '')}"
        )
    lines.append(f"\nTotal: {len(sessions)} session(s)")
    return "\n".join(lines)


def _format_session_details(data: dict) -> str:
    lines = [
        f"Session ID:    {data.get('sessionId', '')}",
        f"Audience ID:   {data.get('audienceId', '')}",
        f"Test Plan ID:  {data.get('testPlanId', '')}",
    ]

    participant = data.get("sessionParticipant", {})
    if participant:
        lines.append(f"\nParticipant ID: {participant.get('participantId', '')}")
        demographics = participant.get("demographicsInfo", [])
        if demographics:
            lines.append("Demographics:")
            for d in demographics:
                lines.append(f"  {d.get('label', '')}: {d.get('value', '')}")

    task_results = data.get("sessionTaskResults", [])
    if task_results:
        lines.append(f"\nTask Results ({len(task_results)}):")
        lines.append("-" * 60)
        for t in task_results:
            lines.append(f"  Task ID:   {t.get('taskId', '')}")
            lines.append(f"  Type:      {t.get('taskType', '')}")
            response = t.get("taskResponse")
            if response is not None:
                lines.append(f"  Response:  {response}")
            lines.append("")

    return "\n".join(lines)


def _format_workspaces(data: list) -> str:
    if not data:
        return "No workspaces found."
    lines = [f"{'ID':<10} {'UUID':<38} Name", "-" * 80]
    for w in data:
        lines.append(f"{str(w.get('id', '')):<10} {w.get('uuid', ''):<38} {w.get('name', '')}")
    lines.append(f"\nTotal: {len(data)} workspace(s)")
    return "\n".join(lines)


def _format_workspace_studies(data: list) -> str:
    if not data:
        return "No studies found in this workspace."
    lines = ["Studies in workspace:", "-" * 60]
    for s in data:
        lines.append(f"Title:      {s.get('title', '')}")
        lines.append(f"UUID:       {s.get('uuid', '')}")
        lines.append(f"Ordered At: {s.get('orderedAt', '')}")
        by = s.get("orderedBy", {})
        if by:
            lines.append(f"Ordered By: {by.get('name', '')} ({by.get('email', '')})")
        lines.append("")
    return "\n".join(lines)


def _format_study(data: dict) -> str:
    lines = [
        f"Title:         {data.get('title', '')}",
        f"Session Count: {data.get('sessionCount', '')}",
    ]
    ordered_by = data.get("orderedBy", {})
    if ordered_by:
        lines.append(f"Ordered By:    {ordered_by.get('name', '')} ({ordered_by.get('email', '')})")
    tasks = data.get("tasks", [])
    if tasks:
        lines.append(f"\nTasks ({len(tasks)}):")
        lines.append("-" * 60)
        for t in tasks:
            lines.append(f"  [{t.get('position', '')}] {t.get('taskType', '')}: {t.get('text', '')}")
    nps = data.get("netPromoterScores", [])
    if nps:
        lines.append("\nNet Promoter Scores:")
        for score in nps:
            lines.append(
                f"  Score {score.get('score', '')}: "
                f"{score.get('promoterPercentage', '')}% promoters, "
                f"{score.get('passivePercentage', '')}% passives, "
                f"{score.get('detractorPercentage', '')}% detractors"
            )
    return "\n".join(lines)


def _format_clip(data: dict) -> str:
    lines = [
        f"Type:      {data.get('typeName', '')}",
        f"Duration:  {data.get('duration', '')}s",
        f"Created:   {data.get('createdAt', '')}",
        f"Important: {data.get('isImportant', '')}",
    ]
    if data.get("note"):
        lines.append(f"Note:      {data['note']}")
    if data.get("noteTags"):
        lines.append(f"Tags:      {', '.join(data['noteTags'])}")
    if data.get("sentimentTag"):
        lines.append(f"Sentiment: {data['sentimentTag']}")
    study = data.get("study", {})
    if study:
        lines.append(f"Study:     {study.get('title', '')}")
    if data.get("embeddableUrl"):
        lines.append(f"\nEmbeddable URL:\n{data['embeddableUrl']}")
    return "\n".join(lines)


def _format_highlight_reel(data: dict) -> str:
    lines = [
        f"Title:    {data.get('title', '')}",
        f"ID:       {data.get('id', '')}",
        f"Duration: {data.get('duration', '')}s",
        f"Created:  {data.get('createdAt', '')}",
        f"Updated:  {data.get('updatedAt', '')}",
    ]
    if data.get("shareUrl"):
        lines.append(f"\nShare URL:\n{data['shareUrl']}")
    clips = data.get("clips", {})
    nodes = clips.get("nodes", [])
    total = clips.get("totalCount", 0)
    if total:
        lines.append(f"\nClips ({total} total):")
        for c in nodes:
            lines.append(f"  ID: {c.get('id', '')} — Duration: {c.get('duration', '')}s")
    return "\n".join(lines)


def _format_session_v1(data: dict) -> str:
    lines = [
        f"Session ID:    {data.get('sessionId', '')}",
        f"UID:           {data.get('uid', '')}",
        f"Title:         {data.get('title', '')}",
        f"Duration:      {data.get('duration', '')}s",
        f"Sequence #:    {data.get('sequenceNumber', '')}",
        f"State Updated: {data.get('stateUpdatedAt', '')}",
    ]
    notes = data.get("notes", [])
    if notes:
        lines.append(f"\nNotes ({len(notes)}):")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def _format_completed_sessions(sessions: list[dict]) -> str:
    if not sessions:
        return "No completed sessions found."
    lines = [f"Total: {len(sessions)} completed session(s)\n"]
    for s in sessions:
        lines.append(f"Session ID:   {s.get('sessionId', '')} ({s.get('sessionUuid', '')})")
        lines.append(f"State:        {s.get('state', '')}")
        lines.append(f"Completed:    {s.get('completedDateTime', '')}")
        lines.append(f"Form Factor:  {s.get('formFactorUsed', '')}")
        participant = s.get("participant", {})
        if participant:
            lines.append(f"Participant:  {participant.get('name', '')}")
            for d in participant.get("demographics", []):
                lines.append(f"  {d.get('label', '')}: {d.get('value', '')}")
        answers = s.get("answers", [])
        if answers:
            lines.append(f"Answers ({len(answers)}):")
            for a in answers:
                for resp in a.get("responses", [])[:3]:
                    lines.append(f"  [{a.get('type', '')}] {resp}")
        lines.append("")
    return "\n".join(lines)


def _format_qx_scores(data: dict) -> str:
    meta = data.get("meta", {})
    lines = [
        f"Test ID:        {data.get('testId', '')}",
        f"Total QX Tasks: {meta.get('totalQxTasks', 'N/A')}",
        f"Completes:      {meta.get('completes', 'N/A')}",
        "",
    ]

    qx_scores = data.get("qxScores", [])
    if not qx_scores:
        lines.append("No QX scores available.")
        return "\n".join(lines)

    lines.append("QX Scores by Task:")
    lines.append("-" * 60)
    for score in qx_scores:
        label = score.get("label") or score.get("taskGroupId", "")
        lines.append(f"\nTask: {label}")
        lines.append(f"  Overall QX Score: {score.get('qxScore', 'N/A')}/100")
        components = score.get("components", {})
        if components:
            lines.append(f"  Behavioral:       {components.get('behavioral', 'N/A')}")
            lines.append(f"  Attitudinal:      {components.get('attitudinal', 'N/A')}")
        values = score.get("values", {})
        if values:
            lines.append("  Sub-scores:")
            for key, val in values.items():
                if isinstance(val, list):
                    lines.append(f"    {key}: {', '.join(str(v) for v in val)}")
                else:
                    lines.append(f"    {key}: {val}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _build_prompt_list() -> list[Prompt]:
    return [
        Prompt(
            name="analyze-test",
            description="Analyze all session results and QX scores for a UserTesting test",
            arguments=[
                PromptArgument(
                    name="test_id",
                    description="UserTesting test ID — numeric ID from app.usertesting.com/workspaces/{workspace_id}/study/{test_id}; for surveys, the UUID from app.usertesting.com/workspaces/{workspace_id}/test/{uuid}/...",
                    required=True,
                ),
            ],
        ),
        Prompt(
            name="summarize-test",
            description=(
                "Generate a formatted summary of UserTesting results for sharing with stakeholders"
            ),
            arguments=[
                PromptArgument(
                    name="test_id",
                    description="UserTesting test ID — numeric ID from app.usertesting.com/workspaces/{workspace_id}/study/{test_id}; for surveys, the UUID from app.usertesting.com/workspaces/{workspace_id}/test/{uuid}/...",
                    required=True,
                ),
                PromptArgument(
                    name="audience",
                    description=(
                        "Target audience for the summary "
                        "(e.g. 'executives', 'product team'). Defaults to 'stakeholders'."
                    ),
                    required=False,
                ),
            ],
        ),
    ]


def _get_prompt_result(name: str, arguments: dict | None) -> GetPromptResult:
    args = arguments or {}

    if name == "analyze-test":
        test_id = args.get("test_id")
        if not test_id:
            raise ValueError("test_id is required for the analyze-test prompt")
        return GetPromptResult(
            description=f"Analyze UserTesting results for test {test_id}",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Analyze the UserTesting results for test {test_id}.\n\n"
                            "1. Call list_sessions to get all sessions and their statuses.\n"
                            "2. Call get_qx_scores to retrieve the overall QX score and "
                            "task-level breakdown.\n"
                            "3. For each completed session, call get_session_details to "
                            "understand task responses and participant demographics.\n"
                            "4. For up to 3 sessions, call get_transcript to sample "
                            "qualitative feedback.\n\n"
                            "Provide:\n"
                            "- A brief overview of the test (session count, completion rate)\n"
                            "- QX score interpretation (overall and per-task)\n"
                            "- Key themes from session details and transcripts\n"
                            "- Notable participant demographics if relevant\n"
                            "- Data quality notes (incomplete sessions, missing transcripts)\n\n"
                            "When done, mention that a stakeholder summary is available "
                            "via the summarize-test prompt."
                        ),
                    ),
                )
            ],
        )

    elif name == "summarize-test":
        test_id = args.get("test_id")
        if not test_id:
            raise ValueError("test_id is required for the summarize-test prompt")
        audience = args.get("audience", "stakeholders")
        return GetPromptResult(
            description=f"Stakeholder summary for UserTesting test {test_id}",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Generate a stakeholder summary of UserTesting results for "
                            f"test {test_id}, tailored for {audience}.\n\n"
                            "Use list_sessions for session overview, get_qx_scores for "
                            "metrics, get_session_details for task-level insights, and "
                            "get_transcript on a sample of sessions for qualitative themes.\n\n"
                            "Structure the summary with:\n"
                            "- **Executive Summary** (2–3 sentences)\n"
                            "- **Key Metrics** (session count, completion rate, QX score)\n"
                            "- **Top Findings** (3–5 bullet points from sessions and transcripts)\n"
                            "- **Recommendations** (only if the data clearly supports them)\n\n"
                            "Keep language accessible and avoid technical jargon."
                        ),
                    ),
                )
            ],
        )

    else:
        raise ValueError(f"Unknown prompt: {name}")


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    return _build_prompt_list()


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
    return _get_prompt_result(name, arguments)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_sessions",
            description=(
                "List all sessions for a UserTesting test. Auto-paginates through all results. "
                "Returns session IDs, statuses, and start/finish times."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "test_id": {
                        "type": "string",
                        "description": "UserTesting test ID (numeric ID from the study URL: app.usertesting.com/workspaces/{workspace_id}/study/{test_id}; for surveys, use the UUID from app.usertesting.com/workspaces/{workspace_id}/test/{uuid}/...)",
                    }
                },
                "required": ["test_id"],
            },
        ),
        Tool(
            name="get_session_details",
            description=(
                "Get full details for a specific session: task results, participant info, "
                "and demographics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from list_sessions, or found under Contributor information in the session player (also shown in the video player title bar)",
                    }
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_transcript",
            description=(
                "Get the transcript for a session, parsed from VTT into readable timestamped "
                "text. Use this to read what participants said during the session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from list_sessions, or found under Contributor information in the session player (also shown in the video player title bar)",
                    }
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_video_url",
            description=(
                "Get a pre-signed video download URL for a session. The URL is valid for 1 hour."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from list_sessions, or found under Contributor information in the session player (also shown in the video player title bar)",
                    }
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_qx_scores",
            description=(
                "Get QX (experience quality) scores for a test. Returns overall score (0–100), "
                "behavioral/attitudinal breakdown, and per-task scores."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "test_id": {
                        "type": "string",
                        "description": "UserTesting test ID (numeric ID from the study URL: app.usertesting.com/workspaces/{workspace_id}/study/{test_id}; for surveys, use the UUID from app.usertesting.com/workspaces/{workspace_id}/test/{uuid}/...)",
                    }
                },
                "required": ["test_id"],
            },
        ),
        # --- V1 API tools ---
        Tool(
            name="list_workspaces",
            description="List all workspaces accessible to the authenticated user (V1 API).",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_workspace_studies",
            description=(
                "List all studies in a workspace (V1 API). "
                "Returns study titles, UUIDs, and ordering info."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace_uuid": {
                        "type": "string",
                        "description": "Workspace UUID from list_workspaces",
                    }
                },
                "required": ["workspace_uuid"],
            },
        ),
        Tool(
            name="get_study",
            description=(
                "Get metadata and task configuration for a study (V1 API). "
                "Returns title, session count, tasks, and Net Promoter Scores."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "study_uuid": {
                        "type": "string",
                        "description": "Study UUID from get_workspace_studies or the app URL",
                    }
                },
                "required": ["study_uuid"],
            },
        ),
        Tool(
            name="get_completed_sessions",
            description=(
                "Get all completed sessions for a study (V1 API). "
                "Richer than list_sessions — includes participant demographics, screener responses, "
                "task answers, and transcript snippets. Auto-paginates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "study_uuid": {
                        "type": "string",
                        "description": "Study UUID from get_workspace_studies or the app URL",
                    }
                },
                "required": ["study_uuid"],
            },
        ),
        Tool(
            name="get_clip",
            description=(
                "Get metadata for a clip (V1 API): duration, note, tags, sentiment, "
                "and embeddable URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "clip_uuid": {
                        "type": "string",
                        "description": "Clip UUID from the app URL when viewing a clip",
                    }
                },
                "required": ["clip_uuid"],
            },
        ),
        Tool(
            name="get_highlight_reel",
            description=(
                "Get a highlight reel (V1 API): title, duration, share URL, and list of clips."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "reel_uuid": {
                        "type": "string",
                        "description": "Highlight reel UUID from the app URL",
                    }
                },
                "required": ["reel_uuid"],
            },
        ),
        Tool(
            name="get_session_embed",
            description=(
                "Get embeddable session metadata (V1 API): title, duration, sequence number, "
                "and state. Use get_session_details for richer task/demographic data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_uuid": {
                        "type": "string",
                        "description": "Session UUID from the app URL or get_completed_sessions",
                    }
                },
                "required": ["session_uuid"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "list_sessions":
            return await _handle_list_sessions(arguments["test_id"])
        elif name == "get_session_details":
            return await _handle_get_session_details(arguments["session_id"])
        elif name == "get_transcript":
            return await _handle_get_transcript(arguments["session_id"])
        elif name == "get_video_url":
            return await _handle_get_video_url(arguments["session_id"])
        elif name == "get_qx_scores":
            return await _handle_get_qx_scores(arguments["test_id"])
        elif name == "list_workspaces":
            return await _handle_list_workspaces()
        elif name == "get_workspace_studies":
            return await _handle_get_workspace_studies(arguments["workspace_uuid"])
        elif name == "get_study":
            return await _handle_get_study(arguments["study_uuid"])
        elif name == "get_completed_sessions":
            return await _handle_get_completed_sessions(arguments["study_uuid"])
        elif name == "get_clip":
            return await _handle_get_clip(arguments["clip_uuid"])
        elif name == "get_highlight_reel":
            return await _handle_get_highlight_reel(arguments["reel_uuid"])
        elif name == "get_session_embed":
            return await _handle_get_session_embed(arguments["session_uuid"])
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def _handle_list_sessions(test_id: str) -> list[TextContent]:
    sessions = _get_all_sessions(test_id)
    return [TextContent(type="text", text=_format_sessions(sessions))]


async def _handle_get_session_details(session_id: str) -> list[TextContent]:
    data = _get(f"/api/v2/sessionResults/{session_id}")
    return [TextContent(type="text", text=_format_session_details(data))]


async def _handle_get_transcript(session_id: str) -> list[TextContent]:
    vtt = _get_text(f"/api/v2/sessionResults/{session_id}/transcript")
    parsed = _parse_vtt(vtt)
    return [TextContent(type="text", text=f"Transcript for session {session_id}:\n\n{parsed}")]


async def _handle_get_video_url(session_id: str) -> list[TextContent]:
    data = _get(f"/api/v2/sessionResults/{session_id}/videoDownloadUrl")
    url = data.get("videoUrl", "")
    expires_at = data.get("expiresAt", "")
    if not url:
        return [TextContent(type="text", text="Error: no video URL returned by the API.")]
    return [TextContent(type="text", text=f"Video URL (valid until {expires_at}):\n{url}")]


async def _handle_get_qx_scores(test_id: str) -> list[TextContent]:
    data = _get(f"/api/v2/testResults/{test_id}/qxScores")
    return [TextContent(type="text", text=_format_qx_scores(data))]


async def _handle_list_workspaces() -> list[TextContent]:
    data = _get_v1("/workspaces")
    workspaces = data if isinstance(data, list) else data.get("workspaces", [])
    return [TextContent(type="text", text=_format_workspaces(workspaces))]


async def _handle_get_workspace_studies(workspace_uuid: str) -> list[TextContent]:
    data = _get_v1(f"/workspaces/{workspace_uuid}")
    studies = data if isinstance(data, list) else data.get("studies", [])
    return [TextContent(type="text", text=_format_workspace_studies(studies))]


async def _handle_get_study(study_uuid: str) -> list[TextContent]:
    data = _get_v1(f"/studies/{study_uuid}")
    study = data.get("study", data)
    return [TextContent(type="text", text=_format_study(study))]


async def _handle_get_completed_sessions(study_uuid: str) -> list[TextContent]:
    sessions = _get_all_completed_sessions(study_uuid)
    return [TextContent(type="text", text=_format_completed_sessions(sessions))]


async def _handle_get_clip(clip_uuid: str) -> list[TextContent]:
    data = _get_v1(f"/clip/{clip_uuid}")
    clip = data.get("clip", data)
    return [TextContent(type="text", text=_format_clip(clip))]


async def _handle_get_highlight_reel(reel_uuid: str) -> list[TextContent]:
    data = _get_v1(f"/highlightreel/{reel_uuid}")
    reel = data.get("highlightReel", data)
    return [TextContent(type="text", text=_format_highlight_reel(reel))]


async def _handle_get_session_embed(session_uuid: str) -> list[TextContent]:
    data = _get_v1(f"/session/{session_uuid}")
    session = data.get("session", data)
    return [TextContent(type="text", text=_format_session_v1(session))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "USERTESTING_CLIENT_ID and USERTESTING_CLIENT_SECRET must be set. "
            "Add them to mcp-usertesting-service/.env or set the environment variables."
        )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
