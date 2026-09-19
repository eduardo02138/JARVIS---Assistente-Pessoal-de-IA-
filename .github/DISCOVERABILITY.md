# GitHub Discoverability Checklist

This checklist tracks public-facing improvements that are independent from the JARVIS runtime.

## Recommended repository metadata

### Repository name
Preferred: `jarvis-gemini-adk`

Alternatives:
- `jarvis-linux-ai-assistant`
- `jarvis-gemini-live`

Rationale: shorter, easier to share, English-first and more descriptive for GitHub search.

### Recommended description
`Multimodal Linux AI assistant powered by Gemini Live and Google ADK, with real-time voice, screen vision, secure tool calling, Computer Use and MCP.`

### Recommended topics
Keep the strongest discovery terms:
- ai-agent
- ai-assistant
- computer-use
- fastapi
- gemini
- gemini-live
- google-adk
- jarvis
- linux-automation
- mcp
- model-context-protocol
- multimodal-ai
- personal-assistant
- playwright
- python
- screen-vision
- tool-calling
- voice-agent
- voice-assistant
- websocket

## Social preview

Create a 1280×640 image using the real JARVIS HUD/widget screenshots. Suggested headline:

`JARVIS AI Assistant`

Suggested subheadline:

`Gemini Live · Google ADK · Linux Automation · Computer Use · MCP`

The image should prioritize the actual UI over decorative text.

## Release strategy

Do not market the project as stable while known runtime P1/P2 findings remain open.

When the runtime hardening items are closed:
1. Publish `v0.1.0-alpha`.
2. Include a concise changelog.
3. List supported Linux environments and Python version.
4. Link to screenshots and Quick Start.
5. Clearly label experimental/simulated integrations.

## Distribution checklist

After metadata and the first alpha release are ready:
- Share a short demo clip or GIF, not only a repository link.
- Lead with real-time voice + Linux automation + secure tool calling.
- Mention Google ADK, Gemini Live, Computer Use and MCP naturally.
- Link directly to the Quick Start for developers.
- Invite issues and small contributions through `CONTRIBUTING.md` and `ROADMAP.md`.
