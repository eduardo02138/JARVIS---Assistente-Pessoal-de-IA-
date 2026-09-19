/**
 * Gemini Live // Floating Mini Widget & Desktop Controller
 * Suporte a Arraste Nativo (Bridge PySide6), Abas de Configuração
 * (Vozes & Microfone), VU Meter em tempo real e Web Audio.
 */

let jarvisSessionToken = "";
async function initSessionToken() {
    try {
        const res = await fetch("/api/auth/session");
        if (res.ok) {
            const data = await res.json();
            jarvisSessionToken = data.token;
            return jarvisSessionToken;
        }
    } catch (e) {
        console.warn("Falha ao inicializar token local no widget:", e);
    }
    return "";
}
initSessionToken();

// Detecta se está rodando dentro do App Desktop nativo
if (window.location.search.includes("app=1")) {
    document.body.classList.add("app-mode");
}

// Desbloqueia AudioContext no primeiro gesto do usuário
window.addEventListener("click", () => {
    if (state.audioCtx && state.audioCtx.state === "suspended") state.audioCtx.resume();
    if (state.inputAudioCtx && state.inputAudioCtx.state === "suspended") state.inputAudioCtx.resume();
}, { once: true });

let bridgeMsgCounter = 0;
function sendBridgeMessage(cmd, val) {
    bridgeMsgCounter = (bridgeMsgCounter + 1) % 10000;
    document.title = `${cmd}:${val}:${bridgeMsgCounter}`;
}

const state = {
    connected: false,
    listening: false,
    speaking: false,
    paused: false,
    ws: null,
    
    // Histórico & Contexto Ativo
    currentAiMsgElement: null,
    currentUserVoiceMsgElement: null,
    currentAiCaptionElement: null,
    currentUserVoiceCaptionElement: null,
    currentAiBubbleElement: null,
    currentUserVoiceBubbleElement: null,
    historyLog: [],

    // Configurações
    voice: localStorage.getItem("gemini_live_voice") || "Puck",
    currentMode: localStorage.getItem("jarvis_app_mode") || "tank-3.8",
    model: (function () {
        const mode = localStorage.getItem("jarvis_app_mode") || "tank-3.8";
        if (mode === "simples") return "gemini-flash-latest";
        if (mode === "live-flash") return "gemini-2.5-flash-native-audio-latest";
        if (mode === "computador") return "gemini-3.6-flash";
        return "gemini-3.8-live";
    })(),
    provider: localStorage.getItem("jarvis_provider") || "google_studio",
    micDeviceId: localStorage.getItem("gemini_mic_device") || "default",
    micMode: localStorage.getItem("gemini_mic_mode") || "always",
    echoCancellation: localStorage.getItem("gemini_echo") !== "false",
    noiseSuppression: localStorage.getItem("gemini_noise") !== "false",
    autoGainControl: localStorage.getItem("gemini_gain") !== "false",

    // Áudio
    audioCtx: null,
    inputAudioCtx: null,
    mediaStream: null,
    audioProcessor: null,
    analyser: null,
    micAnalyser: null,
    scheduledEndTime: 0,
    activeAudioSources: new Set(),
    bargeIn: localStorage.getItem("gemini_barge_in") === "true",
    
    // Canvas
    canvas: null,
    ctx: null,
    animFrame: null,
    wavePhase: 0,
    waveData: new Uint8Array(64)
};

// Elementos DOM
const dom = {
    widget: document.getElementById("geminiFloatingWidget"),
    expandedCard: document.getElementById("geminiExpandedCard"),
    widgetDragHandle: document.getElementById("widgetDragHandle"),
    expandedDragHandle: document.getElementById("expandedDragHandle"),
    liveStatusText: document.getElementById("liveStatusText"),
    statusBadgeChip: document.getElementById("statusBadgeChip"),
    chipLabel: document.getElementById("chipLabel"),
    liveWaveCanvas: document.getElementById("liveWaveCanvas"),
    auraPulse: document.getElementById("auraPulse"),
    btnPauseLive: document.getElementById("btnPauseLive"),
    iconPause: document.getElementById("iconPause"),
    iconPlay: document.getElementById("iconPlay"),
    btnToggleKeyboard: document.getElementById("btnToggleKeyboard"),
    keyboardDrawer: document.getElementById("keyboardDrawer"),
    drawerTextForm: document.getElementById("drawerTextForm"),
    drawerInput: document.getElementById("drawerInput"),
    btnDrawerMic: document.getElementById("btnDrawerMic"),
    widgetChatHistory: document.getElementById("widgetChatHistory"),
    btnClearHistory: document.getElementById("btnClearHistory"),
    btnCloseDrawer: document.getElementById("btnCloseDrawer"),
    btnToggleCaptions: document.getElementById("btnToggleCaptions"),
    captionsDrawer: document.getElementById("captionsDrawer"),
    captionsLog: document.getElementById("captionsLog"),
    transcriptionState: document.getElementById("transcriptionState"),
    
    // Configurações
    settingsPanel: document.getElementById("settingsPanel"),
    btnSettingsModal: document.getElementById("btnSettingsModal"),
    btnSettingsExpanded: document.getElementById("btnSettingsExpanded"),
    userAvatarBtn: document.getElementById("userAvatarBtn"),
    btnCloseSettingsPanel: document.getElementById("btnCloseSettingsPanel"),
    btnSaveConfig: document.getElementById("btnSaveConfig"),
    selectMicDevice: document.getElementById("selectMicDevice"),
    vuBarFill: document.getElementById("vuBarFill"),
    vuLevelVal: document.getElementById("vuLevelVal"),
    toggleEcho: document.getElementById("toggleEcho"),
    toggleNoise: document.getElementById("toggleNoise"),
    toggleGain: document.getElementById("toggleGain"),

    // Controles & Modos de Exibição
    btnSwitchView: document.getElementById("btnSwitchView"),
    selectWidgetHeaderMode: document.getElementById("selectWidgetHeaderMode"),
    selectExpandedMode: document.getElementById("selectExpandedMode"),
    btnMinimizeToWidget: document.getElementById("btnMinimizeToWidget"),
    btnToggleBackdrop: document.getElementById("btnToggleBackdrop"),
    btnToggleScreenShare: document.getElementById("btnToggleScreenShare"),
    pendingActionBanner: document.getElementById("pendingActionBanner"),
    btnApprovePending: document.getElementById("btnApprovePending"),
    btnRejectPending: document.getElementById("btnRejectPending"),
    btnSearchMemory: document.getElementById("btnSearchMemory"),
    memorySearchInput: document.getElementById("memorySearchInput"),
    expandedChatScroll: document.getElementById("expandedChatScroll"),
    expandedInput: document.getElementById("expandedInput"),
    btnExpandedMic: document.getElementById("btnExpandedMic"),
    btnCloseWidget: document.getElementById("btnCloseWidget"),
    btnCloseExpanded: document.getElementById("btnCloseExpanded"),

    // Provedores de Inteligência
    providerPill: document.getElementById("providerPill"),
    providerBadgeText: document.getElementById("providerBadgeText"),
    cardProviderGoogle: document.getElementById("cardProviderGoogle"),
    cardProviderOmni: document.getElementById("cardProviderOmni"),
    btnActivateGoogle: document.getElementById("btnActivateGoogle"),
    btnActivateOmni: document.getElementById("btnActivateOmni"),
    btnTestProvidersLatency: document.getElementById("btnTestProvidersLatency"),
    badgeStatusGoogle: document.getElementById("badgeStatusGoogle"),
    badgeStatusOmni: document.getElementById("badgeStatusOmni"),

    // Telemetria em Tela (HUD)
    btnToggleTelemetry: document.getElementById("btnToggleTelemetry"),
    telemetryDrawer: document.getElementById("telemetryDrawer"),
    btnRefreshTelemetry: document.getElementById("btnRefreshTelemetry"),
    btnCloseTelemetryDrawer: document.getElementById("btnCloseTelemetryDrawer"),
    hudCpuVal: document.getElementById("hudCpuVal"),
    hudCpuBar: document.getElementById("hudCpuBar"),
    hudCpuSub: document.getElementById("hudCpuSub"),
    hudGpuModel: document.getElementById("hudGpuModel"),
    hudGpuTempVal: document.getElementById("hudGpuTempVal"),
    hudGpuBar: document.getElementById("hudGpuBar"),
    hudGpuUso: document.getElementById("hudGpuUso"),
    hudVramVal: document.getElementById("hudVramVal"),
    hudRamVal: document.getElementById("hudRamVal"),
    hudRamBar: document.getElementById("hudRamBar"),
    hudRamSub: document.getElementById("hudRamSub"),
    hudActiveProviderName: document.getElementById("hudActiveProviderName"),
    hudActiveUptime: document.getElementById("hudActiveUptime")
};

// ---------------- SISTEMA DE ARRASTE DA JANELA NATIVA (WAYLAND & X11) ----------------
function setupWindowDragging() {
    let isDragging = false;

    // Escuta mousedown no documento inteiro, mas filtra para as áreas de arraste
    document.addEventListener("mousedown", (e) => {
        const dragHandle = e.target.closest(".widget-inner, #expandedDragHandle, .drag-grip, .brand-row");
        if (!dragHandle) return;

        // Se clicou em botão, input, select, link ou pílula de controle, permite o clique normal
        if (e.target.closest("button, input, select, .voice-card, a, .pill-btn, .icon-action-btn")) {
            return;
        }

        isDragging = true;
        document.body.style.userSelect = "none";
        
        // Dispara o startSystemMove do Wayland/X11 nativo imediatamente
        sendBridgeMessage("PYBRIDGE_START_DRAG", "1");
    });

    window.addEventListener("mousemove", (e) => {
        if (!isDragging) return;
        const dx = e.movementX;
        const dy = e.movementY;
        if (dx !== 0 || dy !== 0) {
            sendBridgeMessage("PYBRIDGE_MOVE", `${dx},${dy}`);
        }
    });

    window.addEventListener("mouseup", () => {
        if (isDragging) {
            isDragging = false;
            document.body.style.userSelect = "";
        }
    });
}

