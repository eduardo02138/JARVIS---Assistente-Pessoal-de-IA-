/**
 * Cliente do agente de voz.
 * Grava PCM 16 kHz do microfone, envia pelo WebSocket e toca o áudio de 24 kHz
 * que volta do Gemini Live, enfileirando os trechos para não haver cortes.
 */

const TAXA_ENTRADA = 16000;
const TAXA_SAIDA = 24000;

function obterOuCriarSessao() {
  let s = localStorage.getItem("jarvis_sessao");
  if (!s) {
    s = "sessao-" + (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().substring(0, 8) : Math.random().toString(36).substring(2, 10));
    try {
      localStorage.setItem("jarvis_sessao", s);
    } catch (_) {}
  }
  return s;
}

const estado = {
  ws: null,
  token: localStorage.getItem("jarvis_token") || "",
  sessao: obterOuCriarSessao(),
  usuario: localStorage.getItem("jarvis_usuario") || "local",
  gravando: false,
  ctxEntrada: null,
  ctxSaida: null,
  stream: null,
  processador: null,
  fila: [],
  tocando: false,
  proximoInicio: 0,
  balaoAtual: null,
};

const el = {
  conectar: document.getElementById("btnConectar"),
  microfone: document.getElementById("btnMicrofone"),
  estado: document.getElementById("estado"),
  conversa: document.getElementById("conversa"),
  form: document.getElementById("formTexto"),
  campo: document.getElementById("campoTexto"),
  ferramentas: document.getElementById("listaFerramentas"),
};

function mostrarEstado(texto, classe = "") {
  el.estado.textContent = texto;
  el.estado.className = `estado ${classe}`;
}

function adicionarBalao(quem, texto) {
  const balao = document.createElement("div");
  balao.className = `balao ${quem}`;
  balao.innerHTML = `<span class="quem">${quem === "agente" ? "Agente" : "Você"}</span><p></p>`;
  balao.querySelector("p").textContent = texto;
  el.conversa.appendChild(balao);
  el.conversa.scrollTop = el.conversa.scrollHeight;
  return balao.querySelector("p");
}

function registrarFerramenta(texto, classe = "") {
  const item = document.createElement("li");
  item.className = classe;
  item.textContent = texto;
  el.ferramentas.prepend(item);
}

// ---------------- AUTORIZAÇÃO DE AÇÃO PENDENTE ----------------
function renderizarBotaoAutorizacao(idConfirmacao, nomeFerramenta) {
  const item = document.createElement("li");
  item.className = "aguardando";
  const rotulo = document.createElement("span");
  rotulo.textContent = `Pendência: ${nomeFerramenta}`;

  const btnAprovar = document.createElement("button");
  btnAprovar.className = "primario";
  btnAprovar.textContent = "Autorizar";
  btnAprovar.addEventListener("click", () => resolverPendencia(idConfirmacao, true, item));

  const btnNegar = document.createElement("button");
  btnNegar.textContent = "Negar";
  btnNegar.addEventListener("click", () => resolverPendencia(idConfirmacao, false, item));

  item.append(rotulo, btnAprovar, btnNegar);
  el.ferramentas.prepend(item);
}

async function resolverPendencia(idConfirmacao, aprovado, item) {
  item.querySelectorAll("button").forEach((botao) => (botao.disabled = true));
  try {
    const resposta = await fetch("/api/confirmar_acao", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Jarvis-Token": estado.token,
      },
      body: JSON.stringify({
        id_confirmacao: idConfirmacao,
        aprovado,
        sessao: estado.sessao,
        usuario: estado.usuario,
      }),
    });
    const dados = await resposta.json().catch(() => ({}));
    if (!resposta.ok) {
      throw new Error(dados.mensagem || `HTTP ${resposta.status}`);
    }
    item.className = aprovado ? "ok" : "negado";
    item.textContent = aprovado ? `Autorizado: ${idConfirmacao}` : `Negado: ${idConfirmacao}`;
    registrarFerramenta(
      dados.mensagem || (aprovado ? "Ação autorizada" : "Ação negada"),
      aprovado ? "ok" : "negado"
    );
  } catch (e) {
    item.className = "negado";
    item.textContent = `Falha ao ${aprovado ? "autorizar" : "negar"}: ${e.message}`;
    item.querySelectorAll("button").forEach((botao) => (botao.disabled = false));
  }
}

