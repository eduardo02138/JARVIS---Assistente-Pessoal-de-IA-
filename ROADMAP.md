# JARVIS Roadmap

This roadmap communicates the direction of the project without promising fixed release dates. Priorities can change as runtime and security findings are discovered.

## Current focus — Runtime hardening

- [ ] Finish async runtime hardening and verify tool cancellation/timeout semantics.
- [ ] Keep WebSocket writes serialized across all production paths.
- [ ] Expand behavioral tests for voice mute/privacy and reconnection.
- [ ] Remove remaining mixed wall-clock/monotonic lease calculations.
- [ ] Reduce duplicated runtime/provider dispatch logic.

## Provider and agent architecture

- [ ] Make provider selection capability-aware instead of bypassing agent/tool orchestration.
- [ ] Keep Policy Engine enforcement independent from model/provider choice.
- [ ] Improve per-session isolation for provider/runtime state.
- [ ] Add integration tests covering provider failure, fallback and tool-capable requests.

## Voice and multimodal experience

- [ ] Improve real-time voice latency and interruption behavior.
- [ ] Add stronger reconnect/resume behavior for live sessions.
- [ ] Improve screen-context workflows and multimodal diagnostics.
- [ ] Document supported audio formats and latency expectations.

## Linux desktop experience

- [ ] Simplify installation on common Linux distributions.
- [ ] Improve Wayland/X11 compatibility documentation.
- [ ] Package the desktop widget for easier installation.
- [ ] Improve hardware/GPU telemetry portability.

## Google ADK, Skills and plugins

- [ ] Document a stable plugin/Skill authoring contract.
- [ ] Add plugin examples for third-party contributors.
- [ ] Add stronger lifecycle tests for plugins that depend on an event loop/background tasks.
- [ ] Improve skill discovery and context-budget visibility.

## MCP and external agents

- [ ] Document MCP setup for compatible IDEs and agents.
- [ ] Add capability discovery examples.
- [ ] Define clearer permission boundaries for externally invoked tools.

## Documentation and community

- [x] Add English-first landing README with screenshots.
- [x] Add Portuguese documentation.
- [x] Add CONTRIBUTING and SECURITY guides.
- [ ] Add architecture diagrams under `docs/`.
- [ ] Add a short demo GIF/video.
- [ ] Publish tagged releases with changelogs.
- [ ] Add issue templates for bugs and feature requests.

## Longer-term ideas

- Easier provider adapters without changing the orchestration layer.
- More local/offline model options where practical.
- Additional desktop integrations and reusable automation plugins.
- Better observability for tool calls, sessions and agent routing.
- Optional packaging/distribution for end users who do not want to manage a Python environment.

Contributions and architecture discussions are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
