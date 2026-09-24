const MODELO_PADRAO = 'gemini-3.8-live';

/** Descarta o modelo legado gravado no navegador: ele sobrepunha o padrão do .env. */
function modeloSalvado() {
    const salvo = localStorage.getItem('jarvis_model');
    if (!salvo || salvo.startsWith('gemini-2.5') || salvo.includes('exp')) {
        localStorage.setItem('jarvis_model', MODELO_PADRAO);
        return MODELO_PADRAO;
    }
    return salvo;
}

let jarvisSessionToken = "";
async function initSessionToken() {
    jarvisSessionToken = await JarvisComum.obterTokenDeSessao();
    return jarvisSessionToken;
}
const sessionTokenReady = initSessionToken();

/** Envia requisições autenticadas ao backend local (token de sessão do JARVIS). */
async function apiFetch(url, options = {}) {
    if (!jarvisSessionToken) {
        await sessionTokenReady;
    }
    const headers = Object.assign({}, options.headers || {});
    if (jarvisSessionToken) {
        headers['X-Jarvis-Token'] = jarvisSessionToken;
    }
    return fetch(url, Object.assign({}, options, { headers }));
}

/**
 * J.A.R.V.I.S. Client Controller
 * Gerencia Web Audio API (gravação PCM 16kHz e reprodução PCM 24kHz),
 * Canvas Visualizer do Reator Arc e WebSocket bidirecional com a Gemini Live API.
 */

// ---------------- ESTADO DA APLICAÇÃO ----------------
const state = {
    connected: false,
    listening: false,
    speaking: false,
    ws: null,
    voice: localStorage.getItem('jarvis_voice') || 'Charon',
    model: modeloSalvado(),
    isPushToTalkActive: false,
    
    // Áudio
    audioCtx: null,
    inputAudioCtx: null,
    mediaStream: null,
    inputSource: null,
    audioProcessor: null,
    analyser: null,
    micAnalyser: null,
    micDataArray: null,
    
    // Fila de reprodução PCM 24kHz
    audioQueue: [],
    isPlayingAudio: false,
    currentAudioSource: null,
    scheduledEndTime: 0,
    activeAudioSources: new Set(),
    bargeIn: false,
    
    // Visualizer
    animationFrameId: null,
    dataArray: null
};

// ---------------- ELEMENTOS DO DOM ----------------
const dom = {
    liveClock: document.getElementById('liveClock'),
    liveDate: document.getElementById('liveDate'),
    statusDot: document.getElementById('statusDot'),
    statusLabel: document.getElementById('statusLabel'),
    cpuValue: document.getElementById('cpuValue'),
    cpuBar: document.getElementById('cpuBar'),
    cpuCores: document.getElementById('cpuCores'),
    ramValue: document.getElementById('ramValue'),
    ramBar: document.getElementById('ramBar'),
    ramUsed: document.getElementById('ramUsed'),
    ramTotal: document.getElementById('ramTotal'),
    batteryStatus: document.getElementById('batteryStatus'),
    uptimeStatus: document.getElementById('uptimeStatus'),
    toolsLog: document.getElementById('toolsLog'),
    visualizerCanvas: document.getElementById('visualizerCanvas'),
    reactorGlow: document.getElementById('reactorGlow'),
    aiStateText: document.getElementById('aiStateText'),
    btnConnect: document.getElementById('btnConnect'),
    btnConnectLabel: document.getElementById('btnConnectLabel'),
    btnMic: document.getElementById('btnMic'),
    micLabel: document.getElementById('micLabel'),
    dialogueContainer: document.getElementById('dialogueContainer'),
    textForm: document.getElementById('textForm'),
    textInput: document.getElementById('textInput'),
    btnSendText: document.getElementById('btnSendText'),
    interruptedAlert: document.getElementById('interruptedAlert'),
    voiceBadge: document.getElementById('voiceBadge'),
    modelBadge: document.getElementById('modelBadge'),
    ideModeIndicator: document.getElementById('ideModeIndicator'),
    controlModeIndicator: document.getElementById('controlModeIndicator'),
    computerModeIndicator: document.getElementById('computerModeIndicator'),
    
    // Modal
    settingsModal: document.getElementById('settingsModal'),
    btnSettings: document.getElementById('btnSettings'),
    btnCloseSettings: document.getElementById('btnCloseSettings'),
    btnSaveSettings: document.getElementById('btnSaveSettings'),
    inputApiKey: document.getElementById('inputApiKey'),
    selectVoice: document.getElementById('selectVoice'),
    selectModel: document.getElementById('selectModel'),

    // Modal Plugins
    pluginsModal: document.getElementById('pluginsModal'),
    btnPlugins: document.getElementById('btnPlugins'),
    btnClosePlugins: document.getElementById('btnClosePlugins'),
    pluginsList: document.getElementById('pluginsList'),
    tabInstalled: document.getElementById('tabInstalled'),
    tabStore: document.getElementById('tabStore')
};

