/*
 * Utilitários compartilhados pelo HUD (static/app.js) e pelo widget
 * (gemini-live-widget/widget.js): token de sessão, conversão de áudio PCM,
 * interrupção da fila de reprodução e visão de tela.
 *
 * Carregado antes do script de cada interface; expõe globalThis.JarvisComum.
 */
(function (global) {
    "use strict";

    /** Obtém o token da sessão local (a rota só responde no loopback). */
    async function obterTokenDeSessao() {
        try {
            const res = await fetch("/api/auth/session");
            if (res.ok) {
                const data = await res.json();
                return data.token || "";
            }
        } catch (e) {
            console.warn("Falha ao inicializar token local:", e);
        }
        return "";
    }

    /** Reduz a taxa de amostragem do microfone (média por janela) para o PCM de 16 kHz da Live API. */
    function downsampleBuffer(buffer, inputSampleRate, targetSampleRate = 16000) {
        if (inputSampleRate === targetSampleRate) return buffer;
        if (inputSampleRate < targetSampleRate) return buffer;
        const ratio = inputSampleRate / targetSampleRate;
        const newLength = Math.round(buffer.length / ratio);
        const result = new Float32Array(newLength);
        let offsetResult = 0;
        let offsetBuffer = 0;
        while (offsetResult < result.length) {
            const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
            let accum = 0, count = 0;
            for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
                accum += buffer[i];
                count++;
            }
            result[offsetResult] = count > 0 ? accum / count : 0;
            offsetResult++;
            offsetBuffer = nextOffsetBuffer;
        }
        return result;
    }

    /**
     * Decodifica PCM 16-bit (base64) do Gemini para Float32 em [-1, 1].
     * Payloads de tamanho ímpar perdem o último byte: Int16Array exige alinhamento par.
     */
    function pcm16Base64ParaFloat32(base64Data) {
        const binary = atob(base64Data);
        const len = binary.length - (binary.length % 2);
        const float32 = new Float32Array(Math.max(0, len / 2));
        if (len <= 0) return float32;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
        const int16 = new Int16Array(bytes.buffer, 0, len / 2);
        for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;
        return float32;
    }

    /** Para na hora todo áudio agendado (barge-in ou interrupção vinda do servidor). */
    function interromperReproducao(state) {
        if (state.activeAudioSources && state.activeAudioSources.size > 0) {
            state.activeAudioSources.forEach((src) => {
                try {
                    src.stop(0);
                    src.disconnect();
                } catch (e) {}
            });
            state.activeAudioSources.clear();
        }
        if (state.audioCtx) {
            state.scheduledEndTime = state.audioCtx.currentTime;
        }
        state.speaking = false;
    }

    /**
     * Visão de tela: captura a tela com getDisplayMedia e envia frames JPEG
     * pelo WebSocket da sessão Live. O servidor só aceita os frames enquanto a
     * lease do Modo Controle estiver ativa.
     */
    function criarVisaoDeTela(obterSocket, { fps = 1, largura = 1024 } = {}) {
        const visao = { stream: null, timer: null, canvas: null, video: null };

        function parar() {
            if (visao.timer) clearInterval(visao.timer);
            if (visao.stream) visao.stream.getTracks().forEach((t) => t.stop());
            if (visao.video) visao.video.srcObject = null;
            visao.timer = null;
            visao.stream = null;
            visao.video = null;
        }

        async function iniciar() {
            if (visao.stream) return true;
            try {
                visao.stream = await navigator.mediaDevices.getDisplayMedia({
                    video: { frameRate: { ideal: fps, max: 5 } },
                    audio: false
                });
            } catch (e) {
                console.warn("Compartilhamento de tela recusado:", e);
                return false;
            }

            visao.video = document.createElement("video");
            visao.video.srcObject = visao.stream;
            visao.video.muted = true;
            await visao.video.play();

            visao.canvas = document.createElement("canvas");
            const contexto = visao.canvas.getContext("2d");

            // O usuário pode encerrar o compartilhamento pela barra do sistema
            visao.stream.getVideoTracks()[0].addEventListener("ended", () => parar());

            visao.timer = setInterval(() => {
                const ws = obterSocket();
                if (!visao.video || !visao.video.videoWidth) return;
                if (!ws || ws.readyState !== WebSocket.OPEN) return;
                const escala = largura / visao.video.videoWidth;
                visao.canvas.width = largura;
                visao.canvas.height = Math.round(visao.video.videoHeight * escala);
                contexto.drawImage(visao.video, 0, 0, visao.canvas.width, visao.canvas.height);
                const dados = visao.canvas.toDataURL("image/jpeg", 0.6).split(",")[1];
                ws.send(JSON.stringify({ type: "video", data: dados }));
            }, Math.round(1000 / fps));
            return true;
        }

        return { iniciar, parar };
    }

    global.JarvisComum = {
        obterTokenDeSessao,
        downsampleBuffer,
        pcm16Base64ParaFloat32,
        interromperReproducao,
        criarVisaoDeTela,
    };
})(globalThis);
