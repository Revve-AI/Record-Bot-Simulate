"""Vietnamese text normalization — sentence splitting, capitalization, punctuation.

Tách ra module riêng để cả app.py và format_dialogs.py đều import được mà
không phải khởi tạo silero-vad / gradio.
"""
from __future__ import annotations

import re

_QUESTION_TAILS = (
    "không", "chưa", "à", "ạ", "nào", "sao", "đâu", "mấy",
    "phải không", "thế nào", "không ạ", "chưa ạ", "đúng không",
)
_QUESTION_HEADS = (
    "gì", "nào", "sao", "đâu", "mấy", "tại sao", "vì sao",
    "bao nhiêu", "khi nào", "ở đâu", "ai",
)
_VN_LETTERS = (
    "a-zA-Zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    "ùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ"
    "ÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ"
)
_SENTENCE_END_PARTICLES = ("ạ", "rồi", "đấy", "nhé", "nhỉ", "thôi", "vâng")

_VN_LOWER_LETTERS = (
    "a-zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    "ùúụủũưừứựửữỳýỵỷỹđ"
)

# Họ Việt phổ biến
VN_SURNAMES = (
    "nguyễn", "trần", "lê", "phạm", "hoàng", "huỳnh", "võ", "vũ",
    "đặng", "bùi", "đỗ", "hồ", "ngô", "dương", "lý", "phan",
    "đào", "đoàn", "hà", "trương", "vương", "đinh", "lưu", "tô",
    "tăng", "thái", "mai", "trịnh",
)

# Tên đệm Việt phổ biến — dùng để xác nhận đó là TÊN NGƯỜI
# (vì nhiều họ Việt cũng là từ thông dụng: lý, hà, mai, đào, lê, võ, ...)
VN_MIDDLE_NAMES = (
    "văn", "thị", "đức", "minh", "hữu", "quang", "công",
    "ngọc", "hoàng", "phương", "thanh", "hồng", "kim", "lan",
    "thu", "xuân", "thuý", "thúy", "hương", "anh", "tuấn",
    "trung", "trí", "hải", "đăng", "tấn", "khắc", "duy",
    "huy", "thái", "viết", "bảo", "phú", "gia",
)

# Tên đệm + tên thường thấy — chỉ viết hoa khi đứng sau danh xưng cụ thể
COMMON_GIVEN_NAMES = {
    "linh", "hà", "lan", "hoa", "mai", "thu", "xuân", "đông",
    "ngọc", "thảo", "trang", "huyền", "phương", "nga",
    "hồng", "hạnh", "minh", "nam", "hùng", "dũng", "tâm", "phong",
    "khôi", "long", "hiếu", "tùng", "hải", "đạt", "đức", "quân",
    "sơn", "tuấn", "kim", "oanh", "ngân", "châu", "my", "thắng",
    "trí", "tài", "tiến", "phú", "khang", "vinh", "thịnh",
}


def _capitalize_token(tok: str) -> str:
    """Viết hoa chữ cái đầu tiên của token, giữ nguyên dấu/ký tự đặc biệt."""
    for i, ch in enumerate(tok):
        if ch.isalpha():
            return tok[:i] + tok[i].upper() + tok[i + 1:]
    return tok