// ---------------- RELÓGIO & TELEMETRIA EM TEMPO REAL ----------------
function updateClock() {
    const now = new Date();
    dom.liveClock.textContent = now.toLocaleTimeString('pt-BR');
    dom.liveDate.textContent = now.toLocaleDateString('pt-BR');
}
setInterval(updateClock, 1000);
updateClock();

function updateTelemetryUI(data) {
    if (!data) return;
    dom.cpuValue.textContent = data.cpu_percent || '0%';
    dom.cpuBar.style.width = data.cpu_percent || '0%';
    dom.cpuCores.textContent = data.cpu_cores || '-';
    dom.ramValue.textContent = data.ram_percent || '0%';
    dom.ramBar.style.width = data.ram_percent || '0%';
    dom.ramUsed.textContent = data.ram_used_gb || '-';
    dom.ramTotal.textContent = data.ram_total_gb || '-';
    dom.batteryStatus.textContent = data.battery || '-';
    dom.uptimeStatus.textContent = data.uptime || '-';
}

async function fetchSystemTelemetry() {
    if (state.connected && state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'get_status' }));
        return;
    }
    try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        updateTelemetryUI(data);
    } catch (err) {
        // Silencioso em caso de erro momentâneo
    }
}
setInterval(fetchSystemTelemetry, 4000);
fetchSystemTelemetry();

// ---------------- CONTROLE DE STATUS DO JARVIS ----------------
function setJarvisState(status, message) {
    dom.statusDot.className = 'status-dot ' + status;
    dom.statusLabel.textContent = message.toUpperCase();
    dom.aiStateText.textContent = message;

    if (status === 'speaking') {
        dom.reactorGlow.className = 'reactor-core-glow speaking';
        state.speaking = true;
    } else if (status === 'active' && state.listening) {
        dom.reactorGlow.className = 'reactor-core-glow listening';
        state.speaking = false;
    } else {
        dom.reactorGlow.className = 'reactor-core-glow';
        state.speaking = false;
    }
}

// ---------------- FEED DE DIÁLOGO E TOOLS ----------------
function appendDialogue(speaker, text) {
    const bubble = document.createElement('div');
    bubble.className = `dialogue-bubble ${speaker.toLowerCase()}`;
    
    const tag = document.createElement('div');
    tag.className = 'speaker-tag';
    tag.textContent = speaker === 'jarvis' ? 'J.A.R.V.I.S.' : 'SENHOR';
    
    const body = document.createElement('div');
    body.className = 'bubble-body';
    body.textContent = text;
    
    bubble.appendChild(tag);
    bubble.appendChild(body);
    
    dom.dialogueContainer.appendChild(bubble);
    dom.dialogueContainer.scrollTop = dom.dialogueContainer.scrollHeight;
    return body;
}

let activeJarvisBubbleBody = null;
let activeUserBubbleBody = null;

function appendUserInterim(texto) {
    if (!activeUserBubbleBody) {
        activeUserBubbleBody = appendDialogue('user', `🎙️ ${texto}`);
    } else {
        activeUserBubbleBody.textContent = `🎙️ ${texto}`;
        dom.dialogueContainer.scrollTop = dom.dialogueContainer.scrollHeight;
    }
}

function appendJarvisText(chunk) {
    if (!activeJarvisBubbleBody) {
        activeJarvisBubbleBody = appendDialogue('jarvis', chunk);
    } else {
        activeJarvisBubbleBody.textContent += chunk;
        dom.dialogueContainer.scrollTop = dom.dialogueContainer.scrollHeight;
    }
}

function appendThought(texto) {
    // Raciocínio explícito do gemini-3.8-live-extended-thinking: bloco recolhível
    // por padrão para não poluir a conversa principal.
    const thoughtEl = document.createElement('div');
    thoughtEl.className = 'thought-block';

    const header = document.createElement('div');
    header.className = 'thought-header';
    header.textContent = '🧠 Raciocínio interno (Extended Thinking)';

    const body = document.createElement('div');
    body.className = 'thought-body';
    body.textContent = texto;
    body.style.display = 'none';

    header.addEventListener('click', () => {
        body.style.display = body.style.display === 'none' ? 'block' : 'none';
    });

    thoughtEl.appendChild(header);
    thoughtEl.appendChild(body);
    dom.dialogueContainer.appendChild(thoughtEl);
    dom.dialogueContainer.scrollTop = dom.dialogueContainer.scrollHeight;
}