// ---------------- REPRODUÇÃO DO ÁUDIO DO AGENTE ----------------
function tocarTrecho(base64) {
  if (!estado.ctxSaida) {
    estado.ctxSaida = new AudioContext({ sampleRate: TAXA_SAIDA });
  }
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const amostras = new Int16Array(bytes.buffer);
  const buffer = estado.ctxSaida.createBuffer(1, amostras.length, TAXA_SAIDA);
  const canal = buffer.getChannelData(0);
  for (let i = 0; i < amostras.length; i++) canal[i] = amostras[i] / 32768;

  const fonte = estado.ctxSaida.createBufferSource();
  fonte.buffer = buffer;
  fonte.connect(estado.ctxSaida.destination);

  // Encadeia os trechos: cada um começa quando o anterior termina
  const agora = estado.ctxSaida.currentTime;
  const inicio = Math.max(agora, estado.proximoInicio);
  fonte.start(inicio);
  estado.proximoInicio = inicio + buffer.duration;
}

function pararAudio() {
  if (estado.ctxSaida) {
    estado.ctxSaida.close();
    estado.ctxSaida = null;
  }
  estado.proximoInicio = 0;
}

// ---------------- CAPTURA DO MICROFONE ----------------
async function ligarMicrofone() {
  estado.stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  estado.ctxEntrada = new AudioContext({ sampleRate: TAXA_ENTRADA });
  const fonte = estado.ctxEntrada.createMediaStreamSource(estado.stream);
  estado.processador = estado.ctxEntrada.createScriptProcessor(2048, 1, 1);

  estado.processador.onaudioprocess = (evento) => {
    if (!estado.gravando || !estado.ws || estado.ws.readyState !== WebSocket.OPEN) return;
    const entrada = evento.inputBuffer.getChannelData(0);
    const pcm = new Int16Array(entrada.length);
    for (let i = 0; i < entrada.length; i++) {
      const amostra = Math.max(-1, Math.min(1, entrada[i]));
      pcm[i] = amostra < 0 ? amostra * 32768 : amostra * 32767;
    }
    const bytes = new Uint8Array(pcm.buffer);
    let binario = "";
    for (let i = 0; i < bytes.length; i++) binario += String.fromCharCode(bytes[i]);
    estado.ws.send(JSON.stringify({ tipo: "audio", dados: btoa(binario) }));
  };

  fonte.connect(estado.processador);
  estado.processador.connect(estado.ctxEntrada.destination);
  estado.gravando = true;
  if (estado.ws && estado.ws.readyState === WebSocket.OPEN) {
    estado.ws.send(JSON.stringify({ tipo: "estado_microfone", mutado: false }));
  }
  el.microfone.textContent = "Microfone ligado";
  el.microfone.classList.add("ativo");
  mostrarEstado("Ouvindo você", "ok");
}

function desligarMicrofone() {
  estado.gravando = false;
  if (estado.ws && estado.ws.readyState === WebSocket.OPEN) {
    estado.ws.send(JSON.stringify({ tipo: "estado_microfone", mutado: true }));
  }
  if (estado.processador) estado.processador.disconnect();
  if (estado.ctxEntrada) estado.ctxEntrada.close();
  if (estado.stream) estado.stream.getTracks().forEach((t) => t.stop());
  estado.processador = estado.ctxEntrada = estado.stream = null;
  el.microfone.textContent = "Microfone";
  el.microfone.classList.remove("ativo");
}

// ---------------- CONEXÃO COM O SERVIDOR ----------------
async function revalidarToken() {
  try {
    const res = await fetch("/api/auth/session");
    if (res.ok) {
      const data = await res.json();
      if (data.token) {
        estado.token = data.token;
        localStorage.setItem("jarvis_token", data.token);
        return data.token;
      }
    }
  } catch (e) {
    console.warn("Não foi possível carregar token de sessão:", e);
  }
  return estado.token;
}

