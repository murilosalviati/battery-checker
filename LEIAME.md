# Bateria dos Periféricos

Ícones na bandeja do Windows com a bateria do mouse, do teclado e do fone. Cada um ganha seu próprio ícone com a porcentagem (e uma letra no canto: **M**, **T** ou **F**), e você recebe uma notificação do Windows quando cada um passa de **50%**, **25%** e **10%**.

Cores: verde acima de 50% · amarelo de 30 a 50% · laranja de 15 a 29% · vermelho abaixo de 15% · cinza sem resposta. Quando o aparelho está carregando, aparece um raio amarelo ao lado do número (e a dica do ícone mostra "carregando").

## Aparelhos suportados

| Aparelho | Como é lido |
|---|---|
| Mouse VXE (R1, R1 Pro, R1 SE+, Pro Max…) pelo dongle 2.4G | protocolo Kysona/Compx |
| Teclado AULA pelo receptor Compx 3554:FA09 | protocolo Compx (F87 Pro/F99 Pro confirmados; o F75 provavelmente usa o mesmo receptor) |
| Fone Corsair Virtuoso (RGB Wireless, SE, XT, MAX) e Void pelo dongle | protocolo Corsair/Bragi (vindo do HeadsetControl) |

## Instalação

1. Instale o Python 3.10+ em https://www.python.org/downloads/ e marque **"Add Python to PATH"**.
2. Abra o PowerShell nesta pasta e rode:
   ```powershell
   pip install -r requirements.txt
   ```
3. **Faça o teste primeiro** (com os três aparelhos ligados, mexa no mouse antes):
   ```powershell
   python bateria_perifericos.py --scan
   ```
   Cada aparelho reconhecido mostra uma linha `>> Mouse: 72%`, por exemplo.
4. Se der certo, rode na bandeja:
   ```powershell
   pythonw bateria_perifericos.py
   ```
5. Clique com o botão direito em qualquer ícone e marque **Iniciar com o Windows**.

> Dica: no Windows 11 os ícones novos ficam escondidos na setinha `^`. Vá em **Configurações → Personalização → Barra de tarefas → Outros ícones da bandeja do sistema** e ative os três `python`/`BateriaPerifericos`.

## Menu do ícone

- **Atualizar agora** (ou clique duplo): lê as baterias na hora.
- **Iniciar com o Windows**: liga/desliga o início automático.
- **Abrir configuração**: abre o `config.json`.
- **Sair**.

## Configuração

Fica em `%APPDATA%\BateriaPerifericos\config.json`:

```json
{
  "limites": [50, 25, 10],
  "intervalo_segundos": 300,
  "histerese": 5,
  "nomes": { "3554:f58a": "Mouse VXE R1" }
}
```

- `limites`: porcentagens que geram aviso. Cada uma avisa **uma vez**; volta a valer quando você coloca para carregar ou quando a bateria sobe 5% acima dela.
- `intervalo_segundos`: de quanto em quanto tempo ler (padrão 5 min).
- `nomes`: troca o nome mostrado. A chave `VID:PID` aparece no `--scan`.

Reinicie o app depois de editar.

## Se algum aparelho não aparecer

- **Mouse VXE sem resposta**: ele dorme rápido. Mexa nele e clique em *Atualizar agora*. Se o ATK HUB estiver aberto, feche-o (os dois disputam o dongle).
- **Fone Corsair sem resposta**: o iCUE aberto às vezes "rouba" as respostas. Teste com o iCUE fechado.
- **Teclado AULA não reconhecido**: o receptor do seu F75 pode ser diferente do 3554:FA09. Rode `python bateria_perifericos.py --scan` e me mande a lista que aparece; dá para adicionar o protocolo dele.

## Gerar um .exe (opcional)

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole --name BateriaPerifericos bateria_perifericos.py
```

O executável fica em `dist\BateriaPerifericos.exe`. Para o diagnóstico continue usando a versão `.py` com `--scan` (o .exe não tem console).

---
Protocolos adaptados do [Battery Hub](https://github.com/battery-hub/battery-hub) (licença MIT), que se baseou nos drivers hid-kysona/hid-pulsar do Linux, no HeadsetControl e no AulaF87ProBattery.