function appendToolLog(name, status, details = '') {
    const item = document.createElement('div');
    item.className = `log-item ${status}`;
    const time = new Date().toLocaleTimeString('pt-BR');
    
    if (status === 'executing') {
        item.textContent = `[${time}] EXECUTANDO: ${name}(${JSON.stringify(details)})`;
    } else if (status === 'success') {
        item.textContent = `[${time}] CONCLUÍDO: ${name} -> OK`;
    } else {
        item.textContent = `[${time}] ${name}: ${details}`;
    }
    
    dom.toolsLog.appendChild(item);
    dom.toolsLog.scrollTop = dom.toolsLog.scrollHeight;
}

// ---------------- REATOR ARC: AUDIO VISUALIZER (CANVAS) ----------------
function initVisualizer() {
    const canvas = dom.visualizerCanvas;
    const ctx = canvas.getContext('2d');
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = 105;

    function render() {
        state.animationFrameId = requestAnimationFrame(render);
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        let avgVolume = 0;
        const barCount = 48;
        const angleStep = (Math.PI * 2) / barCount;

        const activeAnalyser = state.speaking ? state.analyser : (state.micAnalyser || state.analyser);
        const activeData = state.speaking ? state.dataArray : (state.micDataArray || state.dataArray);

        if (activeAnalyser && activeData) {
            activeAnalyser.getByteFrequencyData(activeData);
            let sum = 0;
            for (let i = 0; i < activeData.length; i++) {
                sum += activeData[i];
            }
            avgVolume = sum / activeData.length;
        }

        // Animação dos raios circulares pulsantes
        ctx.save();
        ctx.translate(centerX, centerY);

        for (let i = 0; i < barCount; i++) {
            const angle = i * angleStep;
            const freqVal = activeData ? (activeData[i % activeData.length] || 0) : 0;
            const barHeight = Math.max(6, (freqVal / 255) * 55);

            ctx.save();
            ctx.rotate(angle);

            // Cor baseada no estado (Dourado se JARVIS fala, Ciano se escuta/espera)
            const strokeColor = state.speaking 
                ? `rgba(255, 183, 0, ${0.4 + (barHeight / 55) * 0.6})` 
                : `rgba(0, 240, 255, ${0.4 + (barHeight / 55) * 0.6})`;

            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = 2.5;
            ctx.shadowBlur = 8;
            ctx.shadowColor = state.speaking ? '#ffb700' : '#00f0ff';

            ctx.beginPath();
            ctx.moveTo(0, radius);
            ctx.lineTo(0, radius + barHeight);
            ctx.stroke();

            ctx.restore();
        }

        ctx.restore();
    }

    render();
}

// ---------------- WEB AUDIO: CAPTURA DO MICROFONE (16kHz PCM) ----------------
async function initMicrophone() {
    if (!state.inputAudioCtx) {
        state.inputAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    }

    if (state.inputAudioCtx.state === 'suspended') {
        await state.inputAudioCtx.resume();
    }

    const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
            channelCount: 1,
            sampleRate: 16000,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
        }
    });

    state.mediaStream = stream;
    state.inputSource = state.inputAudioCtx.createMediaStreamSource(stream);

    // Cria micAnalyser no inputAudioCtx para reação visual sem cross-context error
    state.micAnalyser = state.inputAudioCtx.createAnalyser();
    state.micAnalyser.fftSize = 128;
    state.micDataArray = new Uint8Array(state.micAnalyser.frequencyBinCount);
    state.inputSource.connect(state.micAnalyser);


    // ScriptProcessor para coletar chunks PCM 16-bit
    const bufferSize = 2048;
    state.audioProcessor = state.inputAudioCtx.createScriptProcessor(bufferSize, 1, 1);

    state.audioProcessor.onaudioprocess = (e) => {
        if (!state.connected || !state.listening) return;

        // Se o JARVIS estiver ativamente falando nos alto-falantes e não for PTT forçado,
        // não envia áudio para evitar loop acústico/eco do próprio assistente
        if (state.speaking && !state.isPushToTalkActive) {
            return;
        }

        const rawInput = e.inputBuffer.getChannelData(0);
        const currentRate = state.inputAudioCtx.sampleRate || 16000;

        // Garante taxa exata de 16000Hz exigida pela Gemini Live API
        const inputData = JarvisComum.downsampleBuffer(rawInput, currentRate, 16000);

        // Converte Float32Array para Int16Array PCM
        const pcm16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
            let s = Math.max(-1, Math.min(1, inputData[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        // Converte bytes para base64
        const bytes = new Uint8Array(pcm16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        const b64 = btoa(binary);

        // Envia para o backend via WebSocket
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({
                type: 'audio',
                data: b64
            }));
        }

        // Zera buffer de saída para evitar loop de som no navegador
        const outputBuffer = e.outputBuffer.getChannelData(0);
        outputBuffer.fill(0);
    };

    state.inputSource.connect(state.audioProcessor);
    state.audioProcessor.connect(state.inputAudioCtx.destination);
}

function stopMicrophone() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'audio_stream_end' }));
    }
    if (state.mediaStream) {
        state.mediaStream.getTracks().forEach(track => track.stop());
        state.mediaStream = null;
    }
    if (state.audioProcessor) {
        try { state.audioProcessor.disconnect(); } catch(e) {}
        state.audioProcessor = null;
    }
    if (state.inputSource) {
        try { state.inputSource.disconnect(); } catch(e) {}
        state.inputSource = null;
    }
    if (state.micAnalyser) {
        try { state.micAnalyser.disconnect(); } catch(e) {}
        state.micAnalyser = null;
        state.micDataArray = null;
    }
}

