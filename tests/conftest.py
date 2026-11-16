import os

# Qt sem janela real nos testes (CI e desenvolvimento).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
