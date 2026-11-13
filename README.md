# ParkSIM — Sistema de Estacionamento RFID

## Integrantes

- Alex Rodrigues Gonçalves — RA 1136919
- Gabriel Grasik da Rosa — RA 1136052
- Henrique Zonta Tramontina — RA 1139226
- João Parizotto Benedetti — RA 1136044
- Tomás Confortin Tonial — RA 1139224

## Sobre o projeto

O ParkSIM é um MVP acadêmico para controle de estacionamento por cartões de proximidade RFID. A aplicação registra entradas e saídas, calcula o valor da permanência, permite que um operador confirme o pagamento e mantém o histórico das movimentações.

A solução utiliza FastAPI, SQLite e RabbitMQ. Um serviço separado (Worker) consome os eventos publicados pela API e grava esses registros no banco de dados. A integração física é feita por uma ponte executada em Raspberry Pi, responsável pela leitura RFID e pela sinalização com LEDs e buzzer.

## Tecnologias

- **Python:** linguagem utilizada nos serviços e na integração com o hardware.
- **FastAPI:** API REST e regras de negócio.
- **SQLite:** armazenamento de sessões, tarifas, pagamentos e eventos.
- **RabbitMQ:** fila de mensagens para os eventos gerados pela aplicação.
- **Worker:** consumidor das mensagens da fila.
- **HTML, CSS e JavaScript:** painel web.
- **Docker e Docker Compose:** execução dos serviços em containers.
- **Raspberry Pi, MFRC522 e RFID:** leitura dos cartões e sinalização física.

## Como funciona

1. **Entrada:** a primeira leitura de um UID RFID sem sessão ativa registra a entrada e inicia a contagem do tempo.
2. **Solicitação de saída:** uma nova leitura do mesmo cartão calcula o valor devido e altera a sessão para `PAYMENT_PENDING`.
3. **Pagamento:** o operador confirma o recebimento pelo painel web. A sessão passa para `AUTHORIZED` e o pagamento é registrado.
4. **Encerramento:** depois da confirmação, uma nova leitura do mesmo cartão encerra a sessão e registra o horário de saída.

O cartão não precisa ser cadastrado previamente: o UID recebido pelo leitor é utilizado para identificar a sessão. O painel permite acompanhar os veículos ativos, consultar o histórico, visualizar os valores e alterar as tarifas quando não há sessões em andamento.

### Cálculo da tarifa

Cada sessão guarda os valores de tarifa vigentes no momento da entrada. O total é calculado da seguinte forma:

`valor total = valor fixo + (quantidade de intervalos completos × valor variável)`

O intervalo é configurado em segundos. Por padrão, o valor fixo é R$ 5,00, o adicional é R$ 0,50 e o intervalo é de 60 segundos. O sistema considera apenas os intervalos completos.

### Sensores de vaga

A versão atual não utiliza sensores de presença para controlar a ocupação ou encerrar sessões. A liberação lógica ocorre pela nova leitura do cartão após a confirmação do pagamento. Os sensores mencionados em versões anteriores da documentação não fazem parte da implementação atual.

## Arquitetura

- **API (FastAPI):** recebe as leituras RFID, aplica as regras de negócio, consulta e atualiza o SQLite e publica eventos no RabbitMQ.
- **SQLite:** mantém as tarifas, sessões, pagamentos e o histórico de eventos.
- **RabbitMQ:** recebe os eventos na fila durável `parksim.events`.
- **Worker:** consome os eventos da fila e grava seus dados na tabela `events` do SQLite.
- **Painel web:** apresenta o estado do estacionamento, os valores, as tarifas e o histórico.
- **Serviço de hardware:** lê os cartões pelo MFRC522 e controla os LEDs e o buzzer conectados ao Raspberry Pi.

O banco SQLite é compartilhado entre API e Worker por meio do volume Docker `parksim_data`. O RabbitMQ utiliza o volume `rabbit_data` para persistir seus dados.

## Executar com Docker

### Requisitos

- Docker instalado.
- Docker Compose v2.
- Para utilizar o leitor físico: Raspberry Pi 4 com SPI habilitado, leitor MFRC522 e componentes conectados aos pinos configurados.

### Preparar as configurações

Na pasta raiz do projeto, crie o arquivo `.env` a partir do exemplo:

```bash
cp .env.example .env
```

As configurações iniciais do RabbitMQ são:

| Configuração | Valor |
|---|---|
| Usuário | `parksim_app` |
| Senha | `ParkSim2026!` |
| Virtual host | `parksim` |

Essas credenciais são destinadas ao ambiente local de demonstração do projeto. Altere-as no arquivo `.env` antes de disponibilizar o serviço em uma rede compartilhada ou em produção. O Docker Compose utiliza os mesmos valores para configurar o RabbitMQ, a API e o Worker.

### Executar sem o hardware

Em computadores sem Raspberry Pi, inicie somente os serviços da aplicação:

```bash
docker compose up --build -d api worker rabbitmq
```

### Executar no Raspberry Pi

Com os componentes conectados e a interface SPI habilitada, inicie todos os serviços:

```bash
docker compose up --build -d
```

O serviço `hardware` acessa os dispositivos GPIO e SPI do Raspberry Pi. Por isso, ele não deve ser iniciado em computadores comuns.

### Acessos

- Painel: http://localhost:8000
- Documentação interativa da API: http://localhost:8000/docs
- Gerenciamento do RabbitMQ: http://localhost:15672

No gerenciamento do RabbitMQ, utilize as credenciais definidas no `.env` (ou os valores iniciais informados acima).

### Encerrar

```bash
docker compose down
```

Os dados permanecem nos volumes. Para apagar também o banco SQLite e os dados persistidos do RabbitMQ:

```bash
docker compose down -v
```

Atenção: esse último comando remove os volumes e apaga os dados armazenados.

Se o RabbitMQ já tiver sido iniciado anteriormente com outro usuário ou senha, alterar o `.env` não modifica automaticamente as credenciais gravadas no volume existente. A recriação do volume do RabbitMQ exige cuidado para não apagar dados que devam ser preservados.

## Testar o fluxo

Com os serviços iniciados, é possível testar a API sem o leitor físico. Os exemplos abaixo utilizam `curl`.

1. Registre a entrada de um cartão:

```bash
curl -X POST http://localhost:8000/api/card/read \
  -H "Content-Type: application/json" \
  -d '{"uid":"A1B2C3D4"}'
```

2. Envie novamente o mesmo UID para solicitar a saída e calcular a cobrança:

```bash
curl -X POST http://localhost:8000/api/card/read \
  -H "Content-Type: application/json" \
  -d '{"uid":"A1B2C3D4"}'
```

3. Consulte o painel em http://localhost:8000 e confirme o pagamento na sessão correspondente.

4. Envie o mesmo UID uma terceira vez para encerrar a sessão:

```bash
curl -X POST http://localhost:8000/api/card/read \
  -H "Content-Type: application/json" \
  -d '{"uid":"A1B2C3D4"}'
```

Também é possível consultar o estado e o histórico pelos endpoints da API.

## Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/health` | Verifica a disponibilidade da API. |
| `GET` | `/api/state` | Retorna o estado atual, tarifas, sessões ativas e arrecadação. |
| `GET` | `/api/history` | Consulta o histórico de sessões. |
| `POST` | `/api/card/read` | Registra a leitura de um cartão RFID. |
| `POST` | `/api/sessions/{session_id}/pay` | Confirma o pagamento de uma sessão. |
| `PUT` | `/api/settings` | Atualiza as tarifas. |
| `GET` | `/api/events` | Consulta os eventos registrados pelo Worker. |

A documentação interativa em `/docs` apresenta os parâmetros e os formatos esperados por cada endpoint.

## Estrutura do projeto

```text
ParkSimEstacionamento/
├── app/
│   └── main.py
├── hardware/
│   ├── Dockerfile
│   ├── README.md
│   ├── requirements.txt
│   └── rfid_gpio_bridge.py
├── static/
│   └── index.html
├── data/
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── worker.py
└── README.md
```

## Considerações

O ParkSIM demonstra a integração entre uma API, banco de dados, mensageria, interface web e hardware RFID. Por ser um MVP acadêmico, a confirmação do pagamento é realizada manualmente pelo operador e a liberação da sessão ocorre por uma nova leitura do cartão. O controle por sensores de presença e outras formas de pagamento não estão implementados nesta versão.