async function conectar(tentativaReconexao = false) {
  mostrarEstado("Autenticando…");
  await revalidarToken();

  const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({
    sessao: estado.sessao,
    usuario: estado.usuario,
  });
  const url = `${protocolo}//${location.host}/ws/live?${params.toString()}`;
  estado.ws = new WebSocket(url);
  mostrarEstado("Conectando…");

  estado.ws.onopen = () => {
    // Handshake autenticado obrigatório com token revalidado
    estado.ws.send(JSON.stringify({
      type: "init",
      token: estado.token
    }));
    mostrarEstado("Conectado — fale ou escreva", "ok");
    el.conectar.textContent = "Sair do modo live";
    el.microfone.disabled = false;
  };

  estado.ws.onmessage = (evento) => {
    const msg = JSON.parse(evento.data);
    switch (msg.tipo) {
      case "audio":
        tocarTrecho(msg.dados);
        break;
      case "texto":
        if (!estado.balaoAtual) estado.balaoAtual = adicionarBalao("agente", "");
        estado.balaoAtual.textContent += msg.texto;
        el.conversa.scrollTop = el.conversa.scrollHeight;
        break;
      case "transcricao_usuario_parcial":
        if (!estado.balaoUsuarioParcial) estado.balaoUsuarioParcial = adicionarBalao("usuario", "");
        estado.balaoUsuarioParcial.textContent = `🎙️ ${msg.texto}`;
        el.conversa.scrollTop = el.conversa.scrollHeight;
        break;
      case "transcricao_usuario":
        if (estado.balaoUsuarioParcial) {
          estado.balaoUsuarioParcial.textContent = msg.texto;
          estado.balaoUsuarioParcial = null;
        } else {
          adicionarBalao("usuario", msg.texto);
        }
        break;
      case "ferramenta":
        registrarFerramenta(`${msg.nome}(${JSON.stringify(msg.args)})`, "executando");
        break;
      case "ferramenta_resultado":
        registrarFerramenta(`${msg.nome} → ${JSON.stringify(msg.resultado).slice(0, 120)}`, "ok");
        if (msg.resultado && msg.resultado.status === "bloqueado_aguardando_confirmacao") {
          renderizarBotaoAutorizacao(msg.resultado.id_confirmacao, msg.nome);
        }
        break;
      case "acao_aprovada":
        registrarFerramenta(`✅ ${msg.mensagem || 'Ação autorizada com sucesso'}`, "ok");
        break;
      case "interrompido":
        pararAudio();
        break;
      case "turno_concluido":
        estado.balaoAtual = null;
        estado.balaoUsuarioParcial = null;
        break;
      case "pronto":
        registrarFerramenta(`sessão de voz pronta — modelo ${msg.modelo}`, "ok");
        break;
      case "erro":
        mostrarEstado(msg.mensagem, "erro");
        break;
    }
  };

  estado.ws.onclose = async (evento) => {
    desligarMicrofone();
    pararAudio();
    estado.ws = null;

    // Se recusado com 1008 (Unauthorized / Stale Token), busca token novo do backend e reconecta
    if (evento && evento.code === 1008 && !tentativaReconexao) {
      mostrarEstado("Token expirado (1008). Revalidando credenciais…", "erro");
      await revalidarToken();
      console.log("Token renovado após fechamento 1008. Reconectando...");
      setTimeout(() => conectar(true), 1200);
      return;
    }

    mostrarEstado("Desconectado");
    el.conectar.textContent = "Entrar no modo live";
    el.microfone.disabled = true;
  };

  estado.ws.onerror = () => mostrarEstado("Falha na conexão", "erro");
}

el.conectar.addEventListener("click", async () => {
  if (estado.ws) {
    estado.ws.close();
  } else {
    await conectar();
  }
});

el.microfone.addEventListener("click", () => {
  if (estado.gravando) desligarMicrofone();
  else ligarMicrofone().catch((e) => mostrarEstado(`Microfone negado: ${e.message}`, "erro"));
});

el.form.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const texto = el.campo.value.trim();
  if (!texto) return;
  el.campo.value = "";
  adicionarBalao("usuario", texto);

  // Com o modo live aberto, o texto entra na mesma sessão de voz;
  // sem ele, usa o endpoint de texto puro.
  if (estado.ws && estado.ws.readyState === WebSocket.OPEN) {
    estado.ws.send(JSON.stringify({ tipo: "texto", texto }));
    return;
  }

  if (!estado.token) {
    await revalidarToken();
  }

  const resposta = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Jarvis-Token": estado.token
    },
    body: JSON.stringify({ texto, sessao: estado.sessao, usuario: estado.usuario }),
  }).then((r) => r.json());
  adicionarBalao("agente", resposta.resposta || resposta.mensagem || "(sem resposta)");
  if (resposta.caminho) {
    registrarFerramenta(`caminho ${resposta.caminho} — ${resposta.motivo_do_roteamento}`);
  }
  (resposta.ferramentas || []).forEach((nome) => registrarFerramenta(nome, "ok"));
});

// Inicialização imediata do token na carga da página
revalidarToken();
