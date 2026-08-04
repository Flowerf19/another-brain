"""Deterministic generator for the `embedding-quality-v1` corpus (TASK-002).

Run from the REPO ROOT environment (pinned production tokenizer):

    uv run python spikes/fp32/build_corpus.py

Corpus contract (Plan 07, Q4 gate):
- 600 documents + 120 judged semantic queries: 60 Vietnamese, 60 English;
- query token buckets (RAW query Harrier tokens, no prompt/specials — the
  prompted-total reading is impossible because QUERY_PROMPT alone is 19-21
  tokens): 40 queries each at 1-16, 17-64, 65-128 raw tokens, with the top
  bucket capped at 107 raw tokens so prompted totals stay within the 128
  budget; 20 Vietnamese queries are no-diacritic variants;
- relevance graded 0..3; each query has one grade-3 target, one grade-2, one
  grade-1, and four judged hard negatives (grade 0);
- separate 24-case behavior partition: 12 content-only identifiers, 6
  punctuation-only queries, 6 expired/deleted starvation cases.

Output: corpus/embedding-quality-v1.json + corpus/manifest.json with every
hash the gate requires. Regenerating with the same seed + generator commit
reproduces the corpus byte-for-byte.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
import unicodedata
from pathlib import Path

from tokenizers import Tokenizer

SPIKE = Path(__file__).resolve().parent
CORPUS_DIR = SPIKE / "corpus"
SEED = 20260804
NOW_MS = 1_785_000_000_000
# Semantic docs span the ~30 days before now_ms so every semantic doc is live
# (min TTL 7 days could otherwise expire old entries); expires_at_ms is the
# importance TTL counted from creation, which lands after now_ms by design.
BASE_MS = NOW_MS - 30 * 86_400_000
TTL_DAYS = {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}

QUERY_PROMPT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query"
    "\nQuery: "
)

sys.path.insert(0, str(SPIKE))
from fetch_models import (  # noqa: E402
    FP32_SAFETENSORS_SHA256,
    Q4_FILES_SHA256,
    Q4_REPO,
    Q4_REVISION,
    FP32_REPO,
    FP32_REVISION,
)

# ---------------------------------------------------------------------------
# Cluster data: 6 English + 6 Vietnamese clusters, 5 subjects each.
# Slots: {thing} {attr} {action} {detail} {place} {n}
# ---------------------------------------------------------------------------

EN_SUMMARY_T = [
    "{action_cap} {thing} {detail}; check again after {n} days.",
    "For {thing}, {action} {detail} — keeps {attr} results over time.",
    "Note: {thing} stays {attr} when you {action} {detail}.",
]
EN_CONTENT_T = [
    "Checklist: inspect {thing}, {action} {detail}, log the outcome in the diary.",
    "Last run at {place}: {thing} was {attr}. Next step: {action} {detail}.",
]
EN_Q_SHORT = [
    "how to {action} {thing} {aspect_kw}",
    "{thing} {aspect_kw} tips",
]
EN_Q_MEDIUM = [
    "how to {action} {thing} {detail} at {place} with a focus on {aspect_kw}",
    "complete guide to {action} {thing} {aspect_kw} at {place}, explained for beginners",
]
EN_Q_LONG = [
    "I am trying to {action} {thing} {detail} at {place} and I keep running into problems. Can you explain in detail the full routine I should follow every week, the warning signs that something is going wrong, the most common beginner mistakes and how to avoid them, plus a realistic schedule that keeps {thing} {attr} over several months — I specifically care about {aspect_kw} and want practical advice on that",
]

VI_SUMMARY_T = [
    "{action_cap} {thing} {detail}; kiểm tra lại sau {n} ngày.",
    "Với {thing}, nên {action} {detail} để giữ kết quả {attr} lâu dài.",
    "Ghi chú: {thing} sẽ {attr} nếu bạn {action} {detail}.",
]
VI_CONTENT_T = [
    "Danh sách: kiểm tra {thing}, {action} {detail}, ghi lại kết quả vào nhật ký.",
    "Lần trước tại {place}: {thing} khá {attr}. Bước tiếp: {action} {detail}.",
]
VI_Q_SHORT = [
    "cách {action} {thing} {aspect_kw}",
    "mẹo {thing} {aspect_kw}",
]
VI_Q_MEDIUM = [
    "cách {action} {thing} {detail} tại {place} với trọng tâm là {aspect_kw}",
    "hướng dẫn đầy đủ cách {action} {thing} {aspect_kw} tại {place} cho ngườoi mới",
]
VI_Q_LONG = [
    "Tôi đang cố gắng {action} {thing} {detail} tại {place} nhưng liên tục gặp khó khăn. Bạn có thể giải thích chi tiết quy trình tôi nên làm theo mỗi tuần, các lỗi phổ biến ngườoi mới hay mắc phải và cách tránh chúng, cùng lịch trình thực tế để giữ {thing} luôn {attr} trong nhiều tháng — tôi đặc biệt quan tâm đến {aspect_kw}",
]


def _en(topic, thing, attr, action, detail, place):
    return {
        "topic": topic,
        "vocab": {"thing": thing, "attr": attr, "action": action, "detail": detail, "place": place},
    }


def _vi(topic, thing, attr, action, detail, place):
    return _en(topic, thing, attr, action, detail, place)


CLUSTERS = [
    {
        "id": "home-garden", "lang": "en",
        "subjects": [
            _en("basil-care", "basil pots", "healthy", "water", "every other morning", "the balcony"),
            _en("tomato-feeding", "tomato plants", "productive", "feed", "with diluted compost tea", "the raised bed"),
            _en("compost-balance", "compost bin", "odor free", "balance", "with dry leaves", "the backyard corner"),
            _en("tool-maintenance", "garden tools", "rust free", "oil", "after each weekend session", "the shed"),
            _en("aphid-control", "rose bushes", "pest free", "spray", "with soapy water", "the front fence"),
        ],
    },
    {
        "id": "trail-training", "lang": "en",
        "subjects": [
            _en("hill-repeats", "hill repeat sessions", "strong", "run", "at moderate effort", "the north trail"),
            _en("long-run-fueling", "long runs", "steady", "fuel", "with small carb portions", "the river loop"),
            _en("ankle-mobility", "ankle mobility drills", "resilient", "practice", "before every run", "the track"),
            _en("recovery-weeks", "recovery weeks", "sustainable", "schedule", "every fourth week", "the training block"),
            _en("rain-gear", "rain jackets", "breathable", "choose", "with taped seams", "the gear closet"),
        ],
    },
    {
        "id": "home-cooking", "lang": "en",
        "subjects": [
            _en("sourdough-starter", "sourdough starter", "active", "feed", "with rye flour", "the kitchen counter"),
            _en("knife-honing", "chef knives", "sharp", "hone", "with a ceramic rod", "the workshop"),
            _en("rice-texture", "jasmine rice", "fluffy", "rinse", "until the water runs clear", "the sink"),
            _en("stock-simmer", "chicken stock", "clear", "simmer", "at the lowest bubble", "the stove"),
            _en("herb-storage", "fresh herbs", "fresh", "store", "wrapped in damp paper", "the fridge drawer"),
        ],
    },
    {
        "id": "db-operations", "lang": "en",
        "subjects": [
            _en("index-rebuild", "fragmented indexes", "efficient", "rebuild", "during the maintenance window", "the primary node"),
            _en("vacuum-schedule", "autovacuum settings", "balanced", "tune", "for write heavy tables", "the analytics cluster"),
            _en("backup-verify", "nightly backups", "reliable", "verify", "with a restore drill", "the standby server"),
            _en("connection-pooling", "connection pools", "stable", "size", "to twice the core count", "the app tier"),
            _en("slow-query-review", "slow query logs", "actionable", "review", "every monday morning", "the dashboard"),
        ],
    },
    {
        "id": "home-repair", "lang": "en",
        "subjects": [
            _en("faucet-washer", "leaky faucets", "quiet", "fix", "by replacing the washer", "the bathroom"),
            _en("caulk-seams", "window caulk seams", "weather tight", "renew", "before the rainy season", "the attic"),
            _en("breaker-labels", "breaker panel labels", "accurate", "update", "after each renovation", "the garage"),
            _en("gutter-cleaning", "rain gutters", "free flowing", "clean", "twice a year", "the roof edge"),
            _en("door-hinges", "squeaky door hinges", "silent", "lubricate", "with silicone spray", "the hallway"),
        ],
    },
    {
        "id": "photo-workflow", "lang": "en",
        "subjects": [
            _en("raw-backup", "raw photo archives", "safe", "back up", "to two separate drives", "the desk station"),
            _en("lens-cleaning", "lens front elements", "spotless", "clean", "with a blower first", "the field kit"),
            _en("preset-discipline", "editing presets", "consistent", "apply", "only after white balance", "the studio"),
            _en("keyword-taxonomy", "photo keywords", "searchable", "tag", "during import", "the library"),
            _en("print-calibration", "printer profiles", "faithful", "calibrate", "with a color target", "the print corner"),
        ],
    },
    {
        "id": "nau-an", "lang": "vi",
        "subjects": [
            _vi("pho-nuoc-dung", "nước dùng phở", "trong và ngọt", "ninh", "với lửa nhỏ liu riu", "bếp nhà"),
            _vi("ran-gion", "món rán", "giòn lâu", "phủ", "bằng lớp bột mỏng đều", "chảo gang"),
            _vi("nuoc-mam-phn", "nước mắm pha", "cân bằng", "pha", "theo tỉ lệ một chanh một đường", "bàn ăn"),
            _vi("rau-xanh-luoc", "rau luộc", "xanh giòn", "luộc", "với chút muối và dầu", "nồi to"),
            _vi("com-nguoi", "cơm nguội", "tơi không băm", "xới", "khi còn ấm rồi để nguội", "nồi cơm"),
        ],
    },
    {
        "id": "suc-khoe", "lang": "vi",
        "subjects": [
            _vi("giac-ngu", "giấc ngủ", "sâu và đều", "giữ", "bằng giờ ngủ cố định", "phòng ngủ"),
            _vi("co-vai-gay", "cổ vai gáy", "thư giãn", "kéo giãn", "sau mỗi giờ làm việc", "bàn làm việc"),
            _vi("uong-nuoc", "thói quen uống nước", "đủ và đều", "chia", "thành nhiều lần trong ngày", "bình nước"),
            _vi("di-bo-sang", "buổi đi bộ sáng", "đều đặn", "duy trì", "ít nhất ba mươi phút", "công viên"),
            _vi("an-sang", "bữa sáng", "đầy đủ", "chuẩn bị", "từ tối hôm trước", "bếp"),
        ],
    },
    {
        "id": "du-lich", "lang": "vi",
        "subjects": [
            _vi("vali-gon-nhe", "vali du lịch", "gọn nhẹ", "cuộn", "quần áo thay vì gấp", "phòng ngủ"),
            _vi("cho-ben-thanh", "chợ Bến Thành", "đông đúc", "ghé", "vào sáng sớm trong tuần", "quận một"),
            _vi("da-lat-mua-mua", "Đà Lạt mùa mưa", "lạnh và ẩm", "mang", "áo khoác chống nước", "thành phố"),
            _vi("ve-may-bay-som", "vé máy bay", "rẻ hơn", "đặt", "trước sáu đến tám tuần", "trang đặt vé"),
            _vi("homestay-sapa", "homestay Sapa", "yên tĩnh", "chọn", "xa trung tâm thị trấn", "bản Tả Van"),
        ],
    },
    {
        "id": "cong-viec", "lang": "vi",
        "subjects": [
            _vi("hop-ngan", "cuộc họp", "ngắn gọn", "giới hạn", "trong hai mươi lăm phút", "phòng họp"),
            _vi("email-ro-rang", "email công việc", "rõ ràng", "viết", "với tiêu đề nêu hành động", "hộp thư"),
            _vi("uu-tien-tuan", "việc ưu tiên tuần", "thực tế", "chọn", "không quá ba việc lớn", "sổ tay"),
            _vi("bao-cao-tuan", "báo cáo tuần", "ngắn và đủ", "gửi", "trước năm giờ chiều thứ sáu", "nhóm dự án"),
            _vi("lam-viec-sau", "khối làm việc sâu", "tập trung", "đặt", "vào buổi sáng sớm", "lịch cá nhân"),
        ],
    },
    {
        "id": "hoc-tap", "lang": "vi",
        "subjects": [
            _vi("kanji-moi-ngay", "chữ kanji", "nhớ lâu", "ôn", "hai mươi chữ mỗi tối", "bàn học"),
            _vi("ngu-phap-de", "ngữ pháp", "vững chắc", "luyện", "bằng câu ví dụ thực tế", "sách giáo khoa"),
            _vi("nghe-chu-dong", "kỹ năng nghe", "cải thiện", "nghe", "lại đoạn khó ba lần", "tai nghe"),
            _vi("ghi-chep-sach", "ghi chép sách", "mạch lạc", "tóm tắt", "theo ý chính từng chương", "thư viện"),
            _vi("thi-thu", "bài thi thử", "sát thật", "làm", "trong thờoi gian giới hạn", "phòng thi"),
        ],
    },
    {
        "id": "nha-cua", "lang": "vi",
        "subjects": [
            _vi("may-giat-ve-sinh", "lồng máy giặt", "sạch không mùi", "vệ sinh", "bằng chế độ tự làm sạch", "phòng giặt"),
            _vi("roi-dien-nang", "rò rỉ điện", "an toàn", "kiểm tra", "bằng bút thử điện", "bảng điện"),
            _vi("cua-so-mua-mua", "cửa sổ mùa mưa", "kín nước", "tra", "ron cao su mỗi năm", "khung cửa"),
            _vi("tu-lanh-ngan", "ngăn tủ lạnh", "thoáng", "sắp xếp", "theo nhóm thực phẩm", "bếp"),
            _vi("ong-nuoc-tac", "ống nước tắc", "thông thoáng", "thông", "bằng baking soda và giấm", "nhà tắm"),
        ],
    },
]

BEHAVIOR_ID_PREFIX = "RUNID-"

# Per-language aspects give the 10 docs within one subject distinct,
# identifiable angles; queries reference the aspect so the grade-3 target is
# discoverable and same-subject docs form a tight relevant set.
ASPECTS = {
    "en": [
        ("following a fixed weekly schedule", "weekly schedule"),
        ("using only basic inexpensive tools", "basic tools"),
        ("avoiding the most common beginner mistakes", "beginner mistakes"),
        ("with notes on saving cost and time", "cost saving"),
        ("with safety checks before you start", "safety checks"),
        ("with seasonal timing in mind", "seasonal timing"),
        ("plus troubleshooting when results disappoint", "troubleshooting"),
        ("with quality checks at every step", "quality checks"),
        ("adapted for small spaces", "small spaces"),
        ("with a simple log to track progress", "progress log"),
    ],
    "vi": [
        ("theo lịch cố định hàng tuần", "lịch hàng tuần"),
        ("chỉ với dụng cụ đơn giản rẻ tiền", "dụng cụ đơn giản"),
        ("tránh các lỗi ngườoi mới hay gặp", "lỗi ngườoi mới"),
        ("kèm mẹo tiết kiệm chi phí và thờoi gian", "tiết kiệm chi phí"),
        ("với các bước kiểm tra an toàn trước khi bắt đầu", "kiểm tra an toàn"),
        ("theo đúng thờoi điểm mùa vụ", "mùa vụ"),
        ("và cách xử lý khi kết quả không như ý", "xử lý sự cố"),
        ("với kiểm tra chất lượng ở từng bước", "kiểm tra chất lượng"),
        ("phù hợp với không gian nhỏ", "không gian nhỏ"),
        ("kèm nhật ký theo dõi tiến độ", "nhật ký tiến độ"),
    ],
}


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D")


def fill(template: str, vocab: dict, rng: random.Random, n: int,
         aspect_kw: str = "") -> str:
    text = template.format(
        thing=vocab["thing"], attr=vocab["attr"], action=vocab["action"],
        action_cap=vocab["action"].capitalize(), detail=vocab["detail"],
        place=vocab["place"], n=n, aspect_kw=aspect_kw,
    )
    return text


def main() -> int:
    rng = random.Random(SEED)
    tokenizer = Tokenizer.from_file(str(SPIKE / ".models" / "q4" / "tokenizer.json"))

    def raw_tokens(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    # ---- 600 documents -------------------------------------------------
    documents = []
    by_subject: dict[tuple[str, str], list[dict]] = {}
    by_cluster: dict[str, list[dict]] = {}
    created = BASE_MS
    for cluster in CLUSTERS:
        aspects = ASPECTS[cluster["lang"]]
        for subject in cluster["subjects"]:
            for i in range(10):
                created += 43_200_000 // 10  # ~1.2h steps across the 30 days
                importance = 1 + (i % 5)
                expires = created + TTL_DAYS[importance] * 86_400_000
                if expires <= NOW_MS:  # keep every semantic doc live at now_ms
                    expires = NOW_MS + TTL_DAYS[importance] * 86_400_000
                clause, _kw = aspects[i]
                base_summary = fill(rng.choice(
                    EN_SUMMARY_T if cluster["lang"] == "en" else VI_SUMMARY_T
                ), subject["vocab"], rng, 3 + i)
                summary = f"{base_summary} — {clause}." if cluster["lang"] == "en" \
                    else f"{base_summary} — {clause}."
                base_content = fill(rng.choice(
                    EN_CONTENT_T if cluster["lang"] == "en" else VI_CONTENT_T
                ), subject["vocab"], rng, 3 + i)
                content = f"{base_content} ({clause})"
                doc = {
                    "doc_id": f"{cluster['id']}:{subject['topic']}:{i:02d}",
                    "partition": "semantic",
                    "cluster_id": cluster["id"],
                    "lang": cluster["lang"],
                    "topic": subject["topic"],
                    "aspect": clause,
                    "catalog": "note",
                    "summary": summary,
                    "content": content,
                    "importance": importance,
                    "created_at_ms": created,
                    "expires_at_ms": expires,
                    "deleted_at_ms": None,
                }
                documents.append(doc)
                by_subject.setdefault((cluster["id"], subject["topic"]), []).append(doc)
                by_cluster.setdefault(cluster["id"], []).append(doc)
    assert len(documents) == 600

    # ---- 120 judged queries -------------------------------------------
    templates_by_len = {
        "en": {"short": EN_Q_SHORT, "medium": EN_Q_MEDIUM, "long": EN_Q_LONG},
        "vi": {"short": VI_Q_SHORT, "medium": VI_Q_MEDIUM, "long": VI_Q_LONG},
    }
    bucket_of = lambda n: 0 if n <= 16 else (1 if n <= 64 else 2)
    bucket_counts = [0, 0, 0]
    queries = []
    q_index = 0
    # Two queries per subject; assign lengths so each bucket reaches 40.
    subject_list = [
        (cluster, subject) for cluster in CLUSTERS for subject in cluster["subjects"]
    ]
    for round_no in range(2):
        for cluster, subject in subject_list:
            q_index += 1
            templs = templates_by_len[cluster["lang"]]
            docs_same = by_subject[(cluster["id"], subject["topic"])]
            target = docs_same[(q_index * 3) % len(docs_same)]
            target_aspect_idx = int(target["doc_id"].rsplit(":", 1)[1])
            aspect_kw = ASPECTS[cluster["lang"]][target_aspect_idx][1]
            # prefer the length whose bucket is least filled
            order = sorted(("short", "medium", "long"), key=lambda k: bucket_counts[bucket_of({
                "short": 8, "medium": 32, "long": 85}[k])])
            text = None
            for length in order:
                candidate = fill(templs[length][q_index % len(templs[length])],
                                 subject["vocab"], rng, 5, aspect_kw)
                tokens = raw_tokens(candidate)
                bucket = bucket_of(tokens)
                if length == "long" and tokens > 107:
                    continue  # prompted total must stay <= 128
                if bucket_counts[bucket] < 40:
                    text, bucket_no = candidate, bucket
                    break
            if text is None:  # all buckets full for these lengths; force smallest
                candidate = fill(templs["short"][q_index % len(templs["short"])],
                                 subject["vocab"], rng, 5, aspect_kw)
                text = candidate
                bucket_no = bucket_of(raw_tokens(candidate))
            bucket_counts[bucket_no] += 1

            # judgments: grade 3 target + all 9 other same-subject docs graded
            # 2 (x4) and 1 (x5) — the complete same-subject relevant set —
            # plus 4 hard negatives (same cluster, different subjects, grade 0).
            pool = [d for d in docs_same if d["doc_id"] != target["doc_id"]]
            rng.shuffle(pool)
            grade2 = pool[:4]
            grade1 = pool[4:9]
            others = [d for d in by_cluster[cluster["id"]] if d["topic"] != subject["topic"]]
            rng.shuffle(others)
            hard_negs = others[:4]

            queries.append({
                "query_id": f"q{q_index:03d}",
                "partition": "semantic",
                "cluster_id": cluster["id"],
                "lang": cluster["lang"],
                "text": text,
                "raw_tokens": raw_tokens(text),
                "bucket": bucket_no,
                "no_diacritic": False,
                "judgments": [
                    {"doc_id": target["doc_id"], "grade": 3},
                    *[{"doc_id": d["doc_id"], "grade": 2} for d in grade2],
                    *[{"doc_id": d["doc_id"], "grade": 1} for d in grade1],
                    *[{"doc_id": d["doc_id"], "grade": 0} for d in hard_negs],
                ],
            })
    assert len(queries) == 120
    assert bucket_counts == [40, 40, 40], f"bucket distribution off: {bucket_counts}"
    vi_queries = [q for q in queries if q["lang"] == "vi"]
    assert len(vi_queries) == 60
    en_queries = [q for q in queries if q["lang"] == "en"]
    assert len(en_queries) == 60
    # 20 no-diacritic VI variants: only convert queries whose stripped text
    # stays in the same token bucket and within the 107-token cap (stripping
    # diacritics can inflate the token count).
    converted = 0
    for q in vi_queries:
        if converted >= 20:
            break
        stripped = strip_diacritics(q["text"])
        tokens = raw_tokens(stripped)
        if tokens <= 107 and bucket_of(tokens) == q["bucket"]:
            q["text"] = stripped
            q["raw_tokens"] = tokens
            q["no_diacritic"] = True
            converted += 1
    assert converted == 20, f"only {converted} VI queries eligible for no-diacritic variant"

    # ---- behavior partition (24 cases) ---------------------------------
    behavior_docs, behavior_cases = [], []
    b_created = BASE_MS
    # 12 content-only identifiers
    for i in range(12):
        b_created += 86_400_000
        ident = f"{BEHAVIOR_ID_PREFIX}{8800 + i}"
        cluster = CLUSTERS[i % len(CLUSTERS)]
        subject = cluster["subjects"][i % 5]
        doc = {
            "doc_id": f"behavior:content-id:{i:02d}",
            "partition": "behavior", "cluster_id": cluster["id"], "lang": cluster["lang"],
            "topic": subject["topic"],
            "catalog": "note",
            "summary": fill(VI_SUMMARY_T[0] if cluster["lang"] == "vi" else EN_SUMMARY_T[0], subject["vocab"], rng, 7),
            "content": f"raw samples and logs archived under run {ident}",
            "importance": 3, "created_at_ms": b_created,
            "expires_at_ms": b_created + 90 * 86_400_000, "deleted_at_ms": None,
        }
        behavior_docs.append(doc)
        behavior_cases.append({
            "case_id": f"content-id-{i:02d}", "kind": "content_only_identifier",
            "query": ident, "expect_doc_id": doc["doc_id"],
            "expect": "returned via the lexical branch even below the cosine floor",
        })
    # 6 punctuation-only queries
    for i, text in enumerate(["?!?!", "...", "—–—", ",,,", ";;;", "???"]):
        behavior_cases.append({
            "case_id": f"punct-{i:02d}", "kind": "punctuation_only_query",
            "query": text, "expect_doc_id": None,
            "expect": "no safe lexical terms; vector-only retrieval, never an error",
        })
    # 6 expired/deleted starvation
    for i in range(6):
        b_created += 86_400_000
        ident = f"{BEHAVIOR_ID_PREFIX}S{i:02d}"
        expired = i % 2 == 0
        doc = {
            "doc_id": f"behavior:stale:{i:02d}",
            "partition": "behavior", "cluster_id": "home-garden", "lang": "en",
            "topic": "run-archive", "catalog": "note",
            "summary": f"archived benchmark note mentioning {ident}",
            "content": f"archived details for {ident}",
            "importance": 3, "created_at_ms": b_created,
            "expires_at_ms": (NOW_MS - 1) if expired else (b_created + 30 * 86_400_000),
            "deleted_at_ms": None if expired else b_created,
        }
        behavior_docs.append(doc)
        live_tail = {
            "doc_id": f"behavior:live-tail:{i:02d}",
            "partition": "behavior", "cluster_id": "home-garden", "lang": "en",
            "topic": "run-followups", "catalog": "note",
            "summary": f"follow-up measurements mentioning {ident}",
            "content": f"live details for {ident}",
            "importance": 2, "created_at_ms": b_created + 1000,
            "expires_at_ms": b_created + 30 * 86_400_000, "deleted_at_ms": None,
        }
        behavior_docs.append(live_tail)
        behavior_cases.append({
            "case_id": f"stale-{i:02d}", "kind": "expired_deleted_starvation",
            "query": ident, "expect_doc_id": live_tail["doc_id"], "stale_doc_id": doc["doc_id"],
            "expect": "stale row excluded before branch limits; live tail returned",
        })

    all_docs = documents + behavior_docs

    # ---- manifest --------------------------------------------------------
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    corpus = {
        "schema_version": 1,
        "corpus_id": "embedding-quality-v1",
        "now_ms": NOW_MS,
        "documents": all_docs,
        "queries": queries,
        "behavior_cases": behavior_cases,
    }
    corpus_path = CORPUS_DIR / "embedding-quality-v1.json"
    corpus_bytes = (json.dumps(corpus, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    corpus_path.write_bytes(corpus_bytes)
    corpus_sha = hashlib.sha256(corpus_bytes).hexdigest()

    try:
        generator_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        generator_commit = "unknown"

    manifest = {
        "schema_version": 1,
        "corpus_id": "embedding-quality-v1",
        "corpus_sha256": corpus_sha,
        "source": "synthetic, generated by spikes/fp32/build_corpus.py",
        "license": "MIT (project-internal evaluation corpus)",
        "seed": SEED,
        "generator_commit": generator_commit,
        "row_counts": {
            "documents": len(all_docs),
            "semantic_documents": len(documents),
            "behavior_documents": len(behavior_docs),
            "queries": len(queries),
            "queries_vi": len(vi_queries),
            "queries_en": len(en_queries),
            "queries_vi_no_diacritic": sum(1 for q in vi_queries if q["no_diacritic"]),
            "behavior_cases": len(behavior_cases),
            "behavior_content_only": 12,
            "behavior_punctuation": 6,
            "behavior_stale": 6,
        },
        "token_buckets": {
            "definition": "raw query Harrier tokens, no prompt/specials (prompted-total reading impossible: QUERY_PROMPT alone is 19-21 tokens); top bucket capped at 107 raw tokens so prompted totals stay <= 128",
            "buckets": {"1-16": bucket_counts[0], "17-64": bucket_counts[1], "65-128": bucket_counts[2]},
        },
        "judgments": {
            "scale": "0..3",
            "per_query": {"grade3": 1, "grade2": 4, "grade1": 5,
                          "hard_negatives_grade0": 4,
                          "note": "grades 1-3 cover ALL same-subject docs (complete relevant set); hard negatives are same-cluster different-subject; Recall@5 uses denominator min(5, |relevant|)"},
        },
        "models": {
            "q4": {"repo": Q4_REPO, "revision": Q4_REVISION, "files_sha256": Q4_FILES_SHA256},
            "fp32": {"repo": FP32_REPO, "revision": FP32_REVISION,
                     "model_safetensors_sha256": FP32_SAFETENSORS_SHA256},
        },
        "tokenizer_config_prompt_hashes": {
            "tokenizer_json_sha256": Q4_FILES_SHA256["tokenizer.json"],
            "tokenizer_config_json_sha256": Q4_FILES_SHA256["tokenizer_config.json"],
            "config_json_sha256": Q4_FILES_SHA256["config.json"],
            "query_prompt_utf8_sha256": hashlib.sha256(QUERY_PROMPT.encode("utf-8")).hexdigest(),
        },
        "payload_input_version": 2,
    }
    prompt_hash = hashlib.sha256(QUERY_PROMPT.encode("utf-8")).hexdigest()
    expected_prompt_hash = "df4b2898bf22e00bacddddd489243a3f8793730e38b842ec10161cebd94d36d6"
    assert prompt_hash == expected_prompt_hash, f"QUERY_PROMPT hash drift: {prompt_hash}"

    manifest_path = CORPUS_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"corpus: {len(all_docs)} docs, {len(queries)} queries, {len(behavior_cases)} behavior cases")
    print(f"buckets: {bucket_counts}, sha256: {corpus_sha[:16]}…")
    print(f"wrote {corpus_path} + {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