// ---------------- WEB AUDIO: REPRODUÇÃO DA RESPOSTA (24kHz PCM) ----------------
function initOutputAudio() {
    if (!state.audioCtx) {
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 128;
        state.dataArray = new Uint8Array(state.analyser.frequencyBinCount);
        state.analyser.connect(state.audioCtx.destination);
    }
    if (state.audioCtx.state === 'suspended') {
        state.audioCtx.resume();
    }
}

function playPCMChunk(base64Data) {
    initOutputAudio();

    try {
        // PCM 16-bit base64 -> Float32 (alinhamento par garantido no módulo comum)
        const float32Array = JarvisComum.pcm16Base64ParaFloat32(base64Data);
        if (!float32Array.length) return;

        // Cria AudioBuffer a 24000Hz
        const audioBuffer = state.audioCtx.createBuffer(1, float32Array.length, 24000);
        audioBuffer.getChannelData(0).set(float32Array);

        const source = state.audioCtx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(state.analyser);

        // Agendamento contínuo com Jitter Buffer anti-engasgo (120ms)
        // Evita que variações normais de latência de pacotes da nuvem causem silêncio e voz travando
        const currentTime = state.audioCtx.currentTime;
        const JITTER_BUFFER = 0.12; 

        if (state.scheduledEndTime < currentTime) {
            state.scheduledEndTime = currentTime + JITTER_BUFFER;
        }

        if (!state.activeAudioSources) {
            state.activeAudioSources = new Set();
        }
        state.activeAudioSources.add(source);

        source.start(state.scheduledEndTime);
        state.scheduledEndTime += audioBuffer.duration;

        setJarvisState('speaking', 'JARVIS FALANDO...');

        source.onended = () => {
            if (state.activeAudioSources) {
                state.activeAudioSources.delete(source);
            }
            // Se o áudio agendado já foi todo tocado, libera o estado de fala
            if (state.audioCtx && state.audioCtx.currentTime >= state.scheduledEndTime - 0.08) {
                state.speaking = false;
                setJarvisState('active', 'ÀS SUAS ORDENS, SENHOR');
            }
        };
    } catch (err) {
        console.warn("Erro ao decodificar chunk de áudio:", err);
    }
}

// Interrupção: para qualquer áudio pendente imediatamente com 0ms de latência
function flushAudioQueue() {
    JarvisComum.interromperReproducao(state);
    activeJarvisBubbleBody = null;
    dom.interruptedAlert.classList.remove('hidden');
    setTimeout(() => dom.interruptedAlert.classList.add('hidden'), 2500);
    setJarvisState('active', 'ESCUTANDO O SENHOR...');
}