setupWindowDragging();

// ---------------- ANIMAÇÃO DO CANVAS DE ONDAS ----------------
function initWaveAnimation() {
    state.canvas = dom.liveWaveCanvas;
    state.ctx = state.canvas.getContext("2d");
    
    function draw() {
        state.animFrame = requestAnimationFrame(draw);
        const ctx = state.ctx;
        const width = state.canvas.width;
        const height = state.canvas.height;
        const centerY = height / 2;

        ctx.clearRect(0, 0, width, height);

        let audioEnergy = 0;
        if (state.analyser) {
            state.analyser.getByteFrequencyData(state.waveData);
            let sum = 0;
            for (let i = 0; i < state.waveData.length; i++) sum += state.waveData[i];
            audioEnergy = sum / state.waveData.length / 255;
        }

        state.wavePhase += 0.04 + audioEnergy * 0.1;

        const waves = [
            { color: "rgba(66, 133, 244, 0.75)", speed: 1.0, amp: 6 + audioEnergy * 20, freq: 0.025 },
            { color: "rgba(155, 114, 207, 0.7)", speed: 1.4, amp: 5 + audioEnergy * 16, freq: 0.035 },
            { color: "rgba(217, 101, 112, 0.65)", speed: 0.7, amp: 4 + audioEnergy * 12, freq: 0.02 }
        ];

        waves.forEach(w => {
            ctx.beginPath();
            ctx.strokeStyle = w.color;
            ctx.lineWidth = 2.5;
            ctx.lineCap = "round";

            for (let x = 0; x < width; x++) {
                const edgeFade = Math.sin((x / width) * Math.PI);
                const y = centerY + Math.sin(x * w.freq + state.wavePhase * w.speed) * w.amp * edgeFade;
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        });

        if (audioEnergy > 0.05) {
            dom.auraPulse.style.transform = `scale(${1 + audioEnergy * 0.6})`;
            dom.auraPulse.style.opacity = `${0.5 + audioEnergy * 0.5}`;
        } else {
            dom.auraPulse.style.transform = "scale(1)";
            dom.auraPulse.style.opacity = "0.5";
        }
    }

    draw();
}

// ---------------- VU METER DO MICROFONE EM TEMPO REAL ----------------
function updateVuMeter() {
    if (state.micAnalyser) {
        const data = new Uint8Array(state.micAnalyser.frequencyBinCount);
        state.micAnalyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const avg = sum / data.length;
        const percent = Math.min(100, Math.round((avg / 128) * 100));

        if (dom.vuBarFill && dom.vuLevelVal) {
            dom.vuBarFill.style.width = `${percent}%`;
            dom.vuLevelVal.textContent = `${percent}%`;
        }
    }
    requestAnimationFrame(updateVuMeter);
}
requestAnimationFrame(updateVuMeter);

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

// ---------------- WEB AUDIO: CAPTURA E REPRODUÇÃO PCM ----------------
async function initAudio() {
    if (!state.audioCtx) {
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 128;
        state.analyser.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === "suspended") await state.audioCtx.resume();

    if (!state.inputAudioCtx) {
        state.inputAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    }
    if (state.inputAudioCtx.state === "suspended") await state.inputAudioCtx.resume();

    // Encerra stream anterior se existir
    if (state.mediaStream) {
        state.mediaStream.getTracks().forEach(t => t.stop());
    }

    const audioConstraints = {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: state.echoCancellation,
        noiseSuppression: state.noiseSuppression,
        autoGainControl: state.autoGainControl
    };

    if (state.micDeviceId && state.micDeviceId !== "default") {
        audioConstraints.deviceId = { exact: state.micDeviceId };
    }

    try {
        state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });

        // Conecta micAnalyser para o VU Meter
        state.micAnalyser = state.inputAudioCtx.createAnalyser();
        state.micAnalyser.fftSize = 64;
        const micSource = state.inputAudioCtx.createMediaStreamSource(state.mediaStream);
        micSource.connect(state.micAnalyser);

        const processor = state.inputAudioCtx.createScriptProcessor(2048, 1, 1);
        let speechHoldover = 0;
        processor.onaudioprocess = (e) => {
            if (!state.connected || !state.listening || state.paused) return;

            // Muta o envio enquanto o assistente esta falando ou processando para evitar falso barge-in
            if (state.speaking || state.processing) return;

            // Se enviou texto recentemente, nao envia audio do mic para nao colidir com o comando
            if (state.pauseMicUntil && Date.now() < state.pauseMicUntil) return;

            const rawInput = e.inputBuffer.getChannelData(0);

            // Noise Gate / VAD: calcula a energia sonora do microfone
            let sumSq = 0;
            for (let i = 0; i < rawInput.length; i++) {
                sumSq += rawInput[i] * rawInput[i];
            }
            const rms = Math.sqrt(sumSq / rawInput.length);

            // Limiar de fala natural (0.003 calibrado para não podar voz normal/baixa; configurável via localStorage "gemini_vad_threshold" ou legado 0.012)
            const vadThreshold = parseFloat(localStorage.getItem("gemini_vad_threshold")) || 0.003;
            const isSpeaking = rms >= vadThreshold;
            const currentRate = state.inputAudioCtx.sampleRate || 16000;
            const maxHoldover = Math.ceil((currentRate / 2048) * 0.8); // ~800ms de tolerância a pausas naturais
            if (isSpeaking) {
                speechHoldover = maxHoldover;
            } else if (speechHoldover > 0) {
                speechHoldover--;
                if (speechHoldover === 0) {
                    // VAD Híbrida da Live API: notifica término de fala após pausa real e sustentada
                    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                        state.ws.send(JSON.stringify({ type: "audio_stream_end" }));
                    }
                }
            }

            if (!isSpeaking && speechHoldover <= 0) return;

            // Resample para 16kHz
            const inputData = downsampleBuffer(rawInput, currentRate, 16000);

            const pcm16 = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                let s = Math.max(-1, Math.min(1, inputData[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            const bytes = new Uint8Array(pcm16.buffer);
            let binary = "";
            for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
            const b64 = btoa(binary);

            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ type: "audio", data: b64 }));
            }

            const outputBuffer = e.outputBuffer.getChannelData(0);
            outputBuffer.fill(0);
        };

        micSource.connect(processor);
        processor.connect(state.inputAudioCtx.destination);
        state.audioProcessor = processor;

        if (state.paused) {
            // Se já estiver mutado, mantém trilhas desligadas e chip no estado mutado
            if (state.mediaStream) {
                try {
                    state.mediaStream.getAudioTracks().forEach(t => { t.enabled = false; });
                } catch (_) {}
            }
            if (state.inputAudioCtx && state.inputAudioCtx.state === "running") {
                try { state.inputAudioCtx.suspend(); } catch (_) {}
            }
            dom.liveStatusText.textContent = "Microfone pausado";
            dom.statusBadgeChip.classList.remove("active");
            dom.statusBadgeChip.classList.add("muted");
            dom.chipLabel.textContent = "Microfone Mutado";
            if (dom.btnExpandedMic) dom.btnExpandedMic.classList.remove("active");
            if (dom.btnDrawerMic) dom.btnDrawerMic.classList.remove("active");
        } else {
            dom.liveStatusText.textContent = "Gemini Live Conectado";
            dom.statusBadgeChip.classList.remove("muted");
            dom.statusBadgeChip.classList.add("active");
            dom.chipLabel.textContent = "Microfone Ativo";
            if (dom.btnExpandedMic) dom.btnExpandedMic.classList.add("active");
            if (dom.btnDrawerMic) dom.btnDrawerMic.classList.add("active");
        }
    } catch (err) {
        console.warn("Aviso de microfone:", err);
        dom.liveStatusText.textContent = "Microfone não autorizado";
    }

    // Lista microfones disponíveis
    populateMicrophoneList();
}

async function populateMicrophoneList() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = devices.filter(d => d.kind === "audioinput");
        
        dom.selectMicDevice.innerHTML = "";
        const defaultOpt = document.createElement("option");
        defaultOpt.value = "default";
        defaultOpt.textContent = "Microfone Padrão do Sistema";
        dom.selectMicDevice.appendChild(defaultOpt);

        audioInputs.forEach((dev, idx) => {
            const opt = document.createElement("option");
            opt.value = dev.deviceId;
            opt.textContent = dev.label || `Microfone ${idx + 1}`;
            if (dev.deviceId === state.micDeviceId) opt.selected = true;
            dom.selectMicDevice.appendChild(opt);
        });
    } catch (e) {
        console.warn("Erro ao listar dispositivos:", e);
    }
}

