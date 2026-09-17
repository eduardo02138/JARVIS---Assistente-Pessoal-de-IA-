#!/usr/bin/env python3
"""
Suíte Completa de Diagnóstico e Testes do Assistente J.A.R.V.I.S. / Gemini Live
Executa testes de ponta a ponta na infraestrutura local, pool de chaves e conexão Live.
"""
import os
import sys
import time
import asyncio
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_PATH, override=True)

# Cores do terminal
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"

def log_test(name, success, detail=""):
    icon = f"{GREEN}✔ PASS{RESET}" if success else f"{RED}✘ FAIL{RESET}"
    print(f" [{icon}] {BOLD}{name}{RESET}")
    if detail:
        print(f"        {detail}")

async def test_env_and_keys():
    raw_keys = os.environ.get("GEMINI_API_KEYS", "")
    key_pool = [k.strip() for k in raw_keys.split(",") if k.strip()]
    single_key = os.environ.get("GEMINI_API_KEY")
    if single_key and single_key not in key_pool:
        key_pool.insert(0, single_key)

    success = len(key_pool) > 0
    detail = f"Total de chaves configuradas no pool: {len(key_pool)} (Chave 1: {key_pool[0][:8]}...{key_pool[0][-4:] if key_pool else ''})"
    log_test("Configuração de Chaves (.env)", success, detail)
    return key_pool

async def test_system_tools():
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import system_tools
        stats = system_tools.get_system_status()
        cpu = stats.get("cpu_percent")
        ram = stats.get("ram_percent")
        success = cpu is not None and ram is not None
        log_test("Módulo de Automação do SO (system_tools)", success, f"CPU: {cpu}% | RAM: {ram}% | OS: {stats.get('uptime')}")
        return True
    except Exception as e:
        log_test("Módulo de Automação do SO (system_tools)", False, str(e))
        return False

async def test_gemini_live_handshake(key):
    from google import genai
    from google.genai import types

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest")
    start_t = time.time()
    try:
        client = genai.Client(api_key=key)
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            )
        )
        async with client.aio.live.connect(model=model, config=config) as session:
            # Envia prompt textual de teste rápido
            await session.send_realtime_input(text="JARVIS, responda apenas: Sistemas 100% operacionais.")
            audio_bytes = 0
            text_resp = ""
            async for resp in session.receive():
                if resp.server_content:
                    if resp.server_content.model_turn:
                        for p in resp.server_content.model_turn.parts:
                            if p.text:
                                text_resp += p.text
                            if p.inline_data:
                                audio_bytes += len(p.inline_data.data)
                    if resp.server_content.turn_complete:
                        break

            elapsed = int((time.time() - start_t) * 1000)
            success = audio_bytes > 0 or len(text_resp) > 0
            detail = f"Modelo: {model} | Latência: {elapsed}ms | Áudio recebido: {audio_bytes} bytes | Texto: \"{text_resp.strip()[:60]}\""
            log_test("Gemini Multimodal Live API (Bidirecional)", success, detail)
            return True
    except Exception as e:
        elapsed = int((time.time() - start_t) * 1000)
        log_test("Gemini Multimodal Live API (Bidirecional)", False, f"Erro após {elapsed}ms: {e}")
        return False

def test_pyside6_desktop():
    try:
        import PySide6
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        log_test("Ambiente Desktop Nativo (PySide6 + QtWebEngine)", True, f"PySide6 v{PySide6.__version__} instalado e funcional")
        return True
    except Exception as e:
        log_test("Ambiente Desktop Nativo (PySide6 + QtWebEngine)", False, str(e))
        return False

async def main():
    print(f"\n{CYAN}{BOLD}======================================================={RESET}")
    print(f"{CYAN}{BOLD}⚡ J.A.R.V.I.S. // SUÍTE DE DIAGNÓSTICO E AUTO-TESTE  {RESET}")
    print(f"{CYAN}{BOLD}======================================================={RESET}\n")

    keys = await test_env_and_keys()
    await test_system_tools()
    test_pyside6_desktop()

    if keys:
        print(f"\n{BOLD}Testando Conexão Live API com a Chave Primária...{RESET}")
        await test_gemini_live_handshake(keys[0])

    print(f"\n{CYAN}Diagnóstico finalizado.{RESET}\n")

if __name__ == "__main__":
    asyncio.run(main())
