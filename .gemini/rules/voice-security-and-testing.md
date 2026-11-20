# Voice Security, Sandboxing & Reality Simulation Rules

## 1. Third-Party Guest Sandboxing (Strict Zero-Trust)
- Whenever an incoming or outgoing voice call connects to any non-owner peer (e.g. Fotnah `5032777692`, friends, recruiters, third parties):
  - **Tool Filtering at Schema Level:** Filter declared tools strictly to `[end_call, schedule_callback]`. Under no circumstances should vault modification, email drafting, bash commands, or credential tools be declared.
  - **Runtime Interceptor:** Intercept all tool execution requests in `gemini_live_bridge.py` and `telegram_voice_caller.py`. Reject any administrative action with a security warning log.
  - **Biometrics Bypass:** Outbound calls initiated by Aethelgard must mark biometrics as verified for the target peer, avoiding false-positive impostor lockouts on legitimate outgoing missions.

## 2. Multi-Persona Reality Testing
- Testing must not rely on single happy-path scripts. Use the **World Simulation Matrix** (`tests/world_simulation/`):
  - Always verify `@omar_twin` for tone, aviation ground ops context, and chained directives.
  - Always verify `@family_fotnah` for 100% third-party isolation.
  - Always verify `@red_team` for prompt injection and sandbox escape attempts.
  - Test under network chaos (packet loss, jitter, background noise) via `chaos_engine.py`.

## 3. Frontier Tech Scout Integrity
- Daily scout dossiers must be saved to `~/vault/Frontier_Tech_Scout/` with a 4-part architectural blueprint:
  1. Executive Overview & Technical Breakthrough.
  2. Direct Upgrade Value to Aethelgard.
  3. Actionable Code Blueprint & Integration Architecture.
  4. Prerequisites, Credentials & Environment Setup.
- Executive Telegram cards must be formatted cleanly with emojis, bullet points, and actionable next steps.