function playPCMResponse(base64Data) {
    if (state.paused) return;
    if (!state.audioCtx) {
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 128;
        state.analyser.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === "suspended") {
        state.audioCtx.resume();
    }

    const binary = atob(base64Data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const int16Array = new Int16Array(bytes.buffer);
    const float32Array = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) float32Array[i] = int16Array[i] / 32768.0;

    const audioBuffer = state.audioCtx.createBuffer(1, float32Array.length, 24000);
    audioBuffer.getChannelData(0).set(float32Array);

    const source = state.audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(state.analyser);
    try { source.connect(state.audioCtx.destination); } catch(e) {}

    const currentTime = state.audioCtx.currentTime;
    if (state.scheduledEndTime < currentTime) state.scheduledEndTime = currentTime;

    if (!state.activeAudioSources) {
        state.activeAudioSources = new Set();
    }
    state.activeAudioSources.add(source);

    source.start(state.scheduledEndTime);
    state.scheduledEndTime += audioBuffer.duration;

    state.speaking = true;
    dom.liveStatusText.textContent = "Gemini falando...";
    clearTimeout(state.speakingWatchdog);
    const remainingSecs = Math.max(0.2, (state.scheduledEndTime - state.audioCtx.currentTime) + 0.08);
    state.speakingWatchdog = setTimeout(() => {
        state.speaking = false;
        if (dom.liveStatusText.textContent.includes("falando")) {
            dom.liveStatusText.textContent = "Ouvindo você...";
        }
    }, remainingSecs * 1000);

    source.onended = () => {
        if (state.activeAudioSources) {
            state.activeAudioSources.delete(source);
        }
        if (state.audioCtx && state.audioCtx.currentTime >= state.scheduledEndTime - 0.08) {
            state.speaking = false;
            dom.liveStatusText.textContent = "Ouvindo você...";
        }
    };
}

function flushAudioQueue() {
    if (state.activeAudioSources && state.activeAudioSources.size > 0) {
        state.activeAudioSources.forEach(src => {
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
    dom.liveStatusText.textContent = "Ouvindo você...";
}


// ---------------- VISÃO DE TELA EM TEMPO REAL (MODO CONTROLE) ----------------
// Enquanto o Modo Controle estiver ativo, a tela é compartilhada com a sessão Live,
// como no compartilhamento de tela do Gemini Live. Sem isso o assistente opera às cegas.
const VISAO_FPS = 1;
const VISAO_LARGURA = 1024;
const visao = { stream: null, timer: null, canvas: null, video: null };

async function iniciarVisaoDeTela() {
    if (visao.stream) return true;
    try {
        visao.stream = await navigator.mediaDevices.getDisplayMedia({
            video: { frameRate: { ideal: VISAO_FPS, max: 5 } },
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
    visao.stream.getVideoTracks()[0].addEventListener("ended", () => pararVisaoDeTela());

    visao.timer = setInterval(() => {
        if (!visao.video || !visao.video.videoWidth) return;
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
        const escala = VISAO_LARGURA / visao.video.videoWidth;
        visao.canvas.width = VISAO_LARGURA;
        visao.canvas.height = Math.round(visao.video.videoHeight * escala);
        contexto.drawImage(visao.video, 0, 0, visao.canvas.width, visao.canvas.height);
        const dados = visao.canvas.toDataURL("image/jpeg", 0.6).split(",")[1];
        state.ws.send(JSON.stringify({ type: "video", data: dados }));
    }, Math.round(1000 / VISAO_FPS));
    return true;
}

function pararVisaoDeTela() {
    if (visao.timer) clearInterval(visao.timer);
    if (visao.stream) visao.stream.getTracks().forEach((t) => t.stop());
    if (visao.video) visao.video.srcObject = null;
    visao.timer = null;
    visao.stream = null;
    visao.video = null;
}

// ---------------- BANNER DE AUTORIZAÇÃO (POLICY ENGINE) ----------------
let pendingTimerInterval = null;
let currentPendingAction = null;

function exibirBannerAcaoPendente(acao) {
    currentPendingAction = acao;
    const banner = dom.pendingActionBanner;
    if (!banner) return;

    const toolNameEl = document.getElementById("pendingToolName");
    const argsEl = document.getElementById("pendingArgsPreview");
    const riskTag = document.getElementById("pendingRiskTag");
    const timerEl = document.getElementById("pendingTimerVal");

    const toolName = acao.tool_name || acao.name || acao.acao || "ferramenta_sensivel";
    const risk = acao.risk_level || "PRIVILEGED";
    const args = acao.args || acao.argumentos || {};

    if (toolNameEl) toolNameEl.textContent = toolName;
    if (argsEl) argsEl.textContent = JSON.stringify(args);
    if (riskTag) {
        riskTag.textContent = risk;
        riskTag.className = `risk-badge ${risk.toLowerCase()}`;
    }

    let segundosRestantes = acao.timeout_s || 60;
    if (timerEl) timerEl.textContent = `${segundosRestantes}s`;

    clearInterval(pendingTimerInterval);
    pendingTimerInterval = setInterval(() => {
        segundosRestantes--;
        if (timerEl) timerEl.textContent = `${segundosRestantes}s`;
        if (segundosRestantes <= 0) {
            clearInterval(pendingTimerInterval);
            ocultarBannerAcaoPendente();
        }
    }, 1000);

    banner.classList.remove("hidden");
    sendBridgeMessage("PYBRIDGE_RESIZE", "560,340");
}

function ocultarBannerAcaoPendente() {
    clearInterval(pendingTimerInterval);
    currentPendingAction = null;
    if (dom.pendingActionBanner) {
        dom.pendingActionBanner.classList.add("hidden");
    }
    if (!state.expanded && dom.keyboardDrawer && dom.keyboardDrawer.classList.contains("hidden")) {
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
}

async function aprovarAcaoPendente() {
    if (!currentPendingAction) return;
    const actionId = currentPendingAction.action_id || currentPendingAction.id || currentPendingAction.id_confirmacao;
    const sessao = currentPendingAction.sessao || "sessao-principal";
    const toolName = currentPendingAction.tool_name || currentPendingAction.name;
    try {
        const headers = { "Content-Type": "application/json" };
        if (jarvisSessionToken) headers["Authorization"] = `Bearer ${jarvisSessionToken}`;
        await fetch("/api/confirmar_acao", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({ id_confirmacao: actionId, sessao: sessao, aprovado: true })
        });
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ tipo: "confirmar_acao", id_confirmacao: actionId, aprovado: true }));
        }
        appendChatMessage("tool", `Ação autorizada pelo senhor: ${toolName}`, { source: "tool" });
    } catch (e) {
        console.warn("Erro ao autorizar ação:", e);
    }
    ocultarBannerAcaoPendente();
    if (toolName === "set_computer_mode") {
        await alterarModoComputador(true);
    }
}

async function rejeitarAcaoPendente() {
    if (!currentPendingAction) return;
    const actionId = currentPendingAction.action_id || currentPendingAction.id || currentPendingAction.id_confirmacao;
    try {
        const headers = { "Content-Type": "application/json" };
        if (jarvisSessionToken) headers["Authorization"] = `Bearer ${jarvisSessionToken}`;
        await fetch("/api/confirmar_acao", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({ id_confirmacao: actionId, sessao: "sessao-principal", aprovado: false })
        });
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ tipo: "confirmar_acao", id_confirmacao: actionId, aprovado: false }));
        }
        appendChatMessage("tool", `Ação cancelada pelo senhor.`, { source: "tool" });
    } catch (e) {
        console.warn("Erro ao rejeitar ação:", e);
    }
    ocultarBannerAcaoPendente();
}

function mostrarPedidoDeAutorizacao(msg) {
    appendChatMessage("tool", `Autorização necessária (${msg.risk_level || 'PRIVILEGED'}): ${msg.name || msg.tool_name}`, { source: "tool" });
    exibirBannerAcaoPendente(msg);
}

// ---------------- COORDENAÇÃO DE INSTÂNCIA ÚNICA (EVITA TELAS DUPLICADAS) ----------------
const instanceId = Math.random().toString(36).slice(2);
let isInstanceLeader = true;
let instanceChannel = null;

if (typeof BroadcastChannel !== "undefined") {
    try {
        instanceChannel = new BroadcastChannel("jarvis_desktop_channel");
        instanceChannel.onmessage = (event) => {
            const data = event.data || {};
            if (data.type === "CLAIM_INSTANCE" && data.id !== instanceId) {
                // Outra janela do JARVIS assumiu a liderança ativa
                colocarEmModoPassivo("Outra janela do JARVIS assumiu a sessão ativa.");
            }
        };
        // Notifica as demais janelas que esta agora está aberta e reivindica liderança
        instanceChannel.postMessage({ type: "CLAIM_INSTANCE", id: instanceId });
    } catch (e) {
        console.warn("BroadcastChannel indisponível:", e);
    }
}

function colocarEmModoPassivo(motivo) {
    isInstanceLeader = false;
    if (state.ws) {
        try {
            state.ws.close(1000, "Instância passiva");
        } catch (e) {}
    }
    toggleMicrophonePause(true); // muta o microfone para não ter duas escutas
    dom.liveStatusText.textContent = "Janela em espera (outra ativa)";
    exibirBannerInstanciaPassiva(motivo);
}

function exibirBannerInstanciaPassiva(motivo) {
    let banner = document.getElementById("instanceLockBanner");
    if (!banner) {
        banner = document.createElement("div");
        banner.id = "instanceLockBanner";
        banner.className = "instance-lock-banner";
        banner.innerHTML = `
            <div class="lock-banner-content">
                <span class="lock-icon">⚠️</span>
                <div class="lock-text">
                    <strong>Outra janela do JARVIS já está ativa.</strong>
                    <span>Esta tela foi colocada em espera para evitar dois assistentes falando ao mesmo tempo.</span>
                </div>
                <button type="button" id="btnClaimLeadership" class="claim-leadership-btn">Assumir Esta Janela</button>
            </div>
        `;
        document.body.appendChild(banner);
        const btn = document.getElementById("btnClaimLeadership");
        if (btn) {
            btn.addEventListener("click", () => {
                isInstanceLeader = true;
                const b = document.getElementById("instanceLockBanner");
                if (b) b.remove();
                if (instanceChannel) {
                    instanceChannel.postMessage({ type: "CLAIM_INSTANCE", id: instanceId });
                }
                toggleMicrophonePause(false);
                connectLiveBackend();
            });
        }
    }
}

