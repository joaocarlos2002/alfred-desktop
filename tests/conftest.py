import gc
import os
from collections.abc import Iterator

import pytest

# Qt sem janela real nos testes (CI e desenvolvimento).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def collect_garbage_on_main_thread() -> Iterator[None]:
    """Coleta ciclos ao fim de cada teste, na thread principal.

    O coletor cíclico do Python pode disparar em qualquer thread (ex.: thread do banco). Se um
    widget Qt preso num ciclo de referências for destruído fora da thread da GUI, o processo
    cai. Coletar aqui garante que widgets de teste sejam destruídos na thread certa.
    """
    yield
    gc.collect()
