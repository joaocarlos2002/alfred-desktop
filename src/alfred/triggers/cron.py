"""Expressões cron de 5 campos (minuto hora dia-do-mês mês dia-da-semana).

Suporta ``*``, números, intervalos (``1-5``), listas (``1,15``) e passos (``*/10``, ``8-18/2``).
Dia da semana: 0 ou 7 = domingo, 1 = segunda ... 6 = sábado (convenção do cron). Como no cron,
quando dia-do-mês e dia-da-semana são restritos, basta um deles coincidir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_FIELDS = (("minuto", 0, 59), ("hora", 0, 23), ("dia", 1, 31), ("mês", 1, 12), ("semana", 0, 7))


class CronError(ValueError):
    pass


def _parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"Passo inválido no campo {name}: '{step_text}'")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"Intervalo inválido no campo {name}: '{part}'")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise CronError(f"Valor inválido no campo {name}: '{part}'")
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise CronError(f"Campo {name} fora do intervalo {low}-{high}: '{part}'")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    """Na convenção Python: 0 = segunda ... 6 = domingo."""
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str) -> Cron:
        parts = expression.split()
        if len(parts) != 5:
            raise CronError("Use 5 campos: minuto hora dia mês dia-da-semana (ex.: 0 8 * * 1-5)")
        fields = [_parse_field(p, *spec) for p, spec in zip(parts, _FIELDS, strict=True)]
        cron_days = {7 if d == 0 else d for d in fields[4]}  # 0 e 7 = domingo
        weekdays = frozenset((d - 1) % 7 for d in cron_days)  # cron 1=seg -> python 0
        return cls(
            expression=" ".join(parts),
            minutes=fields[0],
            hours=fields[1],
            days=fields[2],
            months=fields[3],
            weekdays=weekdays,
            any_day=parts[2] == "*",
            any_weekday=parts[4] == "*",
        )

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday_ok = moment.weekday() in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return weekday_ok
        if self.any_weekday:
            return day_ok
        return day_ok or weekday_ok

    def next_after(self, moment: datetime, *, horizon_days: int = 366 * 4) -> datetime | None:
        """Próximo minuto (estritamente depois de ``moment``) que satisfaz a expressão."""
        current = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = current + timedelta(days=horizon_days)
        while current <= limit:
            if current.month not in self.months:
                current = (current.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(
                    day=1
                )
                continue
            if not self._day_ok(current):
                current = current.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if current.hour not in self.hours:
                current = current.replace(minute=0) + timedelta(hours=1)
                continue
            if self.matches(current):
                return current
            current += timedelta(minutes=1)
        return None

    def _day_ok(self, moment: datetime) -> bool:
        day_ok = moment.day in self.days
        weekday_ok = moment.weekday() in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return weekday_ok
        if self.any_weekday:
            return day_ok
        return day_ok or weekday_ok
