# Design system

Identidade própria do ALFRED: discrição, precisão, inteligência e controle. A inspiração é a
sofisticação de um sistema de inteligência pessoal premium — **sem** reproduzir personagens,
logotipos ou elementos protegidos de terceiros. Todos os assets são desenhados em código.

Fonte única: [`src/alfred/ui/theme.py`](../src/alfred/ui/theme.py). Componentes não definem
cores próprias; um teste falha se aparecer uma cor hexadecimal fora do tema.

## Cores

| Token | Valor | Uso |
| ----- | ----- | --- |
| `obsidian` | `#0B0D10` | Fundo principal |
| `graphite` | `#14171C` | Superfícies elevadas (painéis, widget) |
| `slate` | `#1F242B` | Bordas, divisores, campos |
| `steel` | `#2C333C` | Hover e estados ativos |
| `text` / `text_muted` | `#E6E8EB` / `#8B939E` | Texto principal e secundário |
| `accent` / `accent_soft` | `#C9A227` / `#3A3220` | Foco, seleção e identidade |

### Estados do assistente

| Estado | Token | Animação |
| ------ | ----- | -------- |
| Inativo | `steel` | nenhuma (zero custo de CPU) |
| Ouvindo | `listening` `#4FA3FF` | pulso suave |
| Processando | `processing` `#C9A227` | arco girando |
| Executando | `executing` `#7C5CFF` | arco girando |
| Aguardando confirmação | `confirm` `#F2A541` | pulso |
| Concluído | `success` `#3FB97A` | estático, volta a inativo |
| Erro | `error` `#E5484D` | estático, volta a inativo |

## Tipografia

Segoe UI Variable (fallback Segoe UI), corpo 10 pt, títulos 13 pt / 600.

## Espaçamento e raios

Escala de 4 px (`xs` 4, `sm` 8, `md` 12, `lg` 16, `xl` 24). Raios `sm` 6, `md` 10, `lg` 16.

## Movimento

- Animações são **sutis e funcionais**: indicam um estado real, nunca decoram.
- Durações: `fast` 120 ms, `normal` 220 ms, `slow` 400 ms; ciclo de estado 1400 ms.
- Alvo de 60 FPS; nenhum timer roda quando o assistente está inativo.

## Ícone

Monograma geométrico "A" em `accent` sobre círculo `obsidian`/`slate`, gerado em
[`ui/icon.py`](../src/alfred/ui/icon.py) em todos os tamanhos.
