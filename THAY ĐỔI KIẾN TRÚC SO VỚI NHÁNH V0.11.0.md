# Thay đổi kiến trúc so với nhánh v0.11.0

## Phạm vi so sánh

Tài liệu này so sánh:

- Kiến trúc cũ: nhánh `origin/v0.11.0`, commit
  `a4be4499c8d2279e7bfb0bdab64c2ad1574131b4`.
- Kiến trúc hiện tại: working tree trên nhánh `v0.11.1_supwin`.

Working tree hiện tại là bản native Windows-first và dùng chung package với
Ubuntu. Đây là trạng thái code đang phát triển, không phải mô tả một tag release
đã được commit hoàn chỉnh.

## Kết luận tổng quan

Kiến trúc đã chuyển từ một service phụ thuộc Redis, container và framework
embedding cấp cao sang một Python package native tự chứa:

```text
v0.11.0
MCP host
  -> FastMCP
  -> MemoryService
  -> SentenceTransformers + Torch
  -> Redis 8.8 + Query Engine
  -> FT.HYBRID
  -> Docker Compose deployment
```

```text
Kiến trúc hiện tại
MCP host
  -> installed another-brain executable
  -> MCP SDK v2 MCPServer
  -> MemoryService
  -> tokenizers + ONNX Runtime CPU
  -> SQLite tables + FTS5
  -> NumPy exact cosine
  -> application-layer RRF
```

Thay đổi này không chỉ đổi storage backend. Composition root, package layout,
model lifecycle, retrieval pipeline, deployment, dependency graph và chiến
lược test đều đã được xây dựng lại.

## Bảng so sánh kiến trúc

| Thành phần | Nhánh v0.11.0 | Kiến trúc hiện tại |
| --- | --- | --- |
| Cách chạy | `python src/main.py` | Executable cài đặt `another-brain` |
| Package layout | Các module top-level trong `src/` | Package chuẩn `src/another_brain/` |
| MCP SDK | `FastMCP`, dependency `mcp>=1.2` | MCP SDK v2 `MCPServer`, `mcp>=2.0,<2.1` |
| Transport mặc định | Stdio hoặc HTTP qua runner riêng | Stdio mặc định; HTTP loopback tùy chọn |
| Storage | Redis 8.8 | SQLite là source of truth duy nhất |
| Full-text search | Redis Query Engine/BM25 | SQLite FTS5 |
| Vector search | Redis vector index | NumPy exact cosine trên FLOAT32 BLOB |
| Hybrid fusion | Một lệnh Redis `FT.HYBRID` | Hai branch độc lập, fusion tại application |
| Embedding runtime | SentenceTransformers + Torch | Raw ONNX Runtime CPU + tokenizers |
| Model selection | Registry/policy/profile có nhiều tùy chọn | Một Harrier q4 artifact được pin cố định |
| Model cache | Model cache abstraction và download policy | Installer staging + SHA-256 manifest |
| Data path | Redis URL và Redis volume | Native user-data directory qua platformdirs |
| Model path | Cache/volume cấu hình | Native user-cache directory qua platformdirs |
| Deployment | Dockerfile + Docker Compose | Wheel/Python executable native |
| Windows | Chủ yếu qua Docker Desktop/WSL | Chạy trực tiếp bằng Python/ONNX Runtime |
| Ubuntu | Docker hoặc source + Redis | Cùng wheel và lockfile với Windows |
| Audit | Redis audit keys/service riêng | Bảng `audit_events` trong SQLite |
| Migration | Redis index bootstrap | SQLite schema version/checksum bootstrap |
| Test layout | `tests/unit` + Redis integration | `tests/native`, không cần external service |
| CI | Ubuntu unit test | Windows + Ubuntu, Python 3.12/3.14, coverage gate |

## 1. Thay đổi package và entry point

### v0.11.0

Source code được import bằng cách thêm `src` vào Python path:

```text
src/
  app.py
  config.py
  main.py
  audit/
  memory/
  models/
  server/
  storage/
```

Không có console script trong `pyproject.toml`. Cách chạy chính là:

```text
python src/main.py serve
```

Các import như `from config import AppConfig` hoặc
`from memory.service import MemoryService` phụ thuộc trực tiếp vào source-tree
layout.

### Hiện tại

Code đã trở thành một package Python chuẩn:

```text
src/another_brain/
  cli.py
  app.py
  config.py
  errors.py
  service.py
  domain/
  embedding/
  storage/
  retrieval/
  mcp/
```

`pyproject.toml` khai báo entry point:

```text
another-brain = another_brain.cli:main
```

MCP host gọi executable đã cài, không cần biết vị trí repository hoặc tự cấu
hình Python path.

## 2. Thay đổi composition root

### v0.11.0

`src/app.py` chịu trách nhiệm khởi tạo nhiều thành phần:

- Redis async client.
- Redis key builder.
- Redis index manager.
- Redis repository.
- Retention policy.
- Audit service.
- Search engine.
- Model registry, model installer và runtime profile.
- SentenceTransformers embedding provider.
- FastMCP server và HTTP health route.

`build_service()` là async vì phải kết nối Redis và kiểm tra index trong quá
trình startup. Caller phải quản lý và đóng Redis client.

### Hiện tại

`src/another_brain/app.py` chỉ composition các thành phần native:

```text
AppConfig
  -> SQLiteRepository
  -> HybridRetriever
  -> OnnxEmbeddingProvider
  -> MemoryService
```

Không còn external connection cần giữ suốt vòng đời process. SQLite connection
được mở ngắn hạn theo operation và tự đóng qua context manager.

## 3. Thay đổi storage

### v0.11.0: Redis

Storage cũ gồm:

```text
src/storage/
  redis_keys.py
  redis_index.py
  redis_repository.py
  migrations.py
```

Đặc điểm:

- Redis hash là record store.
- Redis key TTL điều khiển retention vật lý.
- RediSearch/Query Engine quản lý index.
- Redis TAG syntax được escape thủ công.
- RESP2/RESP3 replies cần mapper và decoder riêng.
- Startup phải kiểm tra Redis reachable và index contract.
- Persistence phụ thuộc Redis configuration/volume.

### Hiện tại: SQLite

Storage mới gồm:

```text
src/another_brain/storage/
  schema.py
  connection.py
  repository.py
```

Đặc điểm:

- Bảng `memories` là source of truth.
- Embedding được lưu dưới dạng FLOAT32 little-endian BLOB 2.560 byte.
- `expires_at` được persist và lọc trong mọi live query.
- Soft deletion dùng `deleted_at`.
- Audit nằm trong bảng `audit_events`.
- Embedding contract nằm trong bảng `embedding_profiles`.
- FTS5 external-content table được đồng bộ bằng trigger.
- Schema dùng `user_version`, migration checksum và file lock.
- WAL, foreign keys, synchronous NORMAL, busy timeout 5 giây và page size
  16 KiB được cấu hình trong bootstrap.

Không còn Redis URL, Redis key namespace, Redis index manager hoặc Redis
process.

## 4. Thay đổi retrieval

### v0.11.0

Search cũ dựa vào Redis `FT.HYBRID`:

```text
query
  -> Redis Query Engine
     -> BM25
     -> vector index
     -> hybrid result
  -> application mapping/cosine checks
```

Lexical, vector và filter syntax được đóng gói thành một Redis query. Storage
backend đồng thời thực hiện search engine responsibility.

### Hiện tại

Retrieval được tách khỏi repository:

```text
query
  ├── safe FTS5 query -> lexical candidates
  └── query embedding -> NumPy exact cosine candidates
               │
               ▼
        application-layer RRF
               │
               ▼
          top 5 previews
```

Các contract chính:

- FTS5 weights topic:summary:content là `5:3:1`.
- Mỗi branch lấy tối đa 50 candidates.
- Vector cosine floor là `0.30`.
- Cosine floor không loại lexical-only result.
- RRF sử dụng `k=60`.
- Final result cố định tối đa 5 entries.
- Tie-breaking ổn định theo memory id.
- Query không có safe lexical term sử dụng vector branch.

