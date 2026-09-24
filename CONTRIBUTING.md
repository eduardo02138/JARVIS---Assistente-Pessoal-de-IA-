# Contributing to JARVIS

Thanks for your interest in improving JARVIS. Contributions are welcome across the voice runtime, Google ADK agents, Linux automation, plugins, MCP integration, security tests, documentation and UI.

## Before opening a pull request

1. Fork or create a branch from `main`.
2. Keep each pull request focused on one problem or feature.
3. Do not commit API keys, tokens, `.env` files, private logs or personal data.
4. Preserve the Policy Engine trust boundary: model output must not become unrestricted authority over the host machine.
5. Add or update tests when changing runtime, security or tool behavior.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements-dev.txt
cp .env.example .env
```

Use development/test credentials only.

## Validation

Run the relevant test suites before submitting:

```bash
export GEMINI_API_KEY="ci-dummy-key-test" JARVIS_TOKEN="ci-secret-token-test-123"
PYTHONPATH=. .venv/bin/pytest monitoring/test_trust_gates.py monitoring/test_mcp_client.py \
    monitoring/test_live_protocolo.py monitoring/test_reproduction_p0.py monitoring/test_perfil_maquina.py \
    monitoring/test_skills_mcp_ide.py -v
.venv/bin/python monitoring/test_suite.py --p0
.venv/bin/python monitoring/test_adk.py
```

New `test_*` functions in `monitoring/test_suite.py` or `monitoring/test_adk.py` must also be added to the explicit runner of that file (`run_p0_suite`/`main` or `executar_todos_testes_adk`); CI fails otherwise.

If your change touches a specific plugin or subsystem, add a focused regression test for that behavior.

## Architecture expectations

Prefer:

- explicit ownership of state and authority;
- small, testable components;
- async-safe behavior in WebSocket/voice paths;
- fail-closed behavior for privileged operations;
- reusable plugin/skill interfaces instead of central-file growth;
- structured errors and observable state;
- tests against production code paths rather than duplicate test-only implementations.

Avoid:

- silently bypassing the Policy Engine;
- duplicating provider/tool orchestration across multiple servers;
- blocking the event loop with synchronous I/O;
- source-string tests when runtime behavioral tests are possible;
- hidden global state that can leak between sessions;
- credentials or machine-specific absolute paths.

## Adding a plugin or ADK Skill

Use the existing plugin contracts in `plugin_sdk.py` and discovery logic in `plugin_manager.py`. Keep tool names descriptive, define risk classification, document parameters clearly and provide a focused `SKILL.md` when the capability should be exposed through ADK skills.

## Pull request description

Please include:

- problem being solved;
- architectural impact;
- files/subsystems changed;
- tests executed;
- known limitations or follow-up work;
- screenshots for UI changes.

## Security issues

Do not publish exploitable security findings in a public issue. See [`SECURITY.md`](SECURITY.md).