// ---------------- WEBSOCKET: CONEXÃO COM O BACKEND ----------------
async function connectWebSocket() {
    if (!jarvisSessionToken) {
        await initSessionToken();  // revalida a cada conexão: o servidor pode ter reiniciado
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    setJarvisState('standby', 'CONECTANDO AOS SISTEMAS...');
    dom.btnConnect.disabled = true;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        // Envia mensagem de inicialização com configurações e token de autenticação
        state.ws.send(JSON.stringify({
            type: 'init',
            voice: state.voice,
            model: state.model,
            token: jarvisSessionToken,
            barge_in: state.bargeIn || false
        }));
    };

    state.ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
            case 'connected':
                state.connected = true;
                dom.btnConnect.disabled = false;
                dom.btnConnect.classList.add('active');
                dom.btnConnectLabel.textContent = 'DESCONECTAR';
                dom.btnMic.disabled = false;
                dom.textInput.disabled = false;
                dom.btnSendText.disabled = false;
                
                if (msg.model && dom.modelBadge) {
                    dom.modelBadge.textContent = msg.model.toUpperCase();
                }
                
                setJarvisState('active', 'SISTEMAS ONLINE - PRONTO');
                appendToolLog('CONEXÃO', 'success', msg.message);
                
                // Ativa microfone automaticamente para fluxo contínuo
                toggleMicrophone(true);
                break;

            case 'warn':
                appendToolLog('AVISO', 'idle', msg.message);
                break;

            case 'ide_mode':
                if (dom.ideModeIndicator) {
                    if (msg.active) {
                        dom.ideModeIndicator.classList.remove('hidden');
                        appendToolLog('MODO IDE', 'executing', 'Canal com Antigravity IDE ativado.');
                    } else {
                        dom.ideModeIndicator.classList.add('hidden');
                        appendToolLog('MODO IDE', 'idle', 'Canal com Antigravity IDE desativado.');
                    }
                }
                break;

            case 'control_mode':
                if (dom.controlModeIndicator) {
                    if (msg.active) {
                        dom.controlModeIndicator.classList.remove('hidden');
                        iniciarVisaoDeTela().then((ok) => {
                            appendToolLog('VISÃO DE TELA', ok ? 'success' : 'error',
                                ok ? 'Compartilhamento de tela ativo: o JARVIS acompanha o que está na tela.'
                                   : 'Sem visão de tela: o compartilhamento foi recusado, o controle fica às cegas.');
                        });
                        const ttl = msg.lease && msg.lease.segundos_restantes ? ` Autoridade válida por ${Math.round(msg.lease.segundos_restantes / 60)} min.` : '';
                        appendToolLog('MODO CONTROLE', 'executing', `Controle físico de mouse, teclado e janelas ativado.${ttl}`);
                    } else {
                        dom.controlModeIndicator.classList.add('hidden');
                        pararVisaoDeTela();
                        appendToolLog('MODO CONTROLE', 'idle', 'Modo Controle desativado.');
                    }
                }
                break;

            case 'computer_mode':
                if (dom.computerModeIndicator) {
                    if (msg.active) {
                        dom.computerModeIndicator.classList.remove('hidden');
                        appendToolLog('MODO COMPUTADOR', 'executing', 'Agente Gemini Computer Use (Playwright) ativado.');
                    } else {
                        dom.computerModeIndicator.classList.add('hidden');
                        appendToolLog('MODO COMPUTADOR', 'idle', 'Modo Computador desativado.');
                    }
                }
                break;

            case 'system_status':
                if (msg.data) updateTelemetryUI(msg.data);
                break;

            case 'audio':
                // Chunks de áudio PCM 24kHz
                if (!activeJarvisBubbleBody) {
                    activeJarvisBubbleBody = appendDialogue('jarvis', '🔊 [Falando resposta por voz...]');
                }
                playPCMChunk(msg.data);
                break;

            case 'text':
                // Transcrição de texto em tempo real (output_transcription do modelo)
                if (activeJarvisBubbleBody && activeJarvisBubbleBody.textContent.startsWith('🔊 [Falando')) {
                    activeJarvisBubbleBody.textContent = msg.text;
                } else {
                    appendJarvisText(msg.text);
                }
                break;

            case 'user_transcription_interim':
                // Parcial em tempo real: atualiza mesma bolha enquanto fala
                if (msg.text) appendUserInterim(msg.text);
                break;

            case 'user_transcription':
                // Transcrição final: consolida bolha parcial ou cria nova
                if (msg.text) {
                    if (activeUserBubbleBody) {
                        activeUserBubbleBody.textContent = msg.text;
                        activeUserBubbleBody = null;
                    } else {
                        appendDialogue('user', msg.text);
                    }
                }
                break;

            case 'interrupted':
                // Usuário falou enquanto o JARVIS falava
                flushAudioQueue();
                appendToolLog('BARGE-IN', 'idle', 'Fala interrompida pelo usuário.');
                break;

            case 'interaction_status':
                if (msg.status === 'IN_PROGRESS') {
                    setJarvisState('active', 'RACIOCINANDO EM SEGUNDO PLANO...');
                } else if (msg.status === 'IDLE') {
                    appendToolLog('RACIOCÍNIO', 'idle', 'Raciocínio explícito concluído.');
                }
                break;

            case 'thought':
                // Raciocínio explícito do Extended Thinking (bloco recolhível)
                appendThought(msg.text);
                break;

            case 'turn_complete':
                activeJarvisBubbleBody = null;
                activeUserBubbleBody = null;
                setTimeout(() => {
                    if (state.connected && !state.speaking) {
                        setJarvisState('active', 'ÀS SUAS ORDENS, SENHOR');
                    }
                }, 500);
                break;

            case 'tool_call':
                setJarvisState('active', `EXECUTANDO: ${msg.name}`);
                appendToolLog(msg.name, 'executing', msg.args);
                break;

            case 'tool_confirmation_request':
                handleToolConfirmationRequest(msg);
                break;

            case 'tool_result':
                appendToolLog(msg.name, 'success', msg.result);
                // Se a tool alterou status, atualiza telemetria imediatamente
                fetchSystemTelemetry();
                break;

            case 'fallback_text':
                // Contingência de voz: o modelo encerrou o turno em silêncio após
                // uma ferramenta e o servidor devolveu a resposta como texto.
                if (msg.text) appendJarvisText(msg.text);
                break;

            case 'policy_verbal_confirmation_approved':
                appendToolLog('CONFIRMAÇÃO', 'success', 'Autorização por voz reconhecida pelo JARVIS.');
                break;

            case 'superseded':
                // Outra janela assumiu a sessão Live única: esta janela ficou órfã.
                alert('JARVIS: ' + (msg.message || 'Esta janela foi substituída por outra conexão do assistente.'));
                disconnectWebSocket();
                break;

            case 'error':
                alert('Aviso do JARVIS: ' + msg.message);
                setJarvisState('error', 'ERRO NOS SISTEMAS');
                appendToolLog('ERRO', 'error', msg.message);
                disconnectWebSocket();
                break;
        }
    };

    state.ws.onclose = () => {
        disconnectWebSocket();
    };

    state.ws.onerror = (err) => {
        console.error('Erro no WebSocket:', err);
        disconnectWebSocket();
    };
}

