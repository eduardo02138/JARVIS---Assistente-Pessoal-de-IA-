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

Use development/test credentials only. Run `.venv/bin/python diagnostico.py` to see what this machine is missing for each feature.

## Validation

Run the relevant test suites before submitting:

```bash
export GEMINI_API_KEY="ci-dummy-key-test" JARVIS_TOKEN="ci-secret-token-test-123"
PYTHONPATH=. .venv/bin/pytest monitoring/ -v
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
- credentials or machine-specific absolute paths;
- `subprocess.Popen` for apps, the browser or CLIs: use `processos.abrir_desanexado`/`processos.executar`, which drop JARVIS's secrets;
- ADK 2.0 pitfalls (enforced by `monitoring/test_compatibilidade_adk.py`): overriding `_run_async_impl`/`_run_live_impl` (ignored in 2.0, use callbacks), appending events to a session by hand, catching `BaseException` or using a bare `except` without re-raising, and passing `Event(...)` fields that don't exist;
- fetching URLs chosen by the model without `rede_segura.ler_url_publica`.

## Adding a plugin or ADK Skill

A plugin is a folder `plugins/<id>/` with a `plugin.json` manifest (`id` equal to the folder name, `name`, `version`, `entry` and optionally `category`, `icon`, `author`, `description`, `simulated`) and the module named in `entry`. `plugin_manager.py` discovers it from the manifest, so it needs no edit. In the module, subclass `JarvisPlugin` and build the metadata with `PluginMeta.do_manifesto(__file__)` so the manifest stays the single source.

Keep tool names descriptive, give every tool a `risk_level` (tools without one are blocked), document parameters clearly, mark demo-data plugins with `"simulated": true` and provide a focused `SKILL.md` in `skills/<skill-name>/` when the capability should be exposed through ADK skills.

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