Thay đổi quan trọng nhất là storage không còn chịu trách nhiệm fusion.

## 5. Thay đổi embedding

### v0.11.0

Embedding cũ dùng:

- `sentence-transformers`.
- `torch` CPU wheels.
- Model registry.
- Download policy: manual/lazy/on-start/disabled.
- Runtime profile cho weight/output/vector precision.
- SentenceTransformers prompt name.
- Summary là document embedding input chính.

Model có thể được resolve qua nhiều abstraction và cấu hình provider.

### Hiện tại

Embedding mới dùng:

- `tokenizers` để tokenize trực tiếp.
- `onnxruntime` CPU để chạy graph.
- Harrier q4 revision cố định.
- Năm model/tokenizer artifacts có SHA-256 cố định.
- Output contract FLOAT32 `[batch,640]`, finite và unit-normalized.
- Document payload là:

```text
humanized topic
summary
```

- Query payload dùng prompt cố định riêng.
- Content không được embed; content chỉ tham gia FTS5.
- Token budgets được kiểm tra trước khi infer.

Kiến trúc mới loại bỏ Torch, SentenceTransformers, model provider selector và
hardware-dependent model variant selection.

## 6. Thay đổi MCP server

### v0.11.0

- Dùng `mcp.server.fastmcp.FastMCP`.
- Tool registration nằm trong `src/server/tools.py`.
- Stdio và HTTP có runner riêng trong `src/server/`.
- HTTP health là một custom Starlette route.
- Redis client phải được đóng khi server shutdown.

### Hiện tại

- Dùng `mcp.server.MCPServer` của MCP SDK v2.
- Tool registration nằm trong `src/another_brain/mcp/server.py`.
- In-memory MCP client được dùng trực tiếp trong integration tests.
- Stdio là transport mặc định của executable.
- HTTP Streamable được bật bằng `another-brain serve --http`.
- HTTP host bị giới hạn ở numeric loopback.
- Không còn external storage client cần lifecycle shutdown.

Tên tám public tools vẫn được giữ ổn định:

```text
brain_remember
brain_search
brain_recent
brain_get
brain_reinforce
brain_forget
brain_health
brain_audit
```

## 7. Thay đổi domain model

Các khái niệm chính vẫn được giữ:

- Shared `brain_id`.
- `agent_id` làm provenance.
- Scope `user | project | global`.
- Append-only diary entries.
- Importance-based retention.
- Reinforce là normal TTL renewal duy nhất.
- Forget là soft delete.
- Search/recent trả preview, get trả full detail.

Thay đổi cách biểu diễn:

- v0.11.0 dùng nhiều value object như identity/vector/search score và mapping
  Redis-specific.
- Hiện tại dùng immutable dataclasses gần với SQLite schema.
- Timestamp chuyển sang integer milliseconds nhất quán trong domain/storage.
- `expires_at` là correctness field bền vững thay vì phụ thuộc Redis key expiry.
- Embedding profile được lưu rõ trong database.

## 8. Thay đổi audit và retention

### v0.11.0

- Audit là một service riêng lưu các Redis keys theo timeline day.
- Audit retention dựa vào expiry của Redis keys.
- Memory retention kết hợp dữ liệu record và TTL của Redis.

### Hiện tại

- Audit được repository ghi vào bảng `audit_events`.
- Audit text không chứa topic/summary/content/metadata.
- Audit cleanup dùng cutoff timestamp.
- Memory row luôn có `expires_at` tuyệt đối.
- Live queries luôn lọc `expires_at > now` và `deleted_at IS NULL`.
- Forget đặt expiry thành giá trị nhỏ hơn giữa expiry hiện tại và grace expiry,
  nên không kéo dài tuổi thọ memory.

## 9. Thay đổi configuration

### Biến cấu hình đã loại khỏi runtime

Những nhóm cấu hình v0.11.0 không còn cần thiết:

- Redis URL/key prefix/index mode/vector dtype/distance metric.
- Embedding provider selector.
- Model download policy và network policy.
- Torch/SentenceTransformers precision profile.
- Redis-specific search/index settings.