/**
 * Mostra um painel de autorização para ferramentas de risco (EXTERNAL_WRITE / PRIVILEGED)
 * e devolve a decisão do usuário ao backend. Sem resposta, o backend nega por tempo esgotado.
 */
function handleToolConfirmationRequest(msg) {
    appendToolLog(msg.name, 'executing', `Aguardando autorização (${msg.risk_level}): ${JSON.stringify(msg.args)}`);

    const painel = document.createElement('div');
    painel.className = 'jarvis-confirm-panel';
    painel.innerHTML = `
        <h3>Autorização necessária</h3>
        <p class="risco">${msg.risk_level}</p>
        <p class="ferramenta">${msg.name}</p>
        <pre>${JSON.stringify(msg.args, null, 2)}</pre>
        <p class="motivo">${msg.reason || ''}</p>
        <div class="acoes">
            <button class="aprovar">Autorizar</button>
            <button class="negar">Negar</button>
        </div>
    `;
    document.body.appendChild(painel);

    let respondido = false;
    const responder = (aprovado) => {
        if (respondido) return;
        respondido = true;
        clearTimeout(temporizador);
        painel.remove();
        if (state.ws && state.connected) {
            state.ws.send(JSON.stringify({ type: 'tool_confirmation', id: msg.id, approved: aprovado }));
        }
        appendToolLog(msg.name, aprovado ? 'success' : 'error', aprovado ? 'Autorizado pelo usuário.' : 'Negado pelo usuário.');
    };

    painel.querySelector('.aprovar').addEventListener('click', () => responder(true));
    painel.querySelector('.negar').addEventListener('click', () => responder(false));
    const temporizador = setTimeout(() => responder(false), (msg.timeout_s || 60) * 1000);
}


// ---------------- VISÃO DE TELA EM TEMPO REAL (MODO CONTROLE) ----------------
// Enquanto o Modo Controle estiver ativo, a tela é compartilhada com a sessão Live,
// como no compartilhamento de tela do Gemini Live. Sem isso o assistente opera às cegas.
const visaoDeTela = JarvisComum.criarVisaoDeTela(() => state.ws, { fps: 1, largura: 1024 });

async function iniciarVisaoDeTela() {
    return visaoDeTela.iniciar();
}

function pararVisaoDeTela() {
    visaoDeTela.parar();
}

function disconnectWebSocket() {
    state.connected = false;
    if (state.ws) {
        try { state.ws.close(); } catch(e) {}
        state.ws = null;
    }

    toggleMicrophone(false);
    dom.btnConnect.disabled = false;
    dom.btnConnect.classList.remove('active');
    dom.btnConnectLabel.textContent = 'INICIAR JARVIS';
    dom.btnMic.disabled = true;
    dom.textInput.disabled = false;
    dom.btnSendText.disabled = false;

    setJarvisState('standby', 'SISTEMA EM ESPERA');
}

