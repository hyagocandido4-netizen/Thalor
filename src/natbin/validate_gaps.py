from datetime import datetime
from zoneinfo import ZoneInfo

from natbin.settings import load_settings
from natbin.db import open_db


def classify_gap(dt_a: datetime, dt_b: datetime, delta_s: int, step: int) -> str:
    """
    Classifica gaps. Heurística prática para Forex:
    - small_unexpected: buracos pequenos (até 30 min) -> provavelmente falha real
    - weekend_expected: fechamento de fim de semana (delta >= ~24h, envolvendo sex->dom/seg)
    - large_expected: feriado/manutenção/fechamentos especiais (delta grande)
    - medium_suspicious: buracos médios em dia útil (30m–6h) -> olhar
    """
    # buracos pequenos: quase sempre erro
    if delta_s <= 6 * step:  # até 30 min em 5m
        return "small_unexpected"

    wd_a = dt_a.weekday()  # 0=Mon ... 4=Fri ... 6=Sun
    wd_b = dt_b.weekday()

    # padrão de fim de semana: sexta para domingo/segunda e grande
    if wd_a == 4 and wd_b in (6, 0) and delta_s >= 24 * 3600:
        return "weekend_expected"

    # se o gap "atravessa" sábado/domingo, normalmente é esperado
    if wd_a in (5, 6) or wd_b in (5, 6):
        return "weekend_expected"

    # gaps médios em dia útil: podem ser manutenção/instabilidade
    if 6 * step < delta_s <= 6 * 3600:
        return "medium_suspicious"

    # gaps grandes em dia útil: feriado/fechamento especial/manutenção prolongada
    if delta_s > 6 * 3600:
        return "large_expected"

    return "unknown"


def main():
    s = load_settings()
    tz = ZoneInfo(s.data.timezone)

    con = open_db(s.data.db_path)
    cur = con.execute(
        "SELECT ts FROM candles WHERE asset=? AND interval_sec=? ORDER BY ts ASC",
        (s.data.asset, s.data.interval_sec),
    )
    tss = [r[0] for r in cur.fetchall()]

    if len(tss) < 3:
        print("Poucos candles para validar.")
        return

    step = int(s.data.interval_sec)
    counts = {
        "small_unexpected": 0,
        "medium_suspicious": 0,
        "weekend_expected": 0,
        "large_expected": 0,
        "unknown": 0,
    }

    print_limit = 30
    printed = 0

    for a, b in zip(tss, tss[1:]):
        delta = b - a
        if delta == step:
            continue

        dt_a = datetime.fromtimestamp(a, tz=tz)
        dt_b = datetime.fromtimestamp(b, tz=tz)
        tag = classify_gap(dt_a, dt_b, delta, step)
        counts[tag] = counts.get(tag, 0) + 1

        # só imprime os suspeitos/inesperados por padrão
        if tag in ("small_unexpected", "medium_suspicious", "unknown") and printed < print_limit:
            printed += 1
            print(f"{tag.upper()}: {dt_a.isoformat()} -> {dt_b.isoformat()} (delta={delta}s)")

    print("\nResumo:")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    total_gaps = sum(counts.values())
    print(f"\nTotal candles: {len(tss)} | gaps totais: {total_gaps}")
    con.close()


if __name__ == "__main__":
    main()