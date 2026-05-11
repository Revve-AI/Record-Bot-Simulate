"""Chạy 1 lần để chuẩn hoá toàn bộ .dialog trong input/:

1) DETECT đúng vai trò bằng cách chấm điểm "độ giống bot" cho từng speaker,
   bot/assistant thường có: tên `vi`/`vy`, `trợ lý`, `dạ`, `ạ`, `anh chị`,
   `vui lòng`, `xin phép`, `cảm ơn anh/chị`, `hỗ trợ`, `mở thẻ`, `ngân hàng`...
   → speaker nào điểm cao hơn → assistant, speaker còn lại → user.

2) Re-format mỗi dòng thành `user: text\\tSTART\\tEND` hoặc
   `assistant: text\\tSTART\\tEND` (giữ timestamps để app vẫn cắt audio chính xác).

3) Chuẩn hoá text: viết hoa, dấu câu, tách câu dài.

Idempotent: chạy lại nhiều lần không gây sai.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from text_utils import normalize_vietnamese_text  # noqa

INPUT_DIR = Path(__file__).parent / "input"

# Bắt mọi dạng prefix có thể có: speaker_0, speaker_1, user, assistant
LINE_RE = re.compile(
    r"^(speaker_(\d+)|user|assistant)\s*:\s*(.+?)(?:\s*\t(\d+)\s*\t(\d+))?\s*$"
)

# Pattern thường gặp ở giọng bot/assistant. Mỗi match cộng 1 điểm.
BOT_PATTERNS = [
    r"\bvi\b", r"\bvy\b",                          # tên bot (giọng AI bank)
    r"\btrợ lý\b",
    r"\banh chị\b",
    r"^\s*dạ\b",                                    # mở đầu bằng "dạ"
    r"(ạ|nhé)\s*[.?!]?\s*$",                        # kết thúc bằng "ạ"/"nhé"
    r"\bsẵn sàng hỗ trợ\b",
    r"\bcảm ơn (anh|chị)\b",
    r"\bem chào (anh|chị)\b",
    r"\bxin chào (anh|chị)\b",
    r"\bhỗ trợ\b",
    r"\bngân hàng\b",
    r"\bmở thẻ\b",
    r"\bxin phép\b",
    r"\bvui lòng\b",
    r"\bchứng minh nhân dân\b",
    r"\bem xin\b",
    r"\bem là\b",
    r"\bhoàn tất\b",
    r"\bhồ sơ\b",
]


def score_text(text: str) -> int:
    lo = text.lower()
    return sum(1 for pat in BOT_PATTERNS if re.search(pat, lo))


_VN_DIACRITICS_RE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    r"ùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)


def is_garbage_turn(text: str) -> bool:
    """Phát hiện turn rác (ASR transcribe sai, gibberish, English-only).

    Tiêu chí 'rác':
    - Có nhiều token rời rạc 1-2 ký tự liên tiếp (vd: 'c z', 'a b c')
    - Không có ký tự diacritic tiếng Việt nào dù có >= 3 token
    - Toàn ký tự đặc biệt / số / khoảng trắng
    """
    t = re.sub(r"[.!?,\s]+", " ", text).strip().lower()
    if not t:
        return True
    tokens = t.split()
    if not tokens:
        return True

    # Tỉ lệ token 1-ký tự > 40%  →  rác
    short = sum(1 for tk in tokens if len(tk) <= 1)
    if len(tokens) >= 2 and short / len(tokens) > 0.4:
        return True

    # >= 3 token mà không có diacritic Việt nào → có thể là English/gibberish
    if len(tokens) >= 3 and not _VN_DIACRITICS_RE.search(text):
        # Cho qua nếu có 1 vài từ tiếng Việt thuần không dấu phổ biến
        common_no_diacritic = {"alo", "ok", "co", "khong", "ho", "ten",
                               "anh", "chi", "em", "ngan", "hang", "vib", "acb"}
        if not any(tk in common_no_diacritic for tk in tokens):
            return True

    # Toàn từ 1-2 ký tự
    if all(len(tk) <= 2 for tk in tokens):
        return True

    return False


def detect_assistant_label(turns: list[tuple[str, str]]) -> str | None:
    """Trả về label nào là bot/assistant (vd 'speaker_0' / 'user' / 'assistant').

    None nếu chỉ có 1 speaker (không phân biệt được)."""
    by_label: dict[str, list[str]] = {}
    for label, text in turns:
        by_label.setdefault(label, []).append(text)

    if len(by_label) < 2:
        return None

    # Avg bot-score per label (chia cho số turn để tránh thiên vị speaker nhiều dòng)
    avg = {}
    for label, texts in by_label.items():
        total = sum(score_text(t) for t in texts)
        avg[label] = total / max(1, len(texts))

    return max(avg, key=avg.get)


def format_file(path: Path) -> tuple[int, int, int, str | None]:
    """Trả về (số dòng đổi role, số dòng đổi text, số dòng XOÁ rác, asst_label)."""
    parsed: list[tuple[str, str | None, str, str | None, str | None]] = []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    for line in raw_lines:
        m = LINE_RE.match(line)
        if not m:
            parsed.append((None, None, line, None, None))
            continue
        prefix, spk_num, text, start, end = m.groups()
        parsed.append((prefix, spk_num, text.strip(), start, end))

    # Detect assistant label
    valid_turns = [(p[0], p[2]) for p in parsed if p[0]]
    assistant_label = detect_assistant_label(valid_turns)

    changed_role = 0
    changed_text = 0
    dropped = 0
    out: list[str] = []
    for orig, _spk_num, text, start, end in parsed:
        if orig is None:
            out.append(text)
            continue

        # Bỏ turn rác (gibberish, ASR fail)
        if is_garbage_turn(text):
            dropped += 1
            continue

        if assistant_label is not None:
            role = "assistant" if orig == assistant_label else "user"
        elif orig in ("user", "assistant"):
            role = orig
        elif orig == "speaker_0":
            role = "assistant"
        else:
            role = "user"

        if role != orig:
            changed_role += 1

        new_text = normalize_vietnamese_text(text)
        if new_text != text:
            changed_text += 1

        if start is not None and end is not None:
            out.append(f"{role}: {new_text}\t{start}\t{end}")
        else:
            out.append(f"{role}: {new_text}")

    new_content = "\n".join(out) + "\n"
    if new_content != path.read_text(encoding="utf-8"):
        path.write_text(new_content, encoding="utf-8")
    return changed_role, changed_text, dropped, assistant_label


def main() -> None:
    if not INPUT_DIR.exists():
        print(f"❌ Không tìm thấy thư mục: {INPUT_DIR}")
        sys.exit(1)
    files = sorted(INPUT_DIR.glob("*.dialog"))
    if not files:
        print(f"❌ Không có file .dialog nào trong {INPUT_DIR}")
        sys.exit(1)
    print(f"🔧 Đang xử lý {len(files)} file .dialog ...\n")
    total_role = total_text = total_drop = 0
    for fp in files:
        cr, ct, dr, asst_label = format_file(fp)
        total_role += cr
        total_text += ct
        total_drop += dr
        marker = "✏️ " if (cr + ct + dr) > 0 else "  "
        asst_note = f"  (assistant = {asst_label})" if asst_label else ""
        print(f"{marker}{fp.name}{asst_note}")
        if cr or ct or dr:
            print(f"    → đổi role: {cr},  chuẩn hoá text: {ct},  XOÁ rác: {dr}")
    print(
        f"\n✅ Hoàn tất: {total_role} dòng đổi role + "
        f"{total_text} dòng chuẩn hoá text + "
        f"{total_drop} dòng XOÁ rác trên {len(files)} file."
    )


if __name__ == "__main__":
    main()