// ---------------- CONTROLE DO MICROFONE ----------------
async function toggleMicrophone(forceState) {
    const shouldEnable = forceState !== undefined ? forceState : !state.listening;

    if (shouldEnable) {
        try {
            await initMicrophone();
            state.listening = true;
            dom.btnMic.classList.add('active');
            dom.micLabel.textContent = 'ESCUTANDO';
            setJarvisState('active', 'ESCUTANDO O SENHOR...');
        } catch (err) {
            alert('Não foi possível acessar o microfone: ' + err.message);
            state.listening = false;
        }
    } else {
        stopMicrophone();
        state.listening = false;
        dom.btnMic.classList.remove('active');
        dom.micLabel.textContent = 'MICROFONE';
        if (state.connected) {
            setJarvisState('active', 'MICROFONE SILENCIADO');
        }
    }
}

// ---------------- ENVIAR MENSAGEM MANUAL OU COMANDOS RÁPIDOS ----------------
function sendTextMessage(text) {
    const cleanText = text.trim();
    if (!cleanText) return;

    if (!state.connected || !state.ws || state.ws.readyState !== WebSocket.OPEN) {
        appendDialogue('user', cleanText);
        setJarvisState('standby', 'INICIANDO SISTEMAS PARA RESPONDER...');
        connectWebSocket();
        
        let attempts = 0;
        const checkConn = setInterval(() => {
            attempts++;
            if (state.connected && state.ws && state.ws.readyState === WebSocket.OPEN) {
                clearInterval(checkConn);
                state.ws.send(JSON.stringify({
                    type: 'text',
                    text: cleanText
                }));
                activeJarvisBubbleBody = null;
                setJarvisState('active', 'PROCESSANDO INSTRUÇÃO...');
            } else if (attempts > 30) {
                clearInterval(checkConn);
                setJarvisState('error', 'FALHA DE CONEXÃO');
            }
        }, 300);
        return;
    }

    appendDialogue('user', cleanText);
    state.ws.send(JSON.stringify({
        type: 'text',
        text: cleanText
    }));

    activeJarvisBubbleBody = null;
    setJarvisState('active', 'PROCESSANDO INSTRUÇÃO...');
}

// ---------------- EVENT LISTENERS ----------------
dom.btnConnect.addEventListener('click', () => {
    if (state.connected) {
        disconnectWebSocket();
    } else {
        connectWebSocket();
    }
});

dom.btnMic.addEventListener('click', () => {
    toggleMicrophone();
});

// PUSH-TO-TALK com tecla ESPAÇO
let spaceKeyDown = false;
window.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !spaceKeyDown && document.activeElement !== dom.textInput) {
        spaceKeyDown = true;
        state.isPushToTalkActive = true;
        if (state.connected) {
            if (!state.listening) toggleMicrophone(true);
            setJarvisState('active', 'ESCUTANDO O SENHOR...');
        }
    }
});

window.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && spaceKeyDown) {
        spaceKeyDown = false;
        state.isPushToTalkActive = false;
    }
});

// Desbloqueia AudioContext na primeira interação do usuário
window.addEventListener('click', () => {
    initOutputAudio();
}, { once: true });

// Envio de formulário de texto
dom.textForm.addEventListener('submit', (e) => {
    e.preventDefault();
    initOutputAudio();
    const text = dom.textInput.value.trim();
    if (text) {
        sendTextMessage(text);
        dom.textInput.value = '';
    }
});

// Chips de sugestão rápida
document.querySelectorAll('.chip-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const prompt = btn.getAttribute('data-prompt');
        if (!state.connected) {
            connectWebSocket();
            setTimeout(() => sendTextMessage(prompt), 1500);
        } else {
            sendTextMessage(prompt);
        }
    });
});

// Modal de Configurações
dom.btnSettings.addEventListener('click', () => {
    dom.selectVoice.value = state.voice;
    if (dom.selectModel) dom.selectModel.value = state.model;
    dom.settingsModal.classList.remove('hidden');
});

dom.btnCloseSettings.addEventListener('click', () => {
    dom.settingsModal.classList.add('hidden');
});

dom.btnSaveSettings.addEventListener('click', () => {
    state.voice = dom.selectVoice.value;
    if (dom.selectModel) state.model = dom.selectModel.value;
    
    localStorage.setItem('jarvis_voice', state.voice);
    localStorage.setItem('jarvis_model', state.model);
    
    dom.voiceBadge.textContent = `${state.voice} (${dom.selectVoice.options[dom.selectVoice.selectedIndex].text.split(' ')[1] || ''})`;
    if (dom.modelBadge) dom.modelBadge.textContent = state.model.toUpperCase();
    dom.settingsModal.classList.add('hidden');
    
    if (state.connected) {
        alert(`Configurações salvas! Reconectando com modelo ${state.model} e voz ${state.voice}...`);
        disconnectWebSocket();
        connectWebSocket();
    }
});

