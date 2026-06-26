import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace generic speaker labels in a transcript with human-friendly aliases."
    )
    parser.add_argument("input_path", help="Path to source transcript text file")
    parser.add_argument("output_path", help="Path to output text file")
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Alias mapping in LABEL=VALUE form, e.g. PERSON_01=Кирилл Лазарев",
    )
    parser.add_argument(
        "--header-note",
        action="append",
        default=[],
        help="Extra note line to write into the header",
    )
    return parser


def parse_aliases(raw_aliases: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for raw in raw_aliases:
        if "=" not in raw:
            raise ValueError(f"Invalid alias mapping: {raw}")
        label, value = raw.split("=", 1)
        label = label.strip()
        value = value.strip()
        if not label or not value:
            raise ValueError(f"Invalid alias mapping: {raw}")
        aliases[label] = value
    return aliases


def apply_aliases(text: str, aliases: dict[str, str]) -> str:
    result = text
    for label, value in aliases.items():
        result = result.replace(f"{label}:", f"{value}:")
        result = result.replace(f"{label} =", f"{value} =")
        result = result.replace(f"{label} ->", f"{value} ->")
    return result


def build_header(aliases: dict[str, str], notes: list[str]) -> str:
    lines = [
        "Именная версия расшифровки для ручной сверки.",
        "Важно: имена проставлены по видео-тайлам Телемоста и активности в кадре.",
        "Если где-то имя спорное, лучше править маппинг, а не сам текст.",
        "",
        "Маппинг:",
    ]
    for label, value in aliases.items():
        lines.append(f"- {label} -> {value}")
    if notes:
        lines.append("")
        lines.append("Примечания:")
        for note in notes:
            lines.append(f"- {note}")
    lines.extend(["", "Текст:", ""])
    return "\n".join(lines)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input_path)
    output_path = Path(args.output_path)
    aliases = parse_aliases(args.alias)

    source = input_path.read_text(encoding="utf-8-sig")
    transformed = apply_aliases(source, aliases)
    header = build_header(aliases, args.header_note)
    output_path.write_text(f"{header}{transformed}", encoding="utf-8-sig")

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
