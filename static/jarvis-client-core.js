/**
 * J.A.R.V.I.S. Client Core SDK (v1.0.0)
 * Núcleo cliente compartilhado entre Web HUD, Dashboard, Live Widget e Static ADK.
 * Gerencia tokens de sessão, requisições autenticadas, handshake WebSocket e conversão de áudio PCM.
 */

(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.JarvisClientCore = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    let _cachedToken = "";
    let _tokenPromise = null;

    /**
     * Obtém o token de sessão ativo.
     * Prioridade: 1) Cache em memória, 2) URL query param, 3) localStorage, 4) /api/auth/session.
     */
    async function getSessionToken(forceRefresh = false) {
        if (!forceRefresh && _cachedToken) {
            return _cachedToken;
        }

        // 1. Tenta recuperar da URL
        if (typeof window !== "undefined" && window.location) {
            const urlToken = new URLSearchParams(window.location.search).get("token");
            if (urlToken) {
                _cachedToken = urlToken.trim();
                localStorage.setItem("jarvis_token", _cachedToken);
                return _cachedToken;
            }
        }

        // 2. Tenta recuperar do localStorage
        if (typeof localStorage !== "undefined") {
            const saved = localStorage.getItem("jarvis_token");
            if (saved && !forceRefresh) {
                _cachedToken = saved.trim();
                return _cachedToken;
            }
        }

        // 3. Busca token emitido na sessão local via loopback
        if (_tokenPromise && !forceRefresh) {
            return _tokenPromise;
        }

        _tokenPromise = (async () => {
            try {
                const res = await fetch("/api/auth/session");
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.token) {
                        _cachedToken = data.token;
                        if (typeof localStorage !== "undefined") {
                            localStorage.setItem("jarvis_token", _cachedToken);
                        }
                        return _cachedToken;
                    }
                }
            } catch (err) {
                console.warn("[JarvisClientCore] Falha ao consultar /api/auth/session:", err);
            } finally {
                _tokenPromise = null;
            }
            return _cachedToken || "";
        })();

        return _tokenPromise;
    }

    /**
     * Executa fetch() injetando automaticamente cabeçalhos de autenticação Bearer e X-Jarvis-Token.
     */
    async function authenticatedFetch(url, options = {}) {
        const token = await getSessionToken();
        const headers = Object.assign({}, options.headers || {});
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
            headers["X-Jarvis-Token"] = token;
        }
        return fetch(url, Object.assign({}, options, { headers }));
    }

    /**
     * Converte buffer de áudio Float32 (-1.0 a 1.0) para PCM 16-bit little-endian Base64.
     */
    function float32ToInt16Base64(float32Array) {
        const int16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        let binary = '';
        const bytes = new Uint8Array(int16.buffer);
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    /**
     * Decodifica string Base64 PCM 16-bit little-endian para Float32Array normalizado.
     */
    function base64ToInt16Float32(base64Str) {
        const binary = atob(base64Str);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        const int16 = new Int16Array(bytes.buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 32768.0;
        }
        return float32;
    }

    /**
     * Monta o payload padronizado de handshake WebSocket.
     */
    async function buildInitPayload(extraData = {}) {
        const token = await getSessionToken(true);
        return Object.assign({
            type: "init",
            token: token
        }, extraData);
    }

    return {
        getSessionToken,
        authenticatedFetch,
        float32ToInt16Base64,
        base64ToInt16Float32,
        buildInitPayload,
    };
}));
