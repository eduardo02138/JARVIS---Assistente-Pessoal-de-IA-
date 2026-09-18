"""Implementacao Playwright da interface BaseComputer do ADK.

Conduz navegador Chromium via API assincrona do Playwright para o agente de
uso de computador (Computer Use) do Gemini. Inspirado no sample oficial do
google/adk-python (contributing/samples/computer_use/playwright.py).
"""

import logging
import os

from playwright.async_api import Page, Playwright, async_playwright

from google.adk.tools.computer_use.base_computer import (
    BaseComputer,
    ComputerEnvironment,
    ComputerState,
)

logger = logging.getLogger("jarvis.computer_use")

HEADLESS = os.environ.get("COMPUTER_USE_HEADLESS", "1").strip().lower() in (
    "1", "true", "sim", "yes",
)
SCREEN_LARGURA = int(os.environ.get("COMPUTER_USE_SCREEN_W", "1280"))
SCREEN_ALTURA = int(os.environ.get("COMPUTER_USE_SCREEN_H", "936"))
URL_INICIAL = os.environ.get("COMPUTER_USE_INITIAL_URL", "https://www.google.com")
URL_BUSCA = os.environ.get("COMPUTER_USE_SEARCH_URL", "https://www.google.com")

# Teclas que a API keyboard do Playwright entende por nome e a doc do
# computer-use costuma enviar como strings prontas ("enter", "control+c", ...).
_ALIASES_TECLAS = {
    "control": "Control",
    "ctrl": "Control",
    "command": "Meta",
    "cmd": "Meta",
    "super": "Meta",
    "option": "Alt",
    "return": "Enter",
    "escape": "Escape",
    "page_up": "PageUp",
    "page_down": "PageDown",
}