### Biến cấu hình hiện tại

Các nhóm cấu hình chính còn lại:

```text
BRAIN_ID
ANOTHER_BRAIN_DATA_DIR
ANOTHER_BRAIN_DATABASE
ANOTHER_BRAIN_MODEL_DIR
TIMELINE_TIMEZONE
MCP_HTTP_HOST
MCP_HTTP_PORT
AUDIT_RETENTION_DAYS
FORGET_GRACE_SECONDS
```

`platformdirs` quyết định default data/cache path riêng cho Windows và Ubuntu.
Windows cài thêm `tzdata` để đảm bảo IANA timezone hoạt động.

## 10. Thay đổi dependency graph

### Runtime dependency chính của v0.11.0

```text
mcp >= 1.2
redis >= 7.1
sentence-transformers >= 5.0 (optional local)
torch >= 2.9 (optional local)
```

### Runtime dependency hiện tại

```text
filelock
mcp >= 2.0, < 2.1
numpy
onnxruntime
platformdirs
tokenizers
tzdata (Windows)
```

Dependency graph mới loại bỏ Redis client, Torch, SentenceTransformers,
Transformers và các dependency ML nặng liên quan.

## 11. Thay đổi deployment

### v0.11.0

Deployment chính gồm:

```text
docker/Dockerfile
docker/docker-compose.yml
scripts/install.sh
scripts/harnesses/*.sh
```

Compose chạy hai services:

- Redis server.
- Another Brain MCP server.

Model và Redis data được giữ bằng các volume riêng.

### Hiện tại

Deployment native gồm:

```text
scripts/install.ps1
scripts/install.sh
scripts/connect.ps1
scripts/connect.sh
```

Quy trình:

```text
uv tool install
  -> installed another-brain executable
  -> optional another-brain model pull
  -> another-brain doctor
  -> MCP host launches executable over stdio
```

Không còn Dockerfile, compose file, Redis service hoặc container volume.

## 12. Thay đổi test architecture

### v0.11.0

```text
tests/unit/
tests/integration/
```

- Nhiều unit tests mock Redis/model behavior.
- Integration tests cần Redis hoặc có khả năng skip khi service không tồn tại.
- CI chỉ chạy Ubuntu unit tests.
- Không có executable wheel/process transport gate bắt buộc.

### Hiện tại

```text
tests/native/
  conftest.py
  test_domain_complete.py
  test_config_complete.py
  test_embedding_complete.py
  test_storage_complete.py
  test_retrieval_complete.py
  test_service_complete.py
  test_mcp_complete.py
  test_cli_complete.py
  test_transport_complete.py
  ...các regression tests ban đầu
```

Các lớp test hiện tại bao phủ:

- Domain và config validation.
- Model manifest/installer/provider.
- SQLite schema, FTS5, restart, lifecycle và concurrent writers.
- Retrieval/RRF/cosine floor/tiếng Việt.
- Service lifecycle đầy đủ.
- Đủ tám MCP calls và tool schemas.
- CLI commands.
- Subprocess stdio và HTTP thật.
- Optional slow gate với ONNX artifacts thật.

Kết quả suite chuẩn hiện tại:

```text
113 passed
15 xfailed
1 skipped
branch coverage 91.31%
```

CI chạy Windows và Ubuntu trên Python 3.12/3.14, đồng thời yêu cầu coverage tối
thiểu 90%.

## 13. Ánh xạ module cũ sang module mới

