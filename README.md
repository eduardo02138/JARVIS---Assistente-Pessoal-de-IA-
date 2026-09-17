# J.A.R.V.I.S. // Stark Industries Voice Assistant

Assistente pessoal de voz e automação de sistema em tempo real inspirado no **JARVIS (Homem de Ferro)**, desenvolvido com a **Gemini Multimodal Live API** (`gemini-3.8-live`), backend em **FastAPI** para controle do sistema operacional e um **HUD Web Holográfico** futurista com Reator Arc reativo a áudio.

---

## ⚡ Principais Recursos

1. **Conversação Bidirecional em Tempo Real**:
   - Streaming contínuo de voz com latência ultrabaixa via WebSockets e Web Audio API.
   - Detecção de interrupção (*barge-in*): fale a qualquer momento e o JARVIS interrompe a resposta anterior para ouvir sua nova ordem.

2. **Personalidade e Voz do JARVIS**:
   - Tom cortês ("Senhor/Senhora"), elegante, espirituoso e com humor britânico refinado.
   - Voz oficial padrão: **Charon** (grave, sofisticada), com suporte alternativo a *Puck*, *Fenrir*, *Aoede* e *Kore*.

3. **Controle Real do Sistema Operacional (Tools / Function Calling)**:
   - 📊 **Telemetria de Hardware**: Uso de CPU, memória RAM, disco, bateria e tempo de atividade do computador.
   - 🕒 **Horário e Calendário**: Consulta de data, dia da semana e horas.
   - 🚀 **Abertura de Aplicativos**: Abre navegadores (`chrome`, `firefox`), terminal, calculadora, VS Code, Spotify, etc.
   - 🌐 **Pesquisas na Web**: Abre consultas diretamente no navegador do sistema.
   - 🔊 **Controle de Volume**: Ajuste de volume do sistema Linux (aumentar, diminuir, mutar).
   - 📝 **Bloco de Notas**: Gravação e leitura de anotações e lembretes rápidos.

4. **HUD Holográfico Sci-Fi (Frontend)**:
   - Reator Arc central com anéis em rotação e espectrograma de áudio em `<canvas>` sincronizado com as frequências sonoras.
   - Painéis translúcidos em Glassmorphism com monitoramento em tempo real.
   - Suporte a microfone contínuo ou Push-to-Talk pela tecla `<Espaço>`.

---

## 🚀 Como Iniciar

### 1. Definir a Chave da API Gemini
Defina sua chave de API no terminal (ou insira diretamente na engrenagem de configurações da interface):
```bash
export GEMINI_API_KEY="SUA_CHAVE_GEMINI_AQUI"
```

### 2. Iniciar o Assistente
Na pasta do projeto:
```bash
./run_jarvis.sh
```
Ou diretamente:
```bash
.venv/bin/python server.py
```

### 3. Acessar o HUD
Abra seu navegador no endereço:
```
http://localhost:8000
```
Clique em **"INICIAR JARVIS"** e permita o acesso ao microfone.

---

## 🗣️ Exemplos de Comandos para Falar com o JARVIS

- *"Jarvis, qual é o status dos meus sistemas e quanta memória estamos usando?"*
- *"Jarvis, que horas são e qual é a data de hoje?"*
- *"Jarvis, abra o navegador para mim, por favor."*
- *"Jarvis, anote que preciso revisar o relatório de projetos amanhã às 14h."*
- *"Jarvis, quais foram minhas últimas anotações?"*
- *"Jarvis, aumente o volume em 20%."*
- *"Jarvis, como funciona a propulsão do Reator Arc?"*
