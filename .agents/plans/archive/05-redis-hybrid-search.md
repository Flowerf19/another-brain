---
status: superseded
owner: architecture
created: 2026-07-11
updated: 2026-07-11
scope: step-05
depends_on:
  - .agents/plans/archive/01-architecture-foundation.md
  - .agents/plans/archive/04-memory-record-and-redis-index-contract.md
superseded_by: .agents/plans/07-multiplatform-embedded-runtime.md
---

# Step 05 - FT.HYBRID trên Redis 8.8

> **Superseded for `v0.11.0` (2026-07-31).** This file is retained as
> Redis-era architecture history and legacy-oracle evidence. The approved
> target is `.agents/plans/another-brain-architecture.md`; execution is Plan
> 07. Do not implement new runtime behavior from this document.


Tài liệu giải thích, không phải tài liệu quyết định. Mục đích: cho thấy cơ chế
`FT.HYBRID` hoạt động thế nào trước khi duyệt thiết kế search chính thức
(bước tiếp theo). Repo đã nâng `docker/docker-compose.yml` lên image
`redis:8.8`; tài liệu này giải thích tại sao và điều đó thay đổi gì cho
`MemorySearchEngine`.

## 1. Redis Stack vs Redis 8 là gì

```mermaid
flowchart TB
    subgraph Stack["Redis Stack 7.4 – đóng gói rời"]
        Core1["Redis 7.x core"]
        Mod1["Module RediSearch"]
        Mod2["Module RedisJSON"]
        Mod3["Module RedisTimeSeries"]
        Mod4["Module RedisBloom"]
        Core1 -.->|nạp module lúc khởi động| Mod1
        Core1 -.->|nạp module lúc khởi động| Mod2
        Core1 -.->|nạp module lúc khởi động| Mod3
        Core1 -.->|nạp module lúc khởi động| Mod4
    end

    subgraph Open8["Redis 8.8 Open Source – tích hợp sẵn"]
        Core2["Redis 8.8 core"]
        Query["Query Engine – FT.*, gồm FT.HYBRID"]
        Json["JSON"]
        Ts["Time Series"]
        Bloom["Bloom / xác suất"]
        Vset["Vector Set – kiểu dữ liệu mới, không dùng trong repo này"]
        Core2 --- Query
        Core2 --- Json
        Core2 --- Ts
        Core2 --- Bloom
        Core2 --- Vset
    end

    Stack ==>|"nâng cấp 2026-07-11, image redis:8.8"| Open8
```

"Redis Stack" chỉ là cách đóng gói cũ: Redis core cộng các module rời
&#40;RediSearch, RedisJSON...&#41; nạp cùng lúc. Từ Redis Open Source 8.x, các
module này được gộp thẳng vào binary chính dưới tên "Query Engine" và các
tính năng liên quan — không còn khái niệm "Stack" nữa, chỉ còn một image
`redis:<version>`. Repo dùng `embedding` là field `VECTOR HNSW` bên trong
Query Engine &#40;Section 3, Step 04&#41;, **không** dùng kiểu dữ liệu Vector
Set mới — hai thứ khác nhau, đừng nhầm.

`FT.HYBRID` xuất hiện từ Redis 8.4.0. Repo chọn **8.8.0** &#40;GA tháng
5/2026&#41; vì 8.8 sửa thêm vài bug hybrid search: race condition khi nhiều
hybrid query chạy đồng thời, `VSIM` với `RANGE` + `FILTER` trả về 0 kết quả
sai, memory leak khi `FT.DROPINDEX` đồng thời — cộng thêm giới hạn ứng viên
theo shard và hỗ trợ `FT.PROFILE` cho hybrid query. Client bắt buộc
`redis-py >= 7.1`; repo đã lên `8.0.1`.

## 2. FT.HYBRID hoạt động thế nào

```mermaid
flowchart TD
    Q["Query text + query vector qv<br/>filter bắt buộc: brain_id, deleted, ..."] --> Cmd["FT.HYBRID index<br/>SEARCH ... VSIM ... COMBINE RRF"]

    Cmd --> Branch1["Nhánh SEARCH<br/>full-text trên field TEXT<br/>scorer mặc định BM25STD<br/>hỗ trợ cú pháp FT.SEARCH đầy đủ:<br/>field-specific, boolean, phrase, TAG"]
    Cmd --> Branch2["Nhánh VSIM<br/>KNN 2 K k trên field VECTOR HNSW<br/>PARAMS 2 qv blob"]

    Branch1 --> Rank1["Danh sách xếp hạng theo text_score<br/>YIELD_SCORE_AS text_score"]
    Branch2 --> Rank2["Danh sách xếp hạng theo vector_score<br/>YIELD_SCORE_AS vector_score"]

    Rank1 --> Fuse["COMBINE RRF<br/>fused = tổng 1 / CONSTANT + rank<br/>trên mọi nhánh doc xuất hiện"]
    Rank2 --> Fuse

    Fuse --> LoadLimit["LOAD field cần xem trước<br/>LIMIT phân trang"]
    LoadLimit --> Out["Danh sách đã fuse, sắp theo fused score<br/>@__key, @__score/@__combined_score,<br/>@vector_distance"]
```