| Module v0.11.0 | Module hiện tại | Ghi chú |
| --- | --- | --- |
| `src/main.py` | `src/another_brain/cli.py` | Chuyển thành installed console entry point |
| `src/app.py` | `src/another_brain/app.py` | Composition root được thu gọn |
| `src/config.py` | `src/another_brain/config.py` | Loại Redis/model-provider config |
| `src/errors.py` | `src/another_brain/errors.py` | Error taxonomy native |
| `src/memory/models.py` | `src/another_brain/domain/models.py` | Domain dataclasses gần SQLite schema |
| `src/memory/retention.py` | `src/another_brain/domain/retention.py` | TTL policy được giữ |
| `src/memory/service.py` | `src/another_brain/service.py` | Service không phụ thuộc Redis interfaces |
| `src/memory/embeddings.py` | `src/another_brain/embedding/provider.py` | SentenceTransformers đổi thành raw ONNX |
| `src/models/*` | `src/another_brain/embedding/manifest.py` và `installer.py` | Registry/policy/profile được thay bằng pinned manifest |
| `src/storage/redis_repository.py` | `src/another_brain/storage/repository.py` | Redis repository đổi thành SQLite repository |
| `src/storage/redis_index.py` | `src/another_brain/storage/schema.py` | Redis index contract đổi thành schema/FTS5 |
| `src/storage/redis_keys.py` | Không còn | SQLite không cần key builder |
| `src/memory/search.py` | `src/another_brain/retrieval/service.py` | Retrieval tách lexical/vector branches |
| Không có module riêng | `src/another_brain/retrieval/fusion.py` | Application-layer RRF mới |
| `src/audit/*` | `storage/repository.py` + `schema.py` | Audit chuyển thành SQLite table |
| `src/server/tools.py` | `src/another_brain/mcp/server.py` | FastMCP đổi thành MCPServer v2 |
| `src/server/stdio.py` | `cli.py` + MCP SDK runner | Không cần runner wrapper riêng |
| `src/server/http.py` | `cli.py` + MCP SDK runner | HTTP chỉ bật theo option |

## 14. Những contract được giữ ổn định

Dù thay đổi kiến trúc lớn, các contract sản phẩm sau được giữ:

- Một brain được chia sẻ giữa nhiều agent.
- Agent identity chỉ là provenance, không phải partition.
- Memory là diary entry append-only.
- Scope user/project/global.
- Importance quyết định TTL.
- Reads không renew.
- Reinforce renews sau khi memory được sử dụng thành công.
- Forget loại memory khỏi live reads ngay lập tức.
- Search/recent trả preview.
- Get trả content và metadata đầy đủ.
- Audit không chứa memory text.
- Tám tên MCP tools không thay đổi.

## 15. Trade-off của kiến trúc mới

### Lợi ích

- Cài và chạy trực tiếp trên Windows.
- Ubuntu sử dụng cùng package, không duy trì implementation riêng.
- Không cần external database/service.
- Startup và vận hành đơn giản hơn.
- Dependency ML nhẹ hơn và có artifact pin/hash rõ ràng.
- Database có thể backup như một file SQLite.
- Retrieval behavior được kiểm soát và test tại application layer.
- Test suite chạy không cần network hoặc service ngoài.

### Chi phí và giới hạn hiện tại

- NumPy exact scan có chi phí tuyến tính theo số memory.
- SQLite multi-writer contention cần retry/mapping hoàn chỉnh hơn.
- Model download chưa resume khi kết nối bị ngắt.
- Installed model chưa được hash lại trong mỗi `model_ready()` call.
- Một số filter/limit validation chưa hoàn thiện.
- MCP v2 client provenance hiện còn fallback về `mcp-client` trong một số
  connection context.
- Migration dữ liệu từ Redis cũ sang SQLite chưa nằm trong runtime hiện tại.

Các giới hạn này đã có `xfail(strict=True)` tương ứng trong test suite khi phù
hợp, để không bị che giấu trong các lần phát triển tiếp theo.

## 16. Trạng thái tương thích Windows và Ubuntu

Kiến trúc hiện tại không duy trì hai code path storage/runtime riêng:

```text
Windows ─┐
         ├─ same wheel -> same domain/service/storage/retrieval/MCP code
Ubuntu ──┘
```

Khác biệt chỉ nằm ở:

- Native filesystem path do `platformdirs` chọn.
- Windows nhận thêm package `tzdata`.
- Script cài đặt/connector dùng PowerShell hoặc POSIX shell.
- Wheel binary dependency của ONNX Runtime được resolver chọn theo OS/Python.

Vì database và model cache nằm ở user directories riêng của từng hệ điều hành,
hai bản cài đặt không ghi đè dữ liệu của nhau.
