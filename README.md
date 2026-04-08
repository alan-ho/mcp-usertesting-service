# mcp-usertesting-service

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Claude and other AI clients to UserTesting.com session data. Ask your AI assistant to list sessions, read transcripts, analyze QX scores, and summarize usability test results — all without leaving your chat interface.

## What It Does

Exposes five MCP tools:

| Tool | Description |
|------|-------------|
| `list_sessions` | Lists all sessions for a test (auto-paginates through all results) |
| `get_session_details` | Returns full session details: task results, participant info, and demographics |
| `get_transcript` | Returns the session transcript, parsed from VTT into readable timestamped text |
| `get_video_url` | Returns a pre-signed video download URL (valid for 1 hour) |
| `get_qx_scores` | Returns QX experience quality scores: overall (0–100), behavioral/attitudinal breakdown, and per-task scores |

And two MCP prompts for guided workflows:

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `analyze-test` | `test_id` (required) | Guides the AI to fetch sessions, QX scores, task details, and sample transcripts, then provide a full analysis |
| `summarize-test` | `test_id` (required), `audience` (optional) | Generates a stakeholder-ready summary with executive summary, key metrics, top findings, and recommendations |

## Prerequisites

- [Python 3.9+](https://www.python.org/downloads/)
- A UserTesting API client ID and secret (see [Get Your Credentials](#get-your-credentials) below)

## Get Your Credentials

1. Log into [app.usertesting.com](https://app.usertesting.com/)
2. Navigate to your account API settings to generate a client ID and secret
3. Paste both values into your `.env` file

## Get a Test ID

Test IDs identify individual tests in UserTesting. How to find yours depends on the test type:

**Interaction tests and Think-out-loud tests**
1. Log into [app.usertesting.com](https://app.usertesting.com/)
2. Open your test
3. Copy the **second number** from the URL: `app.usertesting.com/workspaces/{workspace_id}/study/{test_id}`

**Surveys**
1. Log into [app.usertesting.com](https://app.usertesting.com/)
2. Open your survey
3. Copy the UUID from the URL: `app.usertesting.com/workspaces/{workspace_id}/test/{uuid}/...`
4. Alternatively, the ID is included in the launch confirmation email

> **Note:** The Results API (v2) only supports Surveys, Interaction tests, and Think-out-loud tests. Classic test types (Live Conversation, Video Upload, etc.) require legacy endpoints.

## Get a Session ID

Session IDs identify individual participant recordings within a test. You can find them two ways:

- **From the UI:** Open a test, click a contributor's username, and look for **Session ID** under Contributor information. Session IDs also appear in the video player title bar.
- **From this MCP server:** Call `list_sessions` with your test ID — it returns all session IDs for that test.

## Setup

### Conda environment

```bash
conda create -n <your-env-name> python=3.11
conda activate <your-env-name>
pip install -r requirements.txt
```

> The configs below use `myenv` as a placeholder — replace it with your environment name.

### Install

```bash
git clone git@github.com:alan-ho/mcp-usertesting-service.git
cd mcp-usertesting-service
```

Create your `.env` file:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```
USERTESTING_CLIENT_ID=your_client_id_here
USERTESTING_CLIENT_SECRET=your_client_secret_here
```

## Add to Claude Code

Run once to register the server:

```bash
claude mcp add usertesting /path/to/anaconda3/envs/myenv/bin/python -- /path/to/mcp-usertesting-service/server.py
```

Or add it manually to your `.claude/settings.json`:

```json
{
  "mcpServers": {
    "usertesting": {
      "command": "/path/to/anaconda3/envs/myenv/bin/python",
      "args": ["/path/to/mcp-usertesting-service/server.py"]
    }
  }
}
```

## Add to Cursor

Add to `~/.cursor/mcp.json` (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "usertesting": {
      "command": "/path/to/anaconda3/envs/myenv/bin/python",
      "args": ["/path/to/mcp-usertesting-service/server.py"]
    }
  }
}
```

Restart Cursor. The UserTesting tools will appear in the MCP tools panel.

## Add to Claude Desktop

Add to your Claude Desktop config:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "usertesting": {
      "command": "/path/to/anaconda3/envs/myenv/bin/python",
      "args": ["/path/to/mcp-usertesting-service/server.py"]
    }
  }
}
```

Restart Claude Desktop after saving.

## Usage Examples

Once installed, you can ask your AI assistant:

- *"List all sessions for test `42812389`"*
- *"What did participants say in session `<session-id>`? Show me the transcript."*
- *"What are the QX scores for my latest usability test?"*
- *"Analyze the usertesting results for test `<test-id>`"* (uses the `analyze-test` prompt)
- *"Summarize the results for the product team"* (uses the `summarize-test` prompt)