Một lệnh `FT.HYBRID` chạy **hai nhánh song song trong Redis** rồi tự fuse:
nhánh `SEARCH` chấm điểm BM25STD trên field TEXT &#40;`summary`, `content`&#41;,
nhánh `VSIM` chạy KNN trên field VECTOR HNSW &#40;`embedding`&#41;. `COMBINE RRF`
cộng dồn `1 / &#40;CONSTANT + rank&#41;` qua từng nhánh — doc nào khớp cả hai
nhánh thì cộng hai lần, nên tự nhiên nổi lên đầu danh sách.

| Tham số | Mặc định | Ghi chú |
| --- | --- | --- |
| `CONSTANT` | 60 | trùng khớp `SEARCH_FUSION_K=60` đã đóng băng ở Step 04 — không cần tune lại |
| `WINDOW` | 20 | số ứng viên top của mỗi nhánh được đưa vào fusion |
| `LIMIT` | 10 | mặc định của `FT.HYBRID`; `brain_search` sẽ set theo `SEARCH_TOP_K=20` |

Field dành riêng trong kết quả: `@__key` &#40;key của doc&#41;,
`@__score`/`@__combined_score` &#40;điểm đã fuse&#41;, `@vector_distance`
&#40;khoảng cách thô từ nhánh VSIM, nếu doc có mặt ở nhánh đó&#41;.

## 3. Ví dụ thật trên container

Smoke test chạy trực tiếp trên container hôm nay &#40;2026-07-11&#41;.

Tạo index và 3 doc:

```redis
FT.CREATE smoke:idx ON HASH PREFIX 1 smoke:
  SCHEMA summary TEXT vec VECTOR HNSW 6 TYPE FLOAT32 DIM 4 DISTANCE_METRIC COSINE
```

| Key | summary | vec | Ghi chú |
| --- | --- | --- | --- |
| `smoke:1` | "redis storage layer" | `[1, 0, 0, 0]` | khớp cả text lẫn vector |
| `smoke:2` | "vector database engine" | `[0.99, 0.14, 0, 0]` | chỉ khớp vector |
| `smoke:3` | "storage cabinet recipe" | `[0, 0, 0, 1]` | chỉ khớp text, vector ở xa |

Truy vấn — tìm `"storage"` bằng BM25, đồng thời KNN k=2 tới
`qv = [1, 0, 0, 0]`:

```redis
FT.HYBRID smoke:idx
  SEARCH "storage" YIELD_SCORE_AS text_score
  VSIM @vec $qv KNN 2 K 2 YIELD_SCORE_AS vector_score
  COMBINE RRF 2 CONSTANT 60
  LOAD 4 @__key @text_score @vector_score @summary
  PARAMS 2 qv <blob của [1,0,0,0]>
```

Kết quả thật &#40;RESP3 map, `total_results=3`&#41;:

| Thứ hạng | Key | text_score | vector_score |
| --- | --- | --- | --- |
| 1 | `smoke:1` | 0.94 | 1.0 |
| 2 | `smoke:2` | — vắng mặt | 0.995 |
| 3 | `smoke:3` | 0.94 | — vắng mặt |

`vector_score` là độ tương đồng đã chuẩn hoá:
`vector_score = 1 - cosine_distance / 2`.

```mermaid
flowchart LR
    subgraph SEARCH_BM25["Nhánh SEARCH – BM25 trên 'storage'"]
        SR1["hạng 1: smoke:1<br/>text_score=0.94"]
        SR2["hạng 2: smoke:3<br/>text_score=0.94"]
    end

    subgraph VSIM_KNN["Nhánh VSIM – KNN k=2"]
        VR1["hạng 1: smoke:1<br/>vector_score=1.0"]
        VR2["hạng 2: smoke:2<br/>vector_score=0.995"]
    end

    SR1 --> F["COMBINE RRF<br/>CONSTANT=60"]
    SR2 --> F
    VR1 --> F
    VR2 --> F

    F --> O1["1. smoke:1 – có mặt ở CẢ HAI nhánh<br/>fused = 1/61 + 1/61 ≈ 0.033"]
    F --> O2["2. smoke:2 – chỉ nhánh VSIM<br/>fused = 1/62 ≈ 0.016"]
    F --> O3["3. smoke:3 – chỉ nhánh SEARCH<br/>fused ≈ 0.016, thua ở tie-break nội bộ"]
```

