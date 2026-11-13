# Integração com Raspberry Pi

O serviço `hardware`, definido no `docker-compose.yml`, executa `rfid_gpio_bridge.py`. Ele faz a leitura dos cartões pelo módulo MFRC522, envia o UID à API e controla os LEDs e o buzzer conforme o resultado da operação.

## Componentes e pinagem

A configuração utiliza numeração física `BOARD`:

| Componente | Pino físico |
|---|---:|
| MFRC522 SDA / SS | CE0 (SPI0) |
| MFRC522 SCK | SCK (SPI0) |
| MFRC522 MOSI | MOSI (SPI0) |
| MFRC522 MISO | MISO (SPI0) |
| MFRC522 RST | Conforme ligação do módulo |
| LED verde | 31 |
| LED vermelho | 29 |
| Buzzer | 33 |

Os pinos dos LEDs e do buzzer podem ser alterados no arquivo `.env` pelas variáveis `GREEN_LED_PIN`, `RED_LED_PIN` e `BUZZER_PIN`. Para desativar o buzzer, configure `ENABLE_BUZZER=0`. O intervalo para evitar leituras repetidas pode ser ajustado por `RFID_DEBOUNCE_SECONDS`.

Esta versão não lê sensores de presença. As variáveis de configuração de sensores não são utilizadas pelo código atual.

## Preparação

1. Habilite a interface SPI no Raspberry Pi com `sudo raspi-config`.
2. Conecte o MFRC522 à interface SPI0.
3. Utilize alimentação de 3,3 V no MFRC522. Não aplique sinais de 5 V aos GPIOs.
4. Conecte os LEDs com resistores adequados e confira a pinagem antes de energizar o circuito.
5. Inicie os serviços com `docker compose up --build -d`.

O container utiliza acesso privilegiado e os dispositivos `/dev/gpiomem` e `/dev/spidev0.0`. Use essa configuração somente em um Raspberry Pi dedicado aos testes do projeto.

A imagem e as dependências de GPIO foram preparadas para Raspberry Pi 4. Outros modelos podem exigir ajustes de biblioteca ou acesso ao hardware.
