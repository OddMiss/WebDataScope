#!/usr/bin/env python3
"""Export dataset/datafield summary JSON from local /data files."""

from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

try:
    import msgpack  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: msgpack (install via `pip install msgpack`)") from exc


COLOR_GRAY = "#9e9e9e"
COLOR_RED = "#e53935"
COLOR_GREEN = "#2e7d32"
COLOR_YELLOW = "#f9a825"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export dataset/datafield JSON from info_data.bin")
    parser.add_argument(
        "--data-dir",
        default="/home/runner/work/WebDataScope/WebDataScope/data",
        help="Path to data directory containing dataSetList.json and oth/info_data.bin",
    )
    parser.add_argument(
        "--dataset-output",
        default="dataset.json",
        help="Output JSON path for dataset summary",
    )
    parser.add_argument(
        "--datafield-output",
        default="datafield.json",
        help="Output JSON path for datafield summary",
    )
    parser.add_argument(
        "--universe",
        default="",
        help="Universe used to resolve Star (★★★/☆☆☆). If omitted, Star defaults to ★★★ when any match exists.",
    )
    parser.add_argument(
        "--date-countdown",
        default="",
        help="Optional override for DateCountdown (YYYYMMDD).",
    )
    return parser.parse_args()


def load_info_data(info_path: Path) -> Dict[str, Any]:
    packed = info_path.read_bytes()
    decompressed = zlib.decompress(packed)
    data = msgpack.unpackb(decompressed, raw=False)
    if not isinstance(data, dict):
        raise ValueError("Decoded info_data.bin is not a dictionary")
    return data