`smoke:1` thắng rõ ràng vì nó là doc duy nhất được cả hai nhánh chấm điểm —
mỗi nhánh đóng góp `1/61` vào tổng, còn `smoke:2` và `smoke:3` chỉ có một
nhánh đóng góp `1/62` nên xấp xỉ ngang nhau, thứ tự giữa chúng do Redis
tie-break nội bộ &#40;không quan trọng bằng việc `smoke:1` bubble lên đầu&#41;.

> **Lưu ý quan trọng**: `smoke:3` khớp nhánh SEARCH nhưng **không có
> `vector_score`** — Redis chưa từng tính khoảng cách của nó tới query
> vector, vì nó không lọt vào top-K của nhánh VSIM. Nghĩa là: với những doc
> chỉ đến từ nhánh BM25, muốn áp ngưỡng chất lượng cosine tối thiểu thì phải
> **tự tính ở phía client**, Redis không tính hộ.

## 4. brain_search sẽ dùng nó ra sao

```mermaid
sequenceDiagram
    participant Agent as Agent / LLM
    participant MCP as MCP tool brain_search
    participant Eng as MemorySearchEngine
    participant Redis as Redis 8.8

    Agent->>MCP: brain_search query, filters
    MCP->>Eng: validated search request
    Eng->>Eng: embed query bằng Harrier<br/>prompt web_search_query<br/>vector 640 chiều, normalized
    Eng->>Redis: FT.HYBRID ab:idx:memory<br/>SEARCH = query + brain_id + deleted=0 + filter tuỳ chọn<br/>VSIM = @embedding KNN<br/>COMBINE RRF CONSTANT 60<br/>LOAD preview fields + embedding
    Redis-->>Eng: danh sách đã fuse<br/>vector_score có thể vắng mặt ở vài doc

    rect rgb(245, 235, 210)
        Eng->>Eng: cosine gate: với doc thiếu vector_score,<br/>tự tính dot query_vec, stored_embedding<br/>cả hai đã normalized nên dot = cosine
        Eng->>Eng: loại doc có cosine dưới SEARCH_MIN_COSINE=0.30<br/>SAU ĐÓ mới cắt còn top_k=20
    end

    Eng-->>MCP: preview list<br/>memory_id, topic, summary,<br/>timeline_day, importance, has_content
    MCP-->>Agent: kết quả, KHÔNG đổi TTL
```

So với thiết kế cũ &#40;Step 04 §6.4, hai lệnh `FT.SEARCH` + RRF thủ công
trong Python&#41;, giờ chỉ còn **một lệnh `FT.HYBRID`** làm cả KNN, BM25 và
RRF trong Redis. Phần Python co lại còn đúng cosine gate: vì doc chỉ khớp
BM25 không có `vector_score` &#40;mục 3&#41;, engine tự tính cosine cho những
doc đó từ `embedding` đã `LOAD` về, loại bỏ doc dưới `SEARCH_MIN_COSINE=0.30`,
**rồi mới** cắt còn `SEARCH_TOP_K=20` — đúng thứ tự "gate trước, limit sau"
đã chốt ở Step 04 quyết định 10 &#40;march7 fix B3&#41;: nếu limit trước, vài
slot top-20 có thể bị chiếm bởi rác BM25 chưa qua gate.

## 5. Thay đổi trong repo

| Hạng mục | Trước – 7.4 | Sau – 8.8 |
| --- | --- | --- |
| Image | `redis/redis-stack-server:7.4.0-v8` | `redis:8.8` |
| Khởi động container | `REDIS_ARGS` env, entrypoint riêng của Stack | `command` tường minh `redis-server --appendonly yes`; image chính thức không có entrypoint Stack, chạy dưới uid 999 |
| redis-py tối thiểu | không ràng buộc rõ | `>= 7.1`, repo dùng `8.0.1` |
| Số round trip search | 2 &#40;`FT.SEARCH` KNN + `FT.SEARCH` BM25&#41; | 1 &#40;`FT.HYBRID`&#41; |
| Code fusion phía client | tính rank, RRF, gate, limit | chỉ còn cosine gate + limit |
| Server tối thiểu cho `FT.HYBRID` | không hỗ trợ | `8.4.0`; repo chọn `8.8` để có các fix bug hybrid + `FT.PROFILE` |

## Nguồn

- https://redis.io/docs/latest/commands/ft.hybrid/
- https://redis.io/docs/latest/develop/whats-new/8-8/
- https://redis.io/blog/revamping-context-oriented-retrieval-with-hybrid-search-in-redis-84/
- https://github.com/redis/redis/blob/8.8/00-RELEASENOTES