def capitalize_proper_nouns(text: str) -> str:
    """Viết hoa tên riêng (người, ngân hàng, tên bot) trong text Việt.

    - VIB / VIP (ASR nghe nhầm) → VIB
    - Họ Việt + 1..3 từ tiếp → tên người đầy đủ
    - "Em là vi/vy", "vi cảm ơn"... → Vi/Vy (tên bot trợ lý)
    - "chị Linh" / "chào Linh" → tên riêng phổ biến viết hoa
    """
    # 1) Ngân hàng VIB (gồm cả "vip" do ASR nghe nhầm)
    text = re.sub(r"\b(vib|vip)\b", "VIB", text, flags=re.IGNORECASE)

    # 2) Tên bot trợ lý: vi / vy
    #   2a) Sau "là " hoặc "chào "
    text = re.sub(
        r"\b(là|chào)\s+(vi|vy)\b",
        lambda m: f"{m.group(1)} {m.group(2).capitalize()}",
        text, flags=re.IGNORECASE,
    )
    #   2b) Sau dấu chấm câu (đầu câu)
    text = re.sub(
        r"(?<=[.!?]\s)(vi|vy)(?=\s+(cảm ơn|xin|sẵn sàng|ghi nhận|luôn|đã|sẽ))",
        lambda m: m.group(1).capitalize(),
        text, flags=re.IGNORECASE,
    )

    # 3) Họ Việt — chỉ cap khi đi với TÊN ĐỆM PHỔ BIẾN hoặc HỌ KẾ TIẾP,
    # để tránh nhầm với từ thông dụng (lý, hà, mai, lê, võ, đào, hồ, trần...).
    surnames_alt = "|".join(VN_SURNAMES)
    middles_alt = "|".join(VN_MIDDLE_NAMES)

    def _cap_full_name(m: re.Match) -> str:
        return " ".join(_capitalize_token(w) for w in m.group(0).split())

    # 3a) <Họ> <Tên đệm> [<Tên>]  vd: "nguyễn minh hiếu", "lê thị hoa"
    text = re.sub(
        rf"\b({surnames_alt})\s+({middles_alt})(?:\s+[{_VN_LOWER_LETTERS}]+)?\b",
        _cap_full_name, text, flags=re.IGNORECASE,
    )
    # 3b) <Họ> <Họ> <từ>  vd: "đặng trần côn" (tên trường mang dáng họ kép)
    text = re.sub(
        rf"\b({surnames_alt})\s+({surnames_alt})\s+[{_VN_LOWER_LETTERS}]+\b",
        _cap_full_name, text, flags=re.IGNORECASE,
    )

    # 4) Tên riêng đứng sau danh xưng (Linh, Hoa, ...).
    # Embed luôn danh sách tên vào pattern để regex chỉ match đúng tên thật,
    # không bị "chào chị" consume mất từ "chị" rồi miss "chị linh".
    titles_alt = "chị|chào|cô|chú|bác|bà|ông"
    given_alt = "|".join(COMMON_GIVEN_NAMES)

    text = re.sub(
        rf"\b({titles_alt})\s+({given_alt})(?=[\s.,!?]|$)",
        lambda m: f"{m.group(1)} {_capitalize_token(m.group(2))}",
        text, flags=re.IGNORECASE,
    )

    return text


# Các liên từ/từ nối thường cần dấu phẩy đứng trước
_PRE_COMMA_CONJUNCTIONS = (
    "nhưng", "vì", "nên", "bao gồm", "trong đó", "ngoài ra",
    "đặc biệt", "tuy nhiên", "hoặc là",
)

# Các từ mở đầu (interjection/thán từ) thường có phẩy đứng sau
_OPEN_COMMA_WORDS = ("dạ", "vâng", "alo", "ờm", "ừm")


