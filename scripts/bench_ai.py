"""Mede a IA local separadamente (Seção 9): latência a frio e aquecida, planejamento com
ferramentas e memória do servidor Ollama.

Uso: ``uv run python scripts/bench_ai.py [--model qwen2.5:7b] [--runs 3]``

Só fala com o Ollama local (127.0.0.1); nenhuma requisição sai do computador. Os pedidos são
genéricos (sem dados do usuário).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

import httpx
import psutil

from alfred.ai.ollama import OllamaProvider
from alfred.automation.tools import register_automation_tools
from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ChatMessage


def ollama_rss_mb() -> float:
    total = 0
    for proc in psutil.process_iter(["name", "memory_info"]):
        if (proc.info["name"] or "").lower().startswith("ollama"):
            total += proc.info["memory_info"].rss
    return round(total / 2**20, 1)


async def unload(model: str) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:11434", timeout=30) as client:
        await client.post("/api/generate", json={"model": model, "keep_alive": 0})


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    provider = OllamaProvider()
    model = args.model or (await provider.list_models())[0]
    tools = ToolRegistry()
    register_automation_tools(
        tools,
        apps=None,  # type: ignore[arg-type]  # só as especificações são usadas
        windows=None,  # type: ignore[arg-type]
        uia=None,  # type: ignore[arg-type]
        keyboard=None,  # type: ignore[arg-type]
        browser=None,  # type: ignore[arg-type]
    )
    specs = tools.specs()
    question = [ChatMessage(role="user", content="Em uma frase: o que é um assistente pessoal?")]
    plan = [ChatMessage(role="user", content="Abra a calculadora.")]

    await unload(model)
    idle_rss = ollama_rss_mb()
    started = time.perf_counter()
    cold = await provider.chat(question, model=model, timeout=300)
    cold_s = time.perf_counter() - started
    loaded_rss = ollama_rss_mb()

    warm: list[float] = []
    rates: list[float] = []
    for _ in range(args.runs):
        t = time.perf_counter()
        reply = await provider.chat(question, model=model, timeout=120)
        elapsed = time.perf_counter() - t
        warm.append(elapsed)
        if reply.usage.output_tokens:
            rates.append(reply.usage.output_tokens / elapsed)

    planning: list[float] = []
    chosen: list[str] = []
    for _ in range(args.runs):
        t = time.perf_counter()
        reply = await provider.chat(plan, model=model, tools=specs, timeout=120)
        planning.append(time.perf_counter() - t)
        chosen.append(",".join(c.name for c in reply.tool_calls) or "(texto)")

    result: dict[str, Any] = {
        "model": model,
        "cold_first_reply_s": round(cold_s, 2),
        "warm_reply_s_median": round(statistics.median(warm), 2),
        "tokens_per_s_median": round(statistics.median(rates), 1) if rates else None,
        "tool_planning_s_median": round(statistics.median(planning), 2),
        "tool_chosen": sorted(set(chosen)),
        "ollama_rss_mb_idle": idle_rss,
        "ollama_rss_mb_loaded": loaded_rss,
        "keep_alive": provider.keep_alive,
        "cold_reply_preview": cold.message.content[:80],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