// ---------------- WEBSOCKET BRIDGE COM GEMINI LIVE ----------------
async function connectLiveBackend() {
    if (!isInstanceLeader) return;
    // Sempre revalida: quando o servidor reinicia sem JARVIS_TOKEN fixo, ele gera um
    // token novo e o guardado em memória passa a ser recusado com código 1008.
    await initSessionToken();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    dom.liveStatusText.textContent = "Conectando ao Gemini...";

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        state.ws.send(JSON.stringify({
            type: "init",
            voice: state.voice,
            model: state.model,
            provider: state.provider,
            token: jarvisSessionToken,
            barge_in: state.bargeIn || false
        }));
    };

    state.ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);

        switch (msg.type) {
            case "user_transcription_interim":
                if (msg.text) {
                    appendChatMessage("user", `🎙️ ${msg.text}`, { source: "voice", updateExisting: true });
                    dom.liveStatusText.textContent = `Você: "${msg.text.slice(0, 30)}..."`;
                }
                break;
            case "user_transcription":
                if (msg.text) {
                    appendChatMessage("user", msg.text, { source: "voice", updateExisting: true });
                    dom.liveStatusText.textContent = `Você: "${msg.text.slice(0, 30)}..."`;
                }
                break;
            case "connected":
                state.connected = true;
                const provRotulo = (msg.provider || state.provider) === "omniroute" ? "OmniRoute" : "Google Studio";
                if (state.paused) {
                    dom.liveStatusText.textContent = "Sessão pausada";
                    dom.chipLabel.textContent = "Microfone Mutado";
                    dom.statusBadgeChip.classList.remove("active");
                    dom.statusBadgeChip.classList.add("muted");
                    dom.iconPlay.classList.remove("hidden");
                    dom.iconPause.classList.add("hidden");
                    if (dom.btnExpandedMic) dom.btnExpandedMic.classList.remove("active");
                    if (dom.btnDrawerMic) dom.btnDrawerMic.classList.remove("active");
                    updateProviderUI();
                    break;
                }
                dom.liveStatusText.textContent = `Gemini Live [${provRotulo}] (${msg.model || state.model})`;
                dom.statusBadgeChip.classList.remove("muted");
                dom.statusBadgeChip.classList.add("active");
                if (dom.btnExpandedMic) dom.btnExpandedMic.classList.add("active");
                if (dom.btnDrawerMic) dom.btnDrawerMic.classList.add("active");
                if (state.micMode !== "ptt") state.listening = true;
                initAudio();
                updateProviderUI();
                break;

            case "warn":
                dom.liveStatusText.textContent = `Aviso: ${msg.message}`;
                break;

            case "audio":
                playPCMResponse(msg.data);
                break;

            case "text":
                if (msg.text) {
                    const cleanText = msg.text
                        .replace(/<ctrl\d+>/gi, "")
                        .replace(/\*\*[^*]+\*\*/g, "")
                        .replace(/\n+/g, " ")
                        .trim();
                    if (
                        cleanText &&
                        !cleanText.startsWith("I've processed") &&
                        !cleanText.startsWith("Okay, I'm") &&
                        !cleanText.startsWith("Yes, I can hear you")
                    ) {
                        appendChatMessage("gemini", cleanText, { source: "voice", appendExisting: true });
                        dom.liveStatusText.textContent = cleanText.slice(0, 50) + (cleanText.length > 50 ? "..." : "");
                    }
                }
                break;

            case "interrupted":
                state.hasSpeechCaption = false;
                state.speaking = false;
                state.processing = false;
                state.currentAiMsgElement = null;
                state.currentUserVoiceMsgElement = null;
                state.currentAiCaptionElement = null;
                state.currentUserVoiceCaptionElement = null;
                state.currentAiBubbleElement = null;
                state.currentUserVoiceBubbleElement = null;
                flushAudioQueue();
                break;

            case "interaction_status":
                if (msg.status === "IN_PROGRESS") {
                    dom.liveStatusText.textContent = "Raciocinando em segundo plano...";
                } else if (msg.status === "IDLE") {
                    dom.liveStatusText.textContent = "Raciocínio concluído.";
                }
                break;

            case "thought":
                // Raciocínio explícito do gemini-3.8-live-extended-thinking
                if (msg.text) {
                    appendChatMessage("tool", `🧠 ${msg.text}`, { source: "tool" });
                }
                break;

            case "turn_complete":
                state.hasSpeechCaption = false;
                state.speaking = false;
                state.processing = false;
                state.currentAiMsgElement = null;
                state.currentUserVoiceMsgElement = null;
                state.currentAiCaptionElement = null;
                state.currentUserVoiceCaptionElement = null;
                state.currentAiBubbleElement = null;
                state.currentUserVoiceBubbleElement = null;
                dom.liveStatusText.textContent = "Ouvindo você...";
                break;

            case "tool_call":
                dom.liveStatusText.textContent = `Executando: ${msg.name}...`;
                appendChatMessage("tool", `Executando: ${msg.name}...`, { source: "tool" });
                break;

            case "tool_confirmation_request":
            case "acao_pendente":
                mostrarPedidoDeAutorizacao(msg);
                break;

            case "acao_aprovada":
            case "acao_rejeitada":
                ocultarBannerAcaoPendente();
                break;

            case "control_mode":
                if (msg.active) {
                    iniciarVisaoDeTela().then((ok) => {
                        appendChatMessage("tool", ok
                            ? "Modo Controle ativo com visão de tela: acompanho o que está na tela."
                            : "Modo Controle ativo SEM visão: o compartilhamento de tela foi recusado.",
                            { source: "tool" });
                    });
                } else {
                    pararVisaoDeTela();
                    appendChatMessage("tool", "Modo Controle desativado.", { source: "tool" });
                }
                break;

            case "computer_mode":
                dom.liveStatusText.textContent = msg.active
                    ? "Modo Computador (Computer Use) ativo..."
                    : "Ouvindo você...";
                break;

            case "ide_mode":
                dom.liveStatusText.textContent = msg.active
                    ? "Modo IDE ativo (Antigravity)..."
                    : "Ouvindo você...";
                break;

            case "fallback_text":
                // Contingência de voz: turno encerrado em silêncio após ferramenta
                if (msg.text) {
                    appendChatMessage("gemini", msg.text, { source: "voice", appendExisting: true });
                    dom.liveStatusText.textContent = msg.text.slice(0, 50) + (msg.text.length > 50 ? "..." : "");
                }
                break;

            case "policy_verbal_confirmation_approved":
                appendChatMessage("tool", "Autorização por voz reconhecida.", { source: "tool" });
                break;

            case "toggle_telemetry":
                toggleTelemetryDrawer(msg.active, msg.telemetry);
                break;

            case "tool_result":
                let detalheResultado = "";
                if (msg.result) {
                    if (msg.result.mensagem) {
                        detalheResultado = `: ${msg.result.mensagem}`;
                    } else if (msg.result.erro) {
                        detalheResultado = `: ⚠️ ${msg.result.erro}`;
                    } else if (typeof msg.result === "object") {
                        const partes = Object.entries(msg.result)
                            .filter(([k]) => k !== "sucesso" && k !== "erro")
                            .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`);
                        if (partes.length > 0) {
                            detalheResultado = `: ${partes.slice(0, 4).join(", ")}`;
                        }
                    }
                }
                appendChatMessage("tool", `Concluído: ${msg.name}${detalheResultado}`, { source: "tool" });
                dom.liveStatusText.textContent = detalheResultado ? detalheResultado.slice(2, 60) : "Ouvindo você...";
                break;

            case "superseded":
                colocarEmModoPassivo(msg.message || "Outra janela do JARVIS foi aberta.");
                break;

            case "error":
                state.speaking = false;
                state.processing = false;
                dom.liveStatusText.textContent = "Aviso: " + msg.message;
                break;
        }
    };

    state.ws.onclose = () => {
        state.connected = false;
        if (!isInstanceLeader) {
            dom.liveStatusText.textContent = "Janela em espera (outra ativa)";
            return;
        }
        dom.liveStatusText.textContent = "Reconectando em 3s...";
        setTimeout(() => {
            if (isInstanceLeader) connectLiveBackend();
        }, 3000);
    };
}

// ---------------- TRANSCRIÇÃO & HISTÓRICO DE CONVERSA (CONTEXTO ATIVO) ----------------
function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function appendChatMessage(sender, text, options = {}) {
    if (!text || !text.trim()) return null;
    const cleanText = text.trim();
    const source = options.source || "system"; // voice, typed, tool, system

    // Atualização em tempo real de fala do usuário (interim ou final do mesmo turno)
    if (options.updateExisting && sender === "user" && state.currentUserVoiceMsgElement) {
        const textEl = state.currentUserVoiceMsgElement.querySelector(".msg-text");
        if (textEl) textEl.textContent = cleanText;
        if (dom.widgetChatHistory) dom.widgetChatHistory.scrollTop = dom.widgetChatHistory.scrollHeight;

        if (state.currentUserVoiceCaptionElement) {
            state.currentUserVoiceCaptionElement.innerHTML = `<strong>Você:</strong> ${escapeHtml(cleanText)}`;
            if (dom.captionsLog) dom.captionsLog.scrollTop = dom.captionsLog.scrollHeight;
        }

        if (state.currentUserVoiceBubbleElement) {
            state.currentUserVoiceBubbleElement.textContent = cleanText;
            if (dom.expandedChatScroll) dom.expandedChatScroll.scrollTop = dom.expandedChatScroll.scrollHeight;
        }

        return state.currentUserVoiceMsgElement;
    }

    // Continuação incremental de texto da IA
    if (options.appendExisting && sender === "gemini" && state.currentAiMsgElement) {
        const textEl = state.currentAiMsgElement.querySelector(".msg-text");
        if (textEl) {
            const prev = textEl.textContent.trim();
            const needsSpace = prev.length > 0 && !prev.endsWith(" ") && !cleanText.startsWith(" ") && !/^[.,!?;:]/.test(cleanText);
            const added = (needsSpace ? " " : "") + cleanText;
            textEl.textContent += added;
            if (dom.widgetChatHistory) dom.widgetChatHistory.scrollTop = dom.widgetChatHistory.scrollHeight;

            if (state.currentAiCaptionElement) {
                state.currentAiCaptionElement.innerHTML += (needsSpace ? " " : "") + escapeHtml(cleanText);
                if (dom.captionsLog) dom.captionsLog.scrollTop = dom.captionsLog.scrollHeight;
            }

            if (state.currentAiBubbleElement) {
                state.currentAiBubbleElement.textContent += added;
                if (dom.expandedChatScroll) dom.expandedChatScroll.scrollTop = dom.expandedChatScroll.scrollHeight;
            }

            return state.currentAiMsgElement;
        }
    }

    // 1. Renderiza no Histórico Integrado do Widget Flutuante (#widgetChatHistory)
    if (dom.widgetChatHistory) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `chat-msg ${sender === "gemini" ? "ai" : sender}`;

        let avatar = "✨";
        let author = "JARVIS";
        if (sender === "user") {
            avatar = source === "voice" ? "🎤" : "⌨️";
            author = source === "voice" ? "Você (Voz)" : "Você (Texto)";
        } else if (sender === "tool") {
            avatar = "⚙️";
            author = "Sistema";
        }

        msgDiv.innerHTML = `
            <div class="msg-avatar">${avatar}</div>
            <div class="msg-content">
                <div class="msg-author">${author}</div>
                <div class="msg-text">${escapeHtml(cleanText)}</div>
            </div>
        `;

        dom.widgetChatHistory.appendChild(msgDiv);
        dom.widgetChatHistory.scrollTop = dom.widgetChatHistory.scrollHeight;

        if (sender === "user" && source === "voice") {
            state.currentUserVoiceMsgElement = msgDiv;
        } else if (sender === "gemini" && !options.isFinal) {
            state.currentAiMsgElement = msgDiv;
        }

        state.historyLog.push({ sender, text: cleanText, source, timestamp: Date.now() });
    }

    // 2. Registra na Gaveta de Legenda se ativa
    if (dom.captionsLog) {
        const item = document.createElement("div");
        item.className = `caption-item ${sender === "gemini" ? "gemini" : "user"}`;
        item.innerHTML = `<strong>${sender === "gemini" ? "Gemini" : "Você"}:</strong> ${escapeHtml(cleanText)}`;
        dom.captionsLog.appendChild(item);
        dom.captionsLog.scrollTop = dom.captionsLog.scrollHeight;

        if (sender === "user" && source === "voice") {
            state.currentUserVoiceCaptionElement = item;
        } else if (sender === "gemini" && !options.isFinal) {
            state.currentAiCaptionElement = item;
        }
    }

    // 3. Registra na Janela Expandida 3D se ativa
    if (dom.expandedChatScroll) {
        const chatBubble = document.createElement("div");
        chatBubble.className = `chat-bubble ${sender === "gemini" ? "ai" : "user"}`;
        chatBubble.textContent = cleanText;
        dom.expandedChatScroll.appendChild(chatBubble);
        dom.expandedChatScroll.scrollTop = dom.expandedChatScroll.scrollHeight;

        if (sender === "user" && source === "voice") {
            state.currentUserVoiceBubbleElement = chatBubble;
        } else if (sender === "gemini" && !options.isFinal) {
            state.currentAiBubbleElement = chatBubble;
        }
    }

    return null;
}

function appendCaption(speaker, text) {
    return appendChatMessage(speaker, text, { source: speaker === "user" ? "typed" : "voice" });
}

async function sendTextPrompt(text) {
    if (!text || !text.trim()) return;
    const clean = text.trim();
    state.currentUserVoiceMsgElement = null;
    state.currentAiMsgElement = null;

    appendChatMessage("user", clean, { source: "typed" });
    state.processing = true;
    dom.liveStatusText.textContent = "Processando pergunta...";

    if (state.currentMode === "simples" || state.currentMode === "computador") {
        try {
            const headers = { "Content-Type": "application/json" };
            if (jarvisSessionToken) headers["Authorization"] = `Bearer ${jarvisSessionToken}`;
            const bodyPayload = { texto: clean, usuario: "local", sessao: "sessao-widget" };
            if (state.currentMode === "computador") {
                bodyPayload.caminho = "computador";
            }
            const resp = await fetch("/api/chat", {
                method: "POST",
                headers: headers,
                body: JSON.stringify(bodyPayload)
            }).then(r => r.json());
            const respostaTexto = resp.resposta || resp.mensagem || "(sem resposta)";
            appendChatMessage("gemini", respostaTexto);
            dom.liveStatusText.textContent = `Respondido via ${resp.caminho || 'ADK'}`;
        } catch (err) {
            appendChatMessage("gemini", "Erro ao comunicar com o servidor.");
            dom.liveStatusText.textContent = "Erro na resposta.";
        }
        state.processing = false;
        return;
    }

    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.pauseMicUntil = Date.now() + 800;
        state.ws.send(JSON.stringify({ type: "text", text: clean }));
    }
}


// ---------------- MODO COMPUTADOR (LEASE DO POLICY ENGINE) ----------------
const COMPUTER_SESSION = "sessao-widget";

async function alterarModoComputador(ativo) {
    const headers = { "Content-Type": "application/json" };
    if (jarvisSessionToken) headers["Authorization"] = `Bearer ${jarvisSessionToken}`;
    try {
        const resp = await fetch("/api/computer/mode", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({ ativo: ativo, sessao: COMPUTER_SESSION, usuario: "local" })
        }).then(r => r.json());

        if (!ativo) return resp;

        if (resp.status === "aguardando_confirmacao") {
            appendChatMessage("tool", resp.mensagem || "Confirme a ativação do Modo Computador.", { source: "tool" });
            exibirBannerAcaoPendente({
                action_id: resp.id_confirmacao,
                tool_name: "set_computer_mode",
                risk_level: "PRIVILEGED",
                args: { descricao: "Ativação do Modo Computador (navegador Chromium)" },
                timeout_s: 60,
                sessao: COMPUTER_SESSION
            });
        } else if (resp.status === "ok") {
            appendChatMessage("tool", "Modo Computador autorizado. Navegador liberado.", { source: "tool" });
        }
        return resp;
    } catch (err) {
        console.warn("Falha ao alternar Modo Computador:", err);
        return null;
    }
}

// ---------------- GERENCIAMENTO DE MODOS (SIMPLES, LIVE FLASH & TANK 3.8) ----------------
function applyMode(mode, reconnect = true) {
    const modoAnterior = state.currentMode;
    state.currentMode = mode;
    localStorage.setItem("jarvis_app_mode", mode);

    if (modoAnterior === "computador" && mode !== "computador") {
        alterarModoComputador(false);
    }

    if (dom.selectWidgetHeaderMode) dom.selectWidgetHeaderMode.value = mode;
    if (dom.selectExpandedMode) dom.selectExpandedMode.value = mode;

    document.querySelectorAll(".mode-card").forEach(card => {
        card.classList.toggle("active", card.dataset.mode === mode);
    });

    if (mode === "simples") {
        state.model = "gemini-flash-latest";
        localStorage.setItem("jarvis_model", state.model);
        dom.liveStatusText.textContent = "Modo Simples (Texto Rápido)";
        dom.chipLabel.textContent = "⚡ Simples (Rápido)";
        dom.statusBadgeChip.className = "status-chip active mode-simples";
        appendCaption("gemini", "⚡ Modo Simples ativado. Respostas de texto diretas e ultrarrápidas via ADK.");
    } else if (mode === "live-flash") {
        state.model = "gemini-2.5-flash-native-audio-latest";
        localStorage.setItem("jarvis_model", state.model);
        dom.liveStatusText.textContent = "Gemini Live Flash (Voz Ágil)";
        dom.chipLabel.textContent = "🎙️ Live Flash";
        dom.statusBadgeChip.className = "status-chip active mode-flash";
        appendCaption("gemini", "🎙️ Gemini Live Flash ativado. Voz ágil e dinâmica conectada.");
        if (reconnect && state.ws) {
            state.ws.close();
            initWebSocket();
        }
    } else if (mode === "computador") {
        state.model = "gemini-2.5-computer-use-preview-10-2025";
        localStorage.setItem("jarvis_model", state.model);
        dom.liveStatusText.textContent = "Modo Computador (Chromium Playwright)";
        dom.chipLabel.textContent = "🖥️ Modo Computador";
        dom.statusBadgeChip.className = "status-chip active mode-computer";
        appendCaption("gemini", "🖥️ Modo Computador ativado. Agente autônomo pronto para operar a web.");
        if (reconnect) alterarModoComputador(true);
    } else { // tank-3.8
        state.model = "gemini-3.8-live";
        localStorage.setItem("jarvis_model", state.model);
        dom.liveStatusText.textContent = "Tank Gemini 3.8 Live (Potência Máxima)";
        dom.chipLabel.textContent = "🛡️ Tank 3.8 Live";
        dom.statusBadgeChip.className = "status-chip active mode-tank";
        appendCaption("gemini", "🛡️ Tank do Gemini 3.8 Live ativado. Potência máxima, raciocínio profundo e visão.");
        if (reconnect && state.ws) {
            state.ws.close();
            initWebSocket();
        }
    }

    if (state.paused) {
        dom.chipLabel.textContent = "Microfone Mutado";
        dom.statusBadgeChip.classList.remove("active");
        dom.statusBadgeChip.classList.add("muted");
    }
}

// ---------------- GESTÃO DE PROVEDORES (GOOGLE STUDIO 1º / OMNIROUTE 2º) ----------------
function updateProviderUI() {
    const isGoogle = state.provider === "google_studio";
    if (dom.providerBadgeText) {
        dom.providerBadgeText.textContent = isGoogle ? "Google Studio (1º)" : "OmniRoute (2º)";
    }
    if (dom.providerPill) {
        dom.providerPill.classList.toggle("omniroute-active", !isGoogle);
        dom.providerPill.title = isGoogle
            ? "1º Provedor: Google AI Studio API (Ativo). Clique para alternar para OmniRoute (2º Provedor)."
            : "2º Provedor: OmniRoute Proxy (Ativo). Clique para alternar para Google AI Studio (1º Provedor).";
    }
    if (dom.cardProviderGoogle) dom.cardProviderGoogle.classList.toggle("active", isGoogle);
    if (dom.cardProviderOmni) dom.cardProviderOmni.classList.toggle("active", !isGoogle);
    if (dom.btnActivateGoogle) {
        dom.btnActivateGoogle.textContent = isGoogle ? "✓ Provedor Ativo" : "Definir como Ativo";
        dom.btnActivateGoogle.classList.toggle("active", isGoogle);
    }
    if (dom.btnActivateOmni) {
        dom.btnActivateOmni.textContent = !isGoogle ? "✓ Provedor Ativo" : "Definir como Ativo";
        dom.btnActivateOmni.classList.toggle("active", !isGoogle);
    }
}

async function setProvider(newProvider, reconnect = true) {
    if (newProvider !== "google_studio" && newProvider !== "omniroute") return;

    try {
        const token = jarvisSessionToken || (await initSessionToken());
        const resp = await fetch("/api/providers/select", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Jarvis-Token": token,
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ provider: newProvider })
        });
        if (!resp.ok) {
            throw new Error(`Falha HTTP ${resp.status}: ${await resp.text()}`);
        }

        state.provider = newProvider;
        localStorage.setItem("jarvis_provider", newProvider);
        updateProviderUI();

        appendChatMessage("gemini", newProvider === "google_studio"
            ? "⭐ Provedor alterado: Google AI Studio API ativado como 1º Provedor (Primário)."
            : "🛡️ Provedor alterado: OmniRoute ativado como 2º Provedor (Proxy multi-contas)."
        );

        if (reconnect && state.ws) {
            state.ws.close();
            initWebSocket();
        }
    } catch (e) {
        console.warn("Falha ao sincronizar provedor com o backend:", e);
        appendChatMessage("gemini", `⚠️ Falha ao alterar provedor: ${e.message}`);
    }
}

if (dom.providerPill) {
    dom.providerPill.addEventListener("click", () => {
        const next = state.provider === "google_studio" ? "omniroute" : "google_studio";
        setProvider(next, true);
    });
}
if (dom.btnActivateGoogle) {
    dom.btnActivateGoogle.addEventListener("click", () => setProvider("google_studio", true));
}
if (dom.btnActivateOmni) {
    dom.btnActivateOmni.addEventListener("click", () => setProvider("omniroute", true));
}

if (dom.btnTestProvidersLatency) {
    dom.btnTestProvidersLatency.addEventListener("click", async () => {
        if (dom.providerLatencyReport) dom.providerLatencyReport.textContent = "Testando conexões...";
        try {
            const resp = await fetch("/api/providers/test", { method: "POST" });
            const data = await resp.json();
            if (data.status === "ok" && data.results) {
                const g = data.results.google_studio;
                const o = data.results.omniroute;
                dom.providerLatencyReport.textContent = `Google Studio: ${g.latency_ms}ms (${g.status}) | OmniRoute: ${o.latency_ms}ms (${o.status})`;
            } else {
                dom.providerLatencyReport.textContent = "Erro ao testar provedores.";
            }
        } catch (err) {
            dom.providerLatencyReport.textContent = `Erro: ${err.message}`;
        }
    });
}

// ---------------- CONFIGURAÇÃO DE VOZES ----------------
function updateVoiceSelectionUI() {
    document.querySelectorAll(".voice-card").forEach(card => {
        const v = card.getAttribute("data-voice");
        if (v === state.voice) {
            card.classList.add("selected");
        } else {
            card.classList.remove("selected");
        }
    });
}

document.querySelectorAll(".voice-card[data-voice]").forEach(card => {
    card.addEventListener("click", () => {
        state.voice = card.getAttribute("data-voice");
        localStorage.setItem("gemini_live_voice", state.voice);
        updateVoiceSelectionUI();
        
        // Reconecta com a nova voz
        if (state.ws) {
            state.ws.close();
        }
    });
});

// ---------------- NAVEGAÇÃO DE ABAS DE CONFIGURAÇÃO ----------------
document.querySelectorAll(".settings-nav-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".settings-nav-tabs .tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".settings-panel .tab-content").forEach(c => c.classList.remove("active"));
        
        btn.classList.add("active");
        const targetId = btn.getAttribute("data-tab");
        const targetContent = document.getElementById(targetId);
        if (targetContent) targetContent.classList.add("active");
    });
});

function toggleSettings(forceOpen) {
    const isHidden = dom.settingsPanel.classList.contains("hidden");
    const shouldOpen = forceOpen !== undefined ? forceOpen : isHidden;

    if (shouldOpen) {
        dom.settingsPanel.classList.remove("hidden");
        dom.keyboardDrawer.classList.add("hidden");
        dom.captionsDrawer.classList.add("hidden");
        if (dom.telemetryDrawer) toggleTelemetryDrawer(false);
        updateVoiceSelectionUI();
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,540");
    } else {
        dom.settingsPanel.classList.add("hidden");
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
}

dom.btnSettingsModal.addEventListener("click", () => toggleSettings());
dom.btnSettingsExpanded.addEventListener("click", () => toggleSettings());
dom.userAvatarBtn.addEventListener("click", () => toggleSettings());
dom.btnCloseSettingsPanel.addEventListener("click", () => toggleSettings(false));

// Salvar Configurações de Microfone
dom.btnSaveConfig.addEventListener("click", () => {
    state.micDeviceId = dom.selectMicDevice.value;
    state.echoCancellation = dom.toggleEcho.checked;
    state.noiseSuppression = dom.toggleNoise.checked;
    state.autoGainControl = dom.toggleGain.checked;

    localStorage.setItem("gemini_mic_device", state.micDeviceId);
    localStorage.setItem("gemini_echo", state.echoCancellation);
    localStorage.setItem("gemini_noise", state.noiseSuppression);
    localStorage.setItem("gemini_gain", state.autoGainControl);

    initAudio();
    toggleSettings(false);
});

// ---------------- TELEMETRIA EM TELA (HUD) ----------------
let telemetryPollInterval = null;

async function fetchTelemetryData() {
    try {
        const res = await fetch("/api/system/telemetry");
        if (res.ok) {
            const data = await res.json();
            if (data && data.telemetry) {
                renderTelemetryHUD(data.telemetry);
            }
        }
    } catch (e) {
        console.warn("Falha ao consultar telemetria:", e);
    }
}

function renderTelemetryHUD(t) {
    if (!t) return;
    if (dom.hudCpuVal) dom.hudCpuVal.textContent = t.cpu_percent || "--%";
    if (dom.hudCpuBar) dom.hudCpuBar.style.width = t.cpu_percent || "0%";
    if (dom.hudCpuSub) dom.hudCpuSub.textContent = `Núcleos lógicos: ${t.cpu_cores || "--"}`;

    if (dom.hudGpuModel) dom.hudGpuModel.textContent = t.gpu_modelo || "GPU NVIDIA RTX";
    if (dom.hudGpuTempVal) dom.hudGpuTempVal.textContent = t.gpu_temp || "--°C";
    if (dom.hudGpuBar) dom.hudGpuBar.style.width = t.gpu_uso || "0%";
    if (dom.hudGpuUso) dom.hudGpuUso.textContent = t.gpu_uso || "--%";
    if (dom.hudVramVal) dom.hudVramVal.textContent = `${t.vram_usada || "--"} / ${t.vram_total || "--"}`;

    if (dom.hudRamVal) dom.hudRamVal.textContent = t.ram_percent || "--%";
    if (dom.hudRamBar) dom.hudRamBar.style.width = t.ram_percent || "0%";
    if (dom.hudRamSub) dom.hudRamSub.textContent = `${t.ram_used_gb || "--"} de ${t.ram_total_gb || "--"}`;

    if (dom.hudActiveProviderName) {
        dom.hudActiveProviderName.textContent = state.provider === "omniroute"
            ? "OmniRoute Local Proxy (2º)"
            : "Google AI Studio API (1º)";
    }
    if (dom.hudActiveUptime) {
        dom.hudActiveUptime.textContent = t.uptime ? `● UPTIME: ${t.uptime}` : "● ONLINE";
    }
}

function toggleTelemetryDrawer(forceState, data) {
    if (!dom.telemetryDrawer) return;
    const isCurrentlyOpen = !dom.telemetryDrawer.classList.contains("hidden");
    const shouldOpen = forceState !== undefined ? forceState : !isCurrentlyOpen;

    if (shouldOpen) {
        dom.telemetryDrawer.classList.remove("hidden");
        if (dom.btnToggleTelemetry) dom.btnToggleTelemetry.classList.add("active");
        dom.settingsPanel.classList.add("hidden");
        dom.captionsDrawer.classList.add("hidden");
        dom.keyboardDrawer.classList.add("hidden");
        if (dom.btnToggleCaptions) dom.btnToggleCaptions.classList.remove("active");
        if (dom.btnToggleKeyboard) dom.btnToggleKeyboard.classList.remove("active");

        sendBridgeMessage("PYBRIDGE_RESIZE", "560,450");

        if (data) {
            renderTelemetryHUD(data);
        } else {
            fetchTelemetryData();
        }

        if (telemetryPollInterval) clearInterval(telemetryPollInterval);
        telemetryPollInterval = setInterval(fetchTelemetryData, 2500);
    } else {
        dom.telemetryDrawer.classList.add("hidden");
        if (dom.btnToggleTelemetry) dom.btnToggleTelemetry.classList.remove("active");
        if (telemetryPollInterval) {
            clearInterval(telemetryPollInterval);
            telemetryPollInterval = null;
        }
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
}

if (dom.btnToggleTelemetry) {
    dom.btnToggleTelemetry.addEventListener("click", () => toggleTelemetryDrawer());
}
if (dom.btnCloseTelemetryDrawer) {
    dom.btnCloseTelemetryDrawer.addEventListener("click", () => toggleTelemetryDrawer(false));
}
if (dom.btnRefreshTelemetry) {
    dom.btnRefreshTelemetry.addEventListener("click", () => fetchTelemetryData());
}

// ---------------- CONTROLE DE MODOS & MICROFONE / PAUSA ----------------
async function toggleMicrophonePause(forceState) {
    const nextPaused = (forceState !== undefined) ? forceState : !state.paused;
    state.paused = nextPaused;

    if (state.paused) {
        state.listening = false;
        if (dom.iconPause) dom.iconPause.classList.add("hidden");
        if (dom.iconPlay) dom.iconPlay.classList.remove("hidden");
        if (dom.btnPauseLive) dom.btnPauseLive.title = "Ativar microfone (Retomar)";
        dom.liveStatusText.textContent = "Microfone pausado";
        dom.chipLabel.textContent = "Microfone Mutado";
        dom.statusBadgeChip.classList.remove("active");
        dom.statusBadgeChip.classList.add("muted");
        if (dom.btnExpandedMic) dom.btnExpandedMic.classList.remove("active");
        if (dom.btnDrawerMic) dom.btnDrawerMic.classList.remove("active");
        flushAudioQueue();

        // 1. Hardware Mute: desabilita trilhas de captura no nível da mídia
        if (state.mediaStream) {
            try {
                state.mediaStream.getAudioTracks().forEach(t => { t.enabled = false; });
            } catch (e) {
                console.warn("Falha ao desabilitar tracks do microfone:", e);
            }
        }

        // 2. Suspende AudioContext de captura
        if (state.inputAudioCtx && state.inputAudioCtx.state === "running") {
            try { state.inputAudioCtx.suspend(); } catch (_) {}
        }

        // 3. Notifica o backend para fail-closed (backend é a autoridade única do audio_stream_end)
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            try {
                state.ws.send(JSON.stringify({ type: "microphone_state", muted: true }));
            } catch (e) {
                console.warn("Falha ao notificar estado de microfone mutado via WebSocket:", e);
            }
        }
    } else {
        if (dom.iconPlay) dom.iconPlay.classList.add("hidden");
        if (dom.iconPause) dom.iconPause.classList.remove("hidden");
        if (dom.btnPauseLive) dom.btnPauseLive.title = "Pausar microfone";
        dom.liveStatusText.textContent = "Ouvindo você...";
        dom.chipLabel.textContent = "Microfone Ativo";
        dom.statusBadgeChip.classList.remove("muted");
        dom.statusBadgeChip.classList.add("active");
        if (dom.btnExpandedMic) dom.btnExpandedMic.classList.add("active");
        if (dom.btnDrawerMic) dom.btnDrawerMic.classList.add("active");

        // 1. Reabilita trilhas de captura no nível da mídia
        if (state.mediaStream) {
            try {
                state.mediaStream.getAudioTracks().forEach(t => { t.enabled = true; });
            } catch (e) {
                console.warn("Falha ao reabilitar tracks do microfone:", e);
            }
        }

        if (state.micMode !== "ptt") state.listening = true;

        // 2. Desperta AudioContext se suspenso pelo navegador
        try {
            if (state.inputAudioCtx && state.inputAudioCtx.state === "suspended") {
                await state.inputAudioCtx.resume();
            }
            if (state.audioCtx && state.audioCtx.state === "suspended") {
                await state.audioCtx.resume();
            }
        } catch (e) {
            console.warn("Falha ao resumir AudioContext:", e);
        }

        // 3. Notifica o backend que o microfone está desmutado
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            try {
                state.ws.send(JSON.stringify({ type: "microphone_state", muted: false }));
            } catch (e) {
                console.warn("Falha ao notificar estado de microfone ativo via WebSocket:", e);
            }
        }

        // Se ainda não inicializou o microfone ou a stream caiu, inicializa agora
        if (!state.mediaStream || !state.mediaStream.active || !state.inputAudioCtx) {
            await initAudio();
        }
    }
}

if (dom.btnPauseLive) {
    dom.btnPauseLive.addEventListener("click", () => toggleMicrophonePause());
}
if (dom.statusBadgeChip) {
    dom.statusBadgeChip.addEventListener("click", () => toggleMicrophonePause());
}
if (dom.btnExpandedMic) {
    dom.btnExpandedMic.addEventListener("click", () => toggleMicrophonePause());
}
if (dom.btnDrawerMic) {
    dom.btnDrawerMic.addEventListener("click", () => toggleMicrophonePause());
}

// Alternar Modo de Escrita / Teclado com Expansão e Histórico (~3-4 cm)
function toggleKeyboardDrawer(forceState) {
    const isCurrentlyOpen = !dom.keyboardDrawer.classList.contains("hidden");
    const shouldOpen = forceState !== undefined ? forceState : !isCurrentlyOpen;

    if (shouldOpen) {
        dom.keyboardDrawer.classList.remove("hidden");
        dom.btnToggleKeyboard.classList.add("active");
        dom.settingsPanel.classList.add("hidden");
        dom.captionsDrawer.classList.add("hidden");
        if (dom.telemetryDrawer) toggleTelemetryDrawer(false);
        dom.btnToggleCaptions.classList.remove("active");

        // Expande o widget flutuante em ~3.5 a 4.5 cm (altura de 240px para 420px)
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,420");

        setTimeout(() => {
            dom.drawerInput.focus();
            if (dom.widgetChatHistory) {
                dom.widgetChatHistory.scrollTop = dom.widgetChatHistory.scrollHeight;
            }
        }, 60);
    } else {
        dom.keyboardDrawer.classList.add("hidden");
        dom.btnToggleKeyboard.classList.remove("active");

        // Restaura a dimensão padrão do mini widget flutuante
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
}

dom.btnToggleKeyboard.addEventListener("click", () => {
    toggleKeyboardDrawer();
});

if (dom.btnCloseDrawer) {
    dom.btnCloseDrawer.addEventListener("click", () => {
        toggleKeyboardDrawer(false);
    });
}

if (dom.btnClearHistory) {
    dom.btnClearHistory.addEventListener("click", () => {
        if (dom.widgetChatHistory) {
            dom.widgetChatHistory.innerHTML = `
                <div class="chat-msg ai">
                    <div class="msg-avatar">✨</div>
                    <div class="msg-content">
                        <div class="msg-author">JARVIS</div>
                        <div class="msg-text">Histórico limpo. O contexto de voz e texto continuará sendo mantido aqui.</div>
                    </div>
                </div>
            `;
        }
    });
}

// Envio de texto pelo Modo de Escrita: MANTÉM a gaveta aberta e o histórico visível
dom.drawerTextForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const val = dom.drawerInput.value.trim();
    if (val) {
        sendTextPrompt(val);
        dom.drawerInput.value = "";
        // Mantém o foco no campo para continuar digitando ou conversando
        dom.drawerInput.focus();
    }
});

// Alternar Legendas
dom.btnToggleCaptions.addEventListener("click", () => {
    dom.captionsDrawer.classList.toggle("hidden");
    dom.btnToggleCaptions.classList.toggle("active");
    dom.settingsPanel.classList.add("hidden");
    dom.keyboardDrawer.classList.add("hidden");
    dom.btnToggleKeyboard.classList.remove("active");
    if (dom.telemetryDrawer) toggleTelemetryDrawer(false);
    if (!dom.captionsDrawer.classList.contains("hidden")) {
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,380");
    } else {
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
});

// Alternar entre Mini Widget e Janela Expandida
function setViewMode(mode) {
    if (mode === "expanded") {
        dom.widget.classList.add("hidden");
        dom.expandedCard.classList.remove("hidden");
        sendBridgeMessage("PYBRIDGE_RESIZE", "460,620");
    } else {
        dom.expandedCard.classList.add("hidden");
        dom.widget.classList.remove("hidden");
        sendBridgeMessage("PYBRIDGE_RESIZE", "560,240");
    }
}

dom.btnSwitchView.addEventListener("click", () => {
    const isWidgetVisible = !dom.widget.classList.contains("hidden");
    setViewMode(isWidgetVisible ? "expanded" : "widget");
});

dom.btnMinimizeToWidget.addEventListener("click", () => setViewMode("widget"));
dom.btnCloseExpanded.addEventListener("click", () => setViewMode("widget"));

// PUSH-TO-TALK via tecla ESPAÇO
window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && state.micMode === "ptt" && document.activeElement.tagName !== "INPUT") {
        state.listening = true;
        dom.chipLabel.textContent = "Escutando...";
    }
});

window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && state.micMode === "ptt" && document.activeElement.tagName !== "INPUT") {
        state.listening = false;
        dom.chipLabel.textContent = "Solto (Pressione Espaço)";
    }
});

// Envio de texto pela janela expandida
dom.expandedInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        const val = dom.expandedInput.value.trim();
        if (val) {
            sendTextPrompt(val);
            dom.expandedInput.value = "";
        }
    }
});

// Alternar Cenário / Fundo
dom.btnToggleBackdrop.addEventListener("click", () => {
    document.body.classList.toggle("transparent-mode");
});

// Fechar / Ocultar
dom.btnCloseWidget.addEventListener("click", () => {
    dom.widget.style.opacity = "0";
    setTimeout(() => {
        dom.widget.classList.add("hidden");
        sendBridgeMessage("PYBRIDGE_HIDE", "1");
    }, 200);
});

// ---------------- COMPARTILHAMENTO DE TELA EM TEMPO REAL (MULTIMODAL LIVE) ----------------
let screenStream = null;
let screenCaptureInterval = null;
const screenCanvas = document.createElement("canvas");
const screenCtx = screenCanvas.getContext("2d");
screenCanvas.width = 1280;
screenCanvas.height = 720;
const screenVideo = document.createElement("video");
screenVideo.autoplay = true;
screenVideo.muted = true;
screenVideo.playsInline = true;

async function toggleScreenShare() {
    if (screenStream || screenCaptureInterval) {
        pararCompartilhamentoTela();
        return;
    }

    try {
        screenStream = await navigator.mediaDevices.getDisplayMedia({
            video: {
                width: { ideal: 1280, max: 1920 },
                height: { ideal: 720, max: 1080 },
                frameRate: { ideal: 1, max: 5 }
            },
            audio: false
        });

        screenVideo.srcObject = screenStream;
        screenVideo.muted = true;
        screenVideo.playsInline = true;
        try {
            await screenVideo.play();
        } catch (playErr) {
            console.warn("Autoplay de vídeo:", playErr);
        }

        if (dom.btnToggleScreenShare) {
            dom.btnToggleScreenShare.classList.add("active-screen");
            dom.btnToggleScreenShare.title = "Parar Compartilhamento de Tela";
        }
        dom.chipLabel.textContent = "Visão de Tela Ativa";
        dom.statusBadgeChip.classList.remove("muted");
        dom.statusBadgeChip.classList.add("active");
        appendChatMessage("tool", "Compartilhamento de tela iniciado. A IA está recebendo frames em tempo real.", { source: "tool" });

        // Envia frames JPEG a cada 1.5s
        if (screenCaptureInterval) clearInterval(screenCaptureInterval);
        screenCaptureInterval = setInterval(() => {
            if (!screenStream || !state.ws || state.ws.readyState !== WebSocket.OPEN) return;
            try {
                if (screenVideo.videoWidth > 0 && screenVideo.videoHeight > 0) {
                    screenCtx.drawImage(screenVideo, 0, 0, 1280, 720);
                    const dataUrl = screenCanvas.toDataURL("image/jpeg", 0.55);
                    const b64 = dataUrl.split(",")[1];
                    if (b64) {
                        state.ws.send(JSON.stringify({ type: "screen_frame", data: b64 }));
                    }
                }
            } catch (e) {
                console.warn("Erro ao capturar frame:", e);
            }
        }, 1500);

        screenStream.getVideoTracks()[0].addEventListener("ended", () => {
            pararCompartilhamentoTela();
        });
    } catch (err) {
        if (err.name === "NotAllowedError" || err.name === "AbortError") {
            console.info("Compartilhamento de tela cancelado pelo usuário.");
        } else {
            console.warn("Compartilhamento de tela cancelado ou negado:", err);
            appendChatMessage("tool", `Aviso: não foi possível iniciar compartilhamento de tela (${err.message || 'permissão negada'}).`, { source: "tool" });
        }

        if (screenStream) {
            pararCompartilhamentoTela();
        } else if (dom.btnToggleScreenShare) {
            dom.btnToggleScreenShare.classList.remove("active-screen");
            dom.btnToggleScreenShare.title = "Compartilhar Tela com a IA (Visão em Tempo Real)";
        }
    }
}

function pararCompartilhamentoTela() {
    const estavaAtivo = Boolean(screenStream || screenCaptureInterval);
    if (screenCaptureInterval) {
        clearInterval(screenCaptureInterval);
        screenCaptureInterval = null;
    }
    if (screenStream) {
        try {
            screenStream.getTracks().forEach(t => t.stop());
        } catch (e) {}
        screenStream = null;
    }
    screenVideo.srcObject = null;
    if (dom.btnToggleScreenShare) {
        dom.btnToggleScreenShare.classList.remove("active-screen");
        dom.btnToggleScreenShare.title = "Compartilhar Tela com a IA (Visão em Tempo Real)";
    }
    dom.chipLabel.textContent = state.paused ? "Microfone Mutado" : "Microfone Ativo";
    if (state.paused) {
        dom.statusBadgeChip.classList.remove("active");
        dom.statusBadgeChip.classList.add("muted");
    } else {
        dom.statusBadgeChip.classList.remove("muted");
        dom.statusBadgeChip.classList.add("active");
    }
    if (estavaAtivo) {
        appendChatMessage("tool", "Compartilhamento de tela encerrado.", { source: "tool" });
    }
}

// ---------------- GESTÃO DE MEMÓRIA DE LONGO PRAZO ----------------
async function carregarStatsMemoria() {
    try {
        const res = await fetch("/api/status");
        if (res.ok) {
            const data = await res.json();
            const sessoesCountEl = document.getElementById("memSessionsCount");
            const eventsCountEl = document.getElementById("memEventsCount");
            if (sessoesCountEl) sessoesCountEl.textContent = data.sessoes || "Ativo";
            if (eventsCountEl) eventsCountEl.textContent = data.memoria || "ADK";
        }
    } catch (e) {
        console.warn("Falha ao ler status da memória:", e);
    }
}

function buscarMemoriaLocal() {
    const input = document.getElementById("memorySearchInput");
    const resultsContainer = document.getElementById("memoryResultsList");
    if (!input || !resultsContainer) return;
    const query = input.value.trim().toLowerCase();
    if (!query) {
        resultsContainer.innerHTML = '<div class="memory-empty-hint">Digite um termo para pesquisar nas conversas.</div>';
        return;
    }

    const matches = state.historyLog.filter(item => item.text && item.text.toLowerCase().includes(query));
    if (matches.length === 0) {
        resultsContainer.innerHTML = `<div class="memory-empty-hint">Nenhuma menção a "${escapeHtml(query)}" encontrada nas conversas recentes.</div>`;
        return;
    }

    resultsContainer.innerHTML = "";
    matches.slice(-8).reverse().forEach(m => {
        const card = document.createElement("div");
        card.className = "memory-item";
        card.innerHTML = `<strong>${m.sender === "user" ? "Você" : "JARVIS"}:</strong> ${escapeHtml(m.text.slice(0, 120))}`;
        resultsContainer.appendChild(card);
    });
}

// Listeners dos novos componentes
if (dom.btnToggleScreenShare) {
    dom.btnToggleScreenShare.addEventListener("click", toggleScreenShare);
}
if (dom.btnApprovePending) {
    dom.btnApprovePending.addEventListener("click", aprovarAcaoPendente);
}
if (dom.btnRejectPending) {
    dom.btnRejectPending.addEventListener("click", rejeitarAcaoPendente);
}
if (dom.btnSearchMemory) {
    dom.btnSearchMemory.addEventListener("click", buscarMemoriaLocal);
}
if (dom.memorySearchInput) {
    dom.memorySearchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") buscarMemoriaLocal();
    });
}

// Listener para troca de abas no modal de configurações
document.querySelectorAll(".settings-nav-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".settings-nav-tabs .tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".settings-panel .tab-content").forEach(tc => tc.classList.remove("active"));
        btn.classList.add("active");
        const tabId = btn.dataset.tab;
        const target = document.getElementById(tabId);
        if (target) {
            target.classList.add("active");
            if (tabId === "tab-memory") carregarStatsMemoria();
        }
    });
});

// Listener para os cards de seleção de modo
document.querySelectorAll(".mode-card").forEach(card => {
    card.addEventListener("click", () => {
        const mode = card.dataset.mode;
        if (mode) applyMode(mode, true);
    });
});

// Inicialização
updateVoiceSelectionUI();
updateProviderUI();
initWaveAnimation();
window.addEventListener("pointerdown", async () => {
    if (state.audioCtx && state.audioCtx.state === "suspended") {
        try { await state.audioCtx.resume(); } catch (e) {}
    }
    if (state.inputAudioCtx && state.inputAudioCtx.state === "suspended") {
        try { await state.inputAudioCtx.resume(); } catch (e) {}
    }
});
connectLiveBackend();