def insert_commas(text: str) -> str:
    """Thêm dấu phẩy để ngắt nghỉ cho câu dài (đọc dễ thở hơn):

    1. Sau từ mở đầu kiểu "Dạ", "Vâng", "Alo" + space + nội dung
    2. Trước các liên từ "nhưng", "vì", "nên", "bao gồm", "trong đó"...
    3. Trước cụm "<danh xưng> vui lòng" / "<danh xưng> cho em" khi giữa
       câu dài (đã có ≥5 từ trước đó trong cùng clause)
    """
    # 1) "Dạ X" / "Vâng X" / "Alo X" → "Dạ, X"
    open_alt = "|".join(_OPEN_COMMA_WORDS)
    text = re.sub(
        rf"\b({open_alt})\s+(?=[a-zA-ZÀ-ỹ])",
        lambda m: f"{_capitalize_token(m.group(1))}, ",
        text, flags=re.IGNORECASE,
    )

    # 2) Trước các liên từ trong câu dài
    for conj in _PRE_COMMA_CONJUNCTIONS:
        text = re.sub(
            rf"(?<!^)([{_VN_LOWER_LETTERS}A-Z]+)\s+(?={re.escape(conj)}\b)",
            lambda m: f"{m.group(1)}, ",
            text, flags=re.IGNORECASE,
        )

    # 3) Trước cụm "<title> vui lòng / cho em / xin / xác nhận" — chỉ thêm
    # phẩy nếu cụm đó KHÔNG đứng đầu chuỗi (có nội dung ≥ 1 ký tự trước đó).
    # Pattern đơn giản, không backtracking exponential.
    text = re.sub(
        r"(?<=\S)\s+"
        r"((?:anh chị|anh|chị|em|cô|chú|cháu|mình)\s+(?:vui lòng|cho em|xin phép|xác nhận))",
        lambda m: f", {m.group(1)}",
        text, flags=re.IGNORECASE,
    )

    # Dọn: gộp dấu phẩy lặp, xoá space dư trước phẩy
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
_SENTENCE_STARTERS = (
    "anh", "chị", "em", "tôi", "tao", "mày", "mình", "bạn",
    "dạ", "vâng", "ừ", "ờ", "ơ",
    "vi", "vy",
    "ờm", "thì",
    "xin", "chào", "cho", "cảm",
    "đúng", "có", "không",
    "alo", "này", "đó",
)
_QUESTION_WORDS = (
    "không", "chưa", "gì", "nào", "đâu", "sao", "mấy",
    "tại sao", "vì sao", "khi nào", "ở đâu",
    "phải không", "đúng không", "có phải",
)


def _has_question_word(text: str) -> bool:
    lo = text.lower()
    return any(re.search(rf"\b{re.escape(q)}\b", lo) for q in _QUESTION_WORDS)


def _split_long_sentences(text: str) -> str:
    """Chèn `.` sau tiểu từ kết câu CHỈ KHI tiếp theo là từ bắt đầu câu mới."""
    starters_alt = "|".join(_SENTENCE_STARTERS)
    for p in _SENTENCE_END_PARTICLES:
        pattern = rf"(?<=\s){p}\s+(?=(?:{starters_alt})\b)"
        text = re.sub(pattern, f"{p}. ", text, flags=re.IGNORECASE)
    return text


def normalize_vietnamese_text(text: str) -> str:
    """Chuẩn hoá text tiếng Việt: viết hoa + dấu câu + tách câu dài."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ""

    text = _split_long_sentences(text)
    text = re.sub(r"([.!?])\s*[.!?]+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    text = text[0].upper() + text[1:]
    text = re.sub(
        rf"([.!?]\s+)([{_VN_LETTERS}])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )

    # Viết hoa tên riêng (người, ngân hàng, tên bot)
    text = capitalize_proper_nouns(text)
    # Thêm dấu phẩy ngắt nghỉ
    text = insert_commas(text)
    # Sau khi insert commas có thể làm mất viết hoa sau dấu chấm; áp lại
    text = re.sub(
        rf"([.!?]\s+)([{_VN_LETTERS}])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )

    if text[-1] not in ".!?":
        text += "?" if _has_question_word(text) else "."

    # Mỗi clause: nếu có từ để hỏi mà terminator là "." → đổi sang "?"
    sentences = re.split(r"([.!?]\s*)", text)
    result = ""
    for i in range(0, len(sentences), 2):
        clause = sentences[i] if i < len(sentences) else ""
        terminator = sentences[i + 1] if i + 1 < len(sentences) else ""
        if clause and terminator.startswith("."):
            if _has_question_word(clause):
                terminator = "?" + terminator[1:]
        result += clause + terminator
    return result