// Inicializa visualizador do Canvas
initVisualizer();
dom.voiceBadge.textContent = `${state.voice}`;
if (dom.modelBadge) dom.modelBadge.textContent = state.model.toUpperCase();

// ---------------- ECOSSISTEMA DE PLUG-INS & LOJA (N.E.K.O SDK) ----------------
let currentPluginTab = 'installed';

async function renderPluginsUI() {
    if (!dom.pluginsList) return;
    dom.pluginsList.innerHTML = '<div style="color: var(--hud-cyan); padding: 20px; text-align: center; font-family: var(--font-display);">CARREGANDO PLUG-INS DO SISTEMA...</div>';
    try {
        const url = currentPluginTab === 'installed' ? '/api/plugins' : '/api/plugins/store';
        const res = await fetch(url);
        if (!res.ok) throw new Error('Falha ao obter lista de plugins');
        const plugins = await res.json();
        
        dom.pluginsList.innerHTML = '';
        if (plugins.length === 0) {
            dom.pluginsList.innerHTML = '<div style="color: var(--hud-text-dim); padding: 20px;">Nenhum plug-in encontrado nesta seção.</div>';
            return;
        }

        plugins.forEach(p => {
            const card = document.createElement('div');
            card.className = 'plugin-card';
            
            const isInstalled = p.installed;
            const isEnabled = p.enabled;
            
            let actionBtnHtml = '';
            if (!isInstalled) {
                actionBtnHtml = `<button class="plugin-action-btn btn-install" onclick="installPlugin('${p.id}')">INSTALAR</button>`;
            } else if (isEnabled) {
                actionBtnHtml = `<button class="plugin-action-btn btn-active" onclick="togglePlugin('${p.id}', false)">ATIVO</button>`;
            } else {
                actionBtnHtml = `<button class="plugin-action-btn btn-inactive" onclick="togglePlugin('${p.id}', true)">DESATIVADO</button>`;
            }

            card.innerHTML = `
                <div class="plugin-card-header">
                    <div class="plugin-info">
                        <div class="plugin-icon">${p.icon || '🔌'}</div>
                        <div>
                            <div class="plugin-meta-title">${p.name}</div>
                            <span class="plugin-category-badge cat-${p.category}">${p.category}</span>
                            ${p.simulated ? '<span class="plugin-category-badge">simulado</span>' : ''}
                        </div>
                    </div>
                </div>
                <div class="plugin-desc">${p.description || ''}</div>
                <div class="plugin-card-footer">
                    <span class="plugin-author">${p.author || 'Stark Industries'} // v${p.version || '1.0'}</span>
                    ${actionBtnHtml}
                </div>
            `;
            dom.pluginsList.appendChild(card);
        });
    } catch (e) {
        dom.pluginsList.innerHTML = `<div style="color: var(--hud-red); padding: 20px;">Erro ao carregar plug-ins: ${e.message}</div>`;
    }
}

window.togglePlugin = async function(pluginId, targetState) {
    try {
        const res = await apiFetch('/api/plugins/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId, enabled: targetState })
        });
        const data = await res.json();
        if (data.sucesso) {
            appendToolLog('PLUG-IN', 'success', data.mensagem);
            renderPluginsUI();
        }
    } catch (e) {
        alert('Erro ao alterar status do plug-in: ' + e.message);
    }
};

window.installPlugin = async function(pluginId) {
    try {
        const res = await apiFetch('/api/plugins/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId })
        });
        const data = await res.json();
        if (data.sucesso) {
            appendToolLog('PLUG-IN', 'success', data.mensagem);
            renderPluginsUI();
        } else {
            appendToolLog('PLUG-IN', 'error', data.mensagem || 'Instalação recusada.');
        }
    } catch (e) {
        alert('Erro ao instalar plug-in: ' + e.message);
    }
};

if (dom.btnPlugins) {
    dom.btnPlugins.addEventListener('click', () => {
        dom.pluginsModal.classList.remove('hidden');
        renderPluginsUI();
    });
}

if (dom.btnClosePlugins) {
    dom.btnClosePlugins.addEventListener('click', () => {
        dom.pluginsModal.classList.add('hidden');
    });
}

if (dom.tabInstalled) {
    dom.tabInstalled.addEventListener('click', () => {
        currentPluginTab = 'installed';
        dom.tabInstalled.classList.add('active');
        dom.tabStore.classList.remove('active');
        renderPluginsUI();
    });
}

if (dom.tabStore) {
    dom.tabStore.addEventListener('click', () => {
        currentPluginTab = 'store';
        dom.tabStore.classList.add('active');
        dom.tabInstalled.classList.remove('active');
        renderPluginsUI();
    });
}
