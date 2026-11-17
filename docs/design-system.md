# Design system v2

A identidade do ALFRED transmite elegância, inteligência e discrição: preto profundo, grafite e
carvão em camadas, prata e branco suave no texto e **um único acento**, um dourado discreto.
A profundidade vem de superfícies sobrepostas, bordas sutis e brilho controlado, nunca de
gradientes chamativos. O mascote é a ilustração oficial do Alfred, preservada sem alterações.
Não reproduzimos personagens, logotipos ou elementos protegidos de terceiros.

Fontes únicas:
- [`ui/theme.py`](../src/alfred/ui/theme.py): tokens e stylesheet;
- [`ui/motion.py`](../src/alfred/ui/motion.py): movimento;
- [`ui/components.py`](../src/alfred/ui/components.py): componentes.

O teste `tests/test_design_system.py` falha se algum módulo de UI usar cor, duração, intervalo
ou curva de easing literal, ou se um par texto/fundo ficar abaixo do contraste WCAG AA.

## Cores

### Paleta da identidade

| Token | Valor | Papel |
| ----- | ----- | ----- |
| `void` (= `obsidian`) | `#08090B` | Preto profundo: fundo principal |
| `graphite` | `#151519` | Superfícies elevadas: painéis, widget, cartões |
| `charcoal` (= `slate`) | `#242429` | Bordas, divisores, campos |
| `silver` (= `text_secondary`) | `#BFC1C6` | Texto secundário, ícones |
| `ivory` (= `text`) | `#F2F0EC` | Texto principal |
| `gold` (= `accent`) | `#B69A78` | O único acento: foco, seleção, identidade |

### Papéis derivados

| Token | Valor | Uso |
| ----- | ----- | --- |
| `surface_2` | `#1B1B20` | Cartão dentro de painel (segunda camada) |
| `steel` | `#2F2F36` | Hover e estados ativos |
| `border_soft` | `#1E1E23` | Borda quase invisível entre camadas |
| `text_muted` | `#8C8E95` | Legendas e dicas (5,5:1 sobre grafite) |
| `accent_strong` | `#CDB291` | Hover do acento, texto sobre `accent_soft` |
| `accent_soft` | `#2A241D` | Fundo tingido de dourado (seleção, botão primário) |

### Estados do assistente

Cores contidas, ainda distinguíveis entre si e com contraste mínimo de 3:1 para indicadores gráficos:

| Estado | Token | Valor |
| ------ | ----- | ----- |
| Ouvindo | `listening` | `#9FB4CC` (prata azulado) |
| Processando | `processing` | `#B69A78` (dourado) |
| Executando | `executing` | `#D7C3A0` (champanhe) |
| Aguardando confirmação | `confirm` | `#D9A55B` (âmbar) |
| Concluído | `success` | `#8DB596` (sálvia) |
| Erro | `error` | `#D06A6A` |

## Tipografia

Família Segoe UI Variable, com *Display* para títulos e *Text* para o corpo.

| Papel (`role` no QLabel) | Tamanho | Peso |
| ------------------------ | ------- | ---- |
| `display` | 20 pt | 600 |
| `title` | 14 pt | 600 |
| `subtitle` | 11,5 pt | 500 |
| corpo | 10 pt | 400 |
| `muted` / `secondary` | 10 pt | 400 (`text_muted` / `silver`) |
| `overline` | 8 pt, caixa alta, espaçamento de 1,2 pt | 600, dourado |

## Espaçamento, raios e elevação

- **Espaçamento** (grade de 4 px): `xxs 2` · `xs 4` · `sm 8` · `md 12` · `lg 16` · `xl 24` · `xxl 32` · `xxxl 48`.
- **Raios**: `sm 6` · `md 10` (botões, campos) · `lg 14` (cartões, listas) · `xl 20` · `pill 11`. No QSS,
  um raio maior que metade da altura faz o Qt ignorar os cantos; por isso a pílula tem valor fixo.
- **Elevação**: `low` (blur 12, deslocamento 2), `medium` (24, 6), `high` (40, 12), sombras pretas
  translúcidas. O brilho dourado de foco usa `glow_alpha = 70`.

## Movimento

| Token | ms | Uso |
| ----- | -- | --- |
| `instant` | 90 | Respostas imediatas a toque |
| `fast` | 140 | Toggles, hover, saídas |
| `normal` | 220 | Entradas de componentes, transições de tela |
| `slow` | 360 | Expansões maiores |
| `emphasis` | 480 | Momentos de destaque (com `emphasis`/OutBack) |
| `pulse` | 1400 | Ciclo dos estados vivos (ouvindo, processando) |

Curvas nomeadas: `standard` (OutCubic), `enter` (OutQuint), `exit` (InCubic),
`emphasis` (OutBack), `pulse` (InOutSine), `linear`.

**Movimento reduzido.** `MOTION.reduced` é verdadeiro quando o usuário desliga *Aparência ›
Animações* **ou** quando o Windows está com os efeitos de animação desligados
(`SPI_GETCLIENTAREAANIMATION`). Nesse caso, `MOTION.animate(...)` aplica o valor final na hora:
a informação aparece, só não se move. O widget, o cursor virtual, os toggles e os indicadores
de estado respeitam essa política.

## Componentes

| Componente | Onde | Notas |
| ---------- | ---- | ----- |
| Botões `primary`, padrão, `ghost`, `danger` | QSS (`role`) | Hover e pressionado com transição de cor |
| Campos, selects, listas, abas, menus, tooltip, barra de progresso | QSS | Foco dourado; hover sutil |
| `Card(level=1/2)` | `components.py` | Superfície em camadas com borda sutil |
| `Chip(tone=...)` | `components.py` | `neutral`, `accent`, `success`, `error` |
| `SectionHeader` | `components.py` | Sobrelinha + título + subtítulo |
| `EmptyState` | `components.py` | Orientação + mascote opcional + ação |
| `Toggle` | `components.py` | Interruptor animado, acessível por teclado |
| `StatusDot` | `components.py` | Ponto com pulso suave (estático com movimento reduzido) |
| `Toast` | `components.py` | Entra deslizando, some sozinho |
| `Divider`, `apply_shadow`, `fade_in` | `components.py` | Utilitários |

## Mascote

A ilustração oficial (`docs/images/Mordomo Elegante com Cloche e Tablet.png`) gera os assets
de `src/alfred/ui/assets` por `scripts/build_assets.py`, só com recorte e escala. A presença é
intencional (boas-vindas, espera, voz, avisos importantes e estados vazios), não em todas as telas.