def load_dataset_list(dataset_list_path: Path) -> Iterable[str]:
    content = json.loads(dataset_list_path.read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise ValueError("dataSetList.json is not an array")
    return [str(item) for item in content]


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_float(value: Optional[float], digits: int = 6) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def format_int(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def sharpe_color(sharpe_ratio: Optional[float], mean_sharpe: Optional[float]) -> str:
    if sharpe_ratio is None:
        return COLOR_GRAY
    if sharpe_ratio < 0:
        return COLOR_RED
    if mean_sharpe is not None and sharpe_ratio > mean_sharpe:
        return COLOR_GREEN
    return COLOR_YELLOW


def build_star_index(dataset_list: Iterable[str]) -> Dict[Tuple[str, str, str], set[str]]:
    pattern = re.compile(r"^(?P<id>[^_]+)_(?P<region>[^_]+)_(?P<universe>.+)_Delay(?P<delay>\d+)$")
    index: Dict[Tuple[str, str, str], set[str]] = {}
    for entry in dataset_list:
        match = pattern.match(entry)
        if not match:
            continue
        key = (match.group("id"), match.group("region"), match.group("delay"))
        index.setdefault(key, set()).add(match.group("universe"))
    return index


def resolve_star(
    object_id: str,
    region: str,
    delay: str,
    universe: str,
    star_index: Dict[Tuple[str, str, str], set[str]],
) -> str:
    universes = star_index.get((object_id, region, delay), set())
    if not universes:
        return ""
    if not universe:
        return "★★★"
    if universe in universes:
        return "★★★"
    return "☆☆☆"


def resolve_date_countdown(info_data: Dict[str, Any], override: str) -> str:
    if override:
        return override
    dates = []
    for region_data in info_data.values():
        if isinstance(region_data, dict):
            sub_end_time = str(region_data.get("sub_end_time") or "")
            if sub_end_time:
                dates.append(sub_end_time)
    if not dates:
        return ""
    return max(dates).replace("-", "")


def build_entry(
    obj_id: str,
    region: str,
    delay: str,
    isos_item: Dict[str, Any],
    neutral_item: Dict[str, Any],
    mean_sharpe: Optional[float],
    total_count: Optional[int],
    star: str,
) -> Dict[str, Any]:
    basis_sharpe = safe_float(isos_item.get("sharpe_ratio"))
    basis_count = safe_int(isos_item.get("count"))
    basis = {
        "OS/IS Sharpe": format_float(basis_sharpe),
        "Count": format_int(basis_count),
        "Mean OS/IS Sharpe": format_float(mean_sharpe),
        "Total Count": format_int(total_count),
        "Color": sharpe_color(basis_sharpe, mean_sharpe),
    }

    neutralization: Dict[str, Dict[str, str]] = {}
    counts = [
        safe_float((detail or {}).get("count"))
        for detail in neutral_item.values()
        if isinstance(detail, dict)
    ]
    total_neutral_count = sum(value for value in counts if value is not None)

    for neutral_key in sorted(neutral_item.keys()):
        detail = neutral_item.get(neutral_key)
        if not isinstance(detail, dict):
            continue
        count = safe_float(detail.get("count"))
        sharpe_ratio = safe_float(detail.get("sharpe_ratio"))
        percentage = ""
        if count is not None and total_neutral_count > 0:
            percentage = f"{(count / total_neutral_count) * 100:.2f}%"
        neutralization[neutral_key] = {
            "Count": format_int(int(count) if count is not None else None),
            "Percentage": percentage,
            "Sharpe Ratio": format_float(sharpe_ratio),
            "Color": sharpe_color(sharpe_ratio, mean_sharpe),
        }

    return {
        "Basis": basis,
        "Neutralization": neutralization,
        "Star": star,
    }


def build_output(
    info_data: Dict[str, Any],
    target_type: str,
    date_countdown: str,
    star_index: Dict[Tuple[str, str, str], set[str]],
    universe: str,
) -> Dict[str, Any]:
    output: Dict[str, Any] = {"DateCountdown": date_countdown}
    for region_delay, region_data in info_data.items():
        if not isinstance(region_data, dict):
            continue
        if "_" not in region_delay:
            continue
        region, delay = region_delay.split("_", 1)

        isos = region_data.get("isos", {})
        neutralization = region_data.get("neutralization", {})
        if not isinstance(isos, dict) or not isinstance(neutralization, dict):
            continue

        mean_sharpe = safe_float((isos.get("mean") or {}).get("sharpe_ratio"))
        total_count = safe_int(isos.get("total_count"))
        isos_map = isos.get(target_type, {})
        neutral_map = neutralization.get(target_type, {})
        if not isinstance(isos_map, dict) or not isinstance(neutral_map, dict):
            continue

        for obj_id in sorted(isos_map.keys()):
            isos_item = isos_map.get(obj_id)
            if not isinstance(isos_item, dict):
                continue
            neutral_item = neutral_map.get(obj_id, {})
            if not isinstance(neutral_item, dict):
                neutral_item = {}
            star = resolve_star(obj_id, region, delay, universe, star_index) if target_type == "dataset" else ""
            entry_key = f"{obj_id}-{region}-{delay}"
            output[entry_key] = build_entry(
                obj_id=obj_id,
                region=region,
                delay=delay,
                isos_item=isos_item,
                neutral_item=neutral_item,
                mean_sharpe=mean_sharpe,
                total_count=total_count,
                star=star,
            )
    return output


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    info_data = load_info_data(data_dir / "oth" / "info_data.bin")
    dataset_list = load_dataset_list(data_dir / "dataSetList.json")
    star_index = build_star_index(dataset_list)
    date_countdown = resolve_date_countdown(info_data, args.date_countdown)

    dataset_output = build_output(
        info_data=info_data,
        target_type="dataset",
        date_countdown=date_countdown,
        star_index=star_index,
        universe=args.universe,
    )
    datafield_output = build_output(
        info_data=info_data,
        target_type="datafield",
        date_countdown=date_countdown,
        star_index=star_index,
        universe=args.universe,
    )

    Path(args.dataset_output).write_text(
        json.dumps(dataset_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(args.datafield_output).write_text(
        json.dumps(datafield_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
