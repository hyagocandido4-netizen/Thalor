# RCF-3 — Execution/Broker split

## Motivo

Depois do RCF-2, o ponto mais pesado do runtime continuava no núcleo live:
- `runtime/execution.py`
- `runtime/reconciliation.py`
- construção do adapter do broker ainda misturada com policy de execução

Isso dificultava manutenção, testes e evolução segura da camada live.

## Mudança estrutural

### 1. Superfície do broker isolada

Novo módulo:
- `src/natbin/runtime/broker_surface.py`

Responsabilidades:
- resolver `execution_cfg`, `broker_cfg` e `reconcile_cfg`
- decidir `execution_enabled`
- construir `BrokerScope`
- construir adapter (`FakeBrokerAdapter` / `IQOptionAdapter`)
- centralizar paths do execution DB e signals DB

### 2. Planejamento de intent separado do submit

Novos módulos:
- `src/natbin/runtime/execution_signal.py`
- `src/natbin/runtime/execution_submit.py`

Responsabilidades:
- carregar último trade row
- enriquecer intent com metadata de portfolio/intelligence/retrain
- construir `OrderIntent`
- construir `SubmitOrderRequest`
- persistir tentativa de submit e eventos de transporte

### 3. Artefatos de execução separados

Novo módulo:
- `src/natbin/runtime/execution_artifacts.py`

Responsabilidades:
- leitura utilitária de JSON
- escrita de `orders`, `execution` e `reconcile`

### 4. Orquestração de execução separada

Novo módulo:
- `src/natbin/runtime/execution_process.py`

Responsabilidades:
- gates de kill switch / drain mode / entry deadline / security guard / broker health
- pipeline `process_latest_signal`
- payload `orders`
- payload `reconcile`
- CLI `python -m natbin.runtime.execution`

### 5. Reconciliação separada em core + flow

Novos módulos:
- `src/natbin/runtime/reconciliation_core.py`
- `src/natbin/runtime/reconciliation_flow.py`

Responsabilidades:
- matching por external id / client key / fingerprint
- aplicação de snapshot ao intent
- batch flow de reconciliação por scope
- classificação de órfãos e ambíguos

### 6. Façades compatíveis

Os imports históricos continuam válidos:
- `src/natbin/runtime/execution.py`
- `src/natbin/runtime/reconciliation.py`

Esses módulos agora são finos e apenas reexportam a superfície pública.

## Efeito esperado

- menos acoplamento entre broker e runtime de execução
- menor risco de regressão ao mexer em submit ou reconciliação
- melhor testabilidade das partes críticas
- base mais limpa para o próximo pacote de estabilização de inteligência/ops

## Resultado de refatoração

### Antes
- `runtime/execution.py`: concentrava config, adapter, signal loading, submit, artifacts, reconcile e CLI
- `runtime/reconciliation.py`: concentrava core de matching e batch flow

### Depois
- `runtime/execution.py`: façade compatível
- `runtime/reconciliation.py`: façade compatível
- responsabilidades repartidas por domínio operacional

## Critério de done

- imports históricos preservados
- suíte verde
- `production_doctor` deixa de depender de `runtime.execution` para construir adapter
- reconciliação continua operacional via mesma API pública
