from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CorrelationDescriptor:
    asset: str
    symbol: str
    explicit_cluster_key: str | None
    correlation_group: str
    source: str
    base_symbol: str | None = None
    quote_symbol: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_asset_symbol(asset: str) -> str:
    raw = str(asset or '').strip().upper()
    if not raw:
        return ''
    for sep in ('-', '/', ':', ' '):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
            break
    return ''.join(ch for ch in raw if ch.isalnum())


def resolve_correlation_descriptor(asset: str, cluster_key: str | None = None) -> CorrelationDescriptor:
    symbol = normalize_asset_symbol(asset)
    explicit = str(cluster_key or '').strip()
    if explicit and explicit.lower() != 'default':
        return CorrelationDescriptor(
            asset=str(asset),
            symbol=symbol,
            explicit_cluster_key=explicit,
            correlation_group=explicit,
            source='explicit_cluster_key',
        )

    if len(symbol) == 6 and symbol.isalpha():
        base = symbol[:3]
        quote = symbol[3:]
        return CorrelationDescriptor(
            asset=str(asset),
            symbol=symbol,
            explicit_cluster_key=None,
            correlation_group=f'pair_quote:{quote}',
            source='auto_quote_bucket',
            base_symbol=base,
            quote_symbol=quote,
        )

    if symbol:
        prefix = ''.join(ch for ch in symbol if ch.isalpha())[:4] or symbol[:4]
        return CorrelationDescriptor(
            asset=str(asset),
            symbol=symbol,
            explicit_cluster_key=None,
            correlation_group=f'asset_family:{prefix}',
            source='auto_asset_family',
        )

    return CorrelationDescriptor(
        asset=str(asset),
        symbol=symbol,
        explicit_cluster_key=None,
        correlation_group='default',
        source='fallback_default',
    )


def resolve_correlation_group(asset: str, cluster_key: str | None = None) -> str:
    return resolve_correlation_descriptor(asset=asset, cluster_key=cluster_key).correlation_group


__all__ = [
    'CorrelationDescriptor',
    'normalize_asset_symbol',
    'resolve_correlation_descriptor',
    'resolve_correlation_group',
]