class PlaywrightComputer(BaseComputer):
    """Controla Chromium local via Playwright, com uma aba so (single tab)."""

    def __init__(
        self,
        screen_size: tuple[int, int] = (SCREEN_LARGURA, SCREEN_ALTURA),
        initial_url: str = URL_INICIAL,
        search_engine_url: str = URL_BUSCA,
        headless: bool = HEADLESS,
    ):
        self._screen_size = screen_size
        self._initial_url = initial_url
        self._search_engine_url = search_engine_url
        self._headless = headless
        self._pw: Playwright | None = None
        self._browser = None
        self._context = None
        self._page: Page | None = None

    async def _iniciar(self) -> Page:
        if self._page is not None and not self._page.is_closed():
            return self._page
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            viewport={
                "width": self._screen_size[0],
                "height": self._screen_size[1],
            }
        )
        self._page = await self._context.new_page()
        self._context.on("page", self._ao_abrir_nova_pagina)
        return self._page

    async def _ao_abrir_nova_pagina(self, page: Page) -> None:
        # O modelo de computer use suporta apenas uma aba: foca na mais nova.
        self._page = page
        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

    async def _estado(self) -> ComputerState:
        page = await self._iniciar()
        bytes_png = await page.screenshot(type="png")
        return ComputerState(screenshot=bytes_png, url=page.url)

    async def _coordenadas(
        self, x: int, y: int
    ) -> tuple[int, int]:
        """Escala coordenadas do espaco da tela para o viewport da pagina."""
        if self._page is None:
            return x, y
        try:
            viewport = self._page.viewport_size or {}
            largura = int(viewport.get("width") or self._screen_size[0])
            altura = int(viewport.get("height") or self._screen_size[1])
        except Exception:
            return x, y
        escala_x = largura / self._screen_size[0]
        escala_y = altura / self._screen_size[1]
        return max(0, min(largura - 1, int(x * escala_x))), max(
            0, min(altura - 1, int(y * escala_y))
        )

    # ---------------- interface BaseComputer ----------------

    async def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    async def environment(self) -> ComputerEnvironment:
        return ComputerEnvironment.ENVIRONMENT_BROWSER

    async def initialize(self) -> None:
        await self._iniciar()

    async def open_web_browser(self) -> ComputerState:
        page = await self._iniciar()
        try:
            await page.goto(self._initial_url, timeout=30000)
        except Exception as exc:
            logger.warning("Falha ao abrir %s: %s", self._initial_url, exc)
            await page.goto(self._search_engine_url, timeout=30000)
        return await self._estado()

    async def navigate(self, url: str) -> ComputerState:
        page = await self._iniciar()
        destino = url.strip()
        if "://" not in destino and not destino.startswith(
            ("about:", "data:", "file:", "chrome:", "view-source:")
        ):
            destino = f"https://{destino}"
        await page.goto(destino, timeout=30000)
        return await self._estado()

    async def search(self) -> ComputerState:
        page = await self._iniciar()
        await page.goto(self._search_engine_url, timeout=30000)
        return await self._estado()

    async def click_at(self, x: int, y: int) -> ComputerState:
        page = await self._iniciar()
        px, py = await self._coordenadas(x, y)
        await page.mouse.click(px, py)
        return await self._estado()

    async def hover_at(self, x: int, y: int) -> ComputerState:
        page = await self._iniciar()
        px, py = await self._coordenadas(x, y)
        await page.mouse.move(px, py)
        return await self._estado()

    async def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = True,
        clear_before_typing: bool = True,
    ) -> ComputerState:
        page = await self._iniciar()
        px, py = await self._coordenadas(x, y)
        await page.mouse.click(px, py)
        if clear_before_typing:
            await page.keyboard.press("Control+a")
        await page.keyboard.type(text)
        if press_enter:
            await page.keyboard.press("Enter")
        return await self._estado()

    async def scroll_document(
        self, direction: str
    ) -> ComputerState:
        page = await self._iniciar()
        if direction in ("up", "down"):
            passo = 600 if direction == "down" else -600
            for _ in range(3):
                await page.mouse.wheel(0, passo)
                await page.wait_for_timeout(120)
        else:
            tecla = "ArrowRight" if direction == "right" else "ArrowLeft"
            for _ in range(3):
                await page.keyboard.press(tecla)
                await page.wait_for_timeout(120)
        return await self._estado()

    async def scroll_at(
        self, x: int, y: int, direction: str, magnitude: int
    ) -> ComputerState:
        page = await self._iniciar()
        px, py = await self._coordenadas(x, y)
        await page.mouse.move(px, py)
        delta_y = magnitude if direction in ("up", "down") else 0
        if direction == "up":
            delta_y = -magnitude
        delta_x = magnitude if direction in ("left", "right") else 0
        if direction == "left":
            delta_x = -magnitude
        await page.mouse.wheel(delta_x, delta_y)
        return await self._estado()

    async def wait(self, seconds: int) -> ComputerState:
        page = await self._iniciar()
        await page.wait_for_timeout(int(seconds) * 1000)
        return await self._estado()

    async def go_back(self) -> ComputerState:
        page = await self._iniciar()
        try:
            await page.go_back(wait_until="domcontentloaded")
        except Exception:
            pass
        return await self._estado()

    async def go_forward(self) -> ComputerState:
        page = await self._iniciar()
        try:
            await page.go_forward(wait_until="domcontentloaded")
        except Exception:
            pass
        return await self._estado()

    async def key_combination(self, keys: list[str]) -> ComputerState:
        page = await self._iniciar()
        normalizadas = [
            _ALIASES_TECLAS.get(k.lower(), k) for k in (keys or [])
        ]
        combinacao = "+".join(normalizadas) if normalizadas else ""
        if combinacao:
            await page.keyboard.press(combinacao)
        return await self._estado()

    async def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> ComputerState:
        page = await self._iniciar()
        px, py = await self._coordenadas(x, y)
        dx, dy = await self._coordenadas(destination_x, destination_y)
        await page.mouse.move(px, py)
        await page.mouse.down()
        await page.mouse.move(dx, dy)
        await page.mouse.up()
        return await self._estado()

    async def current_state(self) -> ComputerState:
        return await self._estado()

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as exc:
            logger.warning("Falha ao fechar navegador: %s", exc)
        finally:
            self._browser = None
            self._context = None
            self._page = None
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None