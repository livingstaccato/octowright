# octowright

An MCP server that drives **many headed Playwright browsers in parallel** with a
mix of engines (Chromium, Firefox, WebKit), recording every action to a JSONL log
so a session can be exported as a standalone Playwright script.

## Install

```bash
cd ~/code/gh/provide-io/octowright
uv sync
uv run playwright install webkit firefox chromium
```
